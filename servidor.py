#!/usr/bin/env python3
"""Web de guaguas de Fuerteventura (Guaguas FTV).

    python servidor.py          # http://127.0.0.1:5000
En producción:  gunicorn -w 2 servidor:app   (detrás de nginx con HTTPS)
Pruebas: GUAGUAS_DEBUG=1 permite ?hora=2026-09-24T10:05 en las consultas.
"""
import base64, hashlib, json, logging, math, os, time, jwt
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request

from comunidad_pg import Comunidad, Error, km_ll, mensaje

import guaguas as g
import postgresql_db as pg_db

try:
    import firebase_admin
    from firebase_admin import credentials, auth as firebase_auth, messaging
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False

if not hasattr(g, "nombre_destino"):
    raise SystemExit("guaguas.py es una versión antigua: sustitúyelo por el último y reinicia el servidor. \nLos archivos guaguas.py, servidor.py, importar_mapas.py y web/index.html deben ser de la misma entrega.")

TZ = ZoneInfo("Atlantic/Canary")
DEBUG = bool(os.environ.get("GUAGUAS_DEBUG"))
app = Flask(__name__, static_folder="web", static_url_path="")
HORARIOS, BASE = g.cargar()
_osm = g.DATOS / "paradas_osm.json"
OSM = [p for p in json.loads(_osm.read_text(encoding="utf-8")) if p["nombre"]] if _osm.exists() else []
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
COLOR = {num: g.COLORES[i % len(g.COLORES)] for i, num in enumerate(BASE)}

JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    if os.environ.get("RENDER"):
        raise SystemExit("Falta la variable de entorno JWT_SECRET (genera una con: python -c \"import secrets;print(secrets.token_hex(32))\")")
    JWT_SECRET = "solo-desarrollo-local"  # nunca en producción
JWT_ALG = "HS256"
JWT_EXP_HOURS = 24

FIREBASE_CRED_PATH = os.environ.get("FIREBASE_CRED_PATH", "/etc/secrets/guaguas-ftv1-0c96bbab863c.json")
_firebase_app = None
def init_firebase():
    global _firebase_app
    if not FIREBASE_AVAILABLE:
        return False
    if _firebase_app:
        return True
    try:
        cred = credentials.Certificate(FIREBASE_CRED_PATH)
        _firebase_app = firebase_admin.initialize_app(cred)
        return True
    except Exception as e:
        logging.getLogger("guaguas").error("Firebase init failed (FIREBASE_CRED_PATH=%s): %s", FIREBASE_CRED_PATH, e)
        return False

def create_internal_token(uid: str, email: str = None, display_name: str = None) -> str:
    payload = {
        "uid": uid,
        "email": email,
        "name": display_name,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXP_HOURS * 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def verify_internal_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise Error("token expirado", 401)
    except jwt.InvalidTokenError:
        raise Error("token inválido", 401)

def send_fcm_push(token: str, title: str, body: str, data: dict = None):
    """Devuelve (ok, motivo). motivo='token_invalido' si FCM dice que el token ya no existe."""
    if not FIREBASE_AVAILABLE:
        return False, "firebase_no_instalado"
    if not init_firebase():
        return False, "firebase_no_configurado"
    try:
        messaging.send(messaging.Message(notification=messaging.Notification(title=title, body=body),
                                         data=data or {}, token=token))
        return True, None
    except (messaging.UnregisteredError, messaging.SenderIdMismatchError):
        return False, "token_invalido"
    except Exception as e:
        app.logger.warning("FCM send failed: %s", e)
        return False, "fallo_fcm"


def agrupar(radio_km=0.06, BASE=None):
    """Une las paradas (de cualquier línea) a menos de ~60 m: una parada física con sus dos sentidos
    es un único punto del mapa. Se conservan los ids de cada parada concreta de cada línea."""
    if BASE is None:
        BASE = globals().get('BASE', {})
    pts = sorted((p["lat"], p["lon"], p["n"], p["id"], num) for num, l in BASE.items() for p in l["paradas"])
    grupos = []
    for lat, lon, n, pid, num in pts:
        for gr in grupos:
            if g.km({"lat": lat, "lon": lon}, gr) <= radio_km:
                gr["c"] += 1
                gr["lat"] += (lat - gr["lat"]) / gr["c"]
                gr["lon"] += (lon - gr["lon"]) / gr["c"]
                gr["ids"].add(pid); gr["nombres"].append(n); gr["lineas"].add(num)
                break
        else:
            grupos.append(dict(lat=lat, lon=lon, c=1, ids={pid}, nombres=[n], lineas={num}))
    res = {}
    for gr in grupos:
        gr["nombre"] = Counter(gr["nombres"]).most_common(1)[0][0]
    rep = Counter(gr["nombre"] for gr in grupos)
    for gr in grupos:  # nombres repetidos: se distinguen por las líneas que pasan (y por nº si aún coinciden)
        if rep[gr["nombre"]] > 1:
            gr["nombre"] += " · L" + ", L".join(sorted(gr["lineas"]))
    vistos = Counter()
    for gr in grupos:
        vistos[gr["nombre"]] += 1
        if vistos[gr["nombre"]] > 1:
            gr["nombre"] += f" ({vistos[gr['nombre']]})"
        pid = "p" + hashlib.md5(f"{gr['lat']:.4f},{gr['lon']:.4f}".encode()).hexdigest()[:8]  # estable entre arranques
        while pid in res:
            pid += "x"
        gr["id"] = pid
        res[pid] = gr
    return res


PARADAS = agrupar()
_HOPS = int(os.environ.get("GUAGUAS_PROXY_HOPS", os.environ.get("GUAGUAS_PROXY", "0")) or 0)
if _HOPS:  # proxy propio (nginx...): X-Forwarded-For con N saltos de confianza
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_HOPS)
# Render va detrás de Cloudflare, que fija CF-Connecting-IP.
TRUST_CF = bool(os.environ.get("TRUST_CF_CONNECTING_IP") or os.environ.get("RENDER"))


def client_ip():
    """IP real del cliente. Sin esto todos los usuarios comparten la IP del proxy de Render."""
    ip = (request.headers.get("CF-Connecting-IP") or "").strip() if TRUST_CF else ""
    ip = ip or request.remote_addr or "?"
    if os.environ.get("GUAGUAS_LOG_IP"):  # diagnóstico temporal: ver qué cabeceras llegan de verdad
        app.logger.warning("client_ip=%s remote_addr=%s CF=%s XFF=%s", ip, request.remote_addr,
                           request.headers.get("CF-Connecting-IP"), request.headers.get("X-Forwarded-For"))
    return ip


def ahora_ts():
    """Segundos epoch 'de ahora' (en pruebas, ?hora=... con GUAGUAS_DEBUG=1)."""
    try:
        if DEBUG and request.args.get("hora"):
            return datetime.fromisoformat(request.args["hora"]).replace(tzinfo=TZ).timestamp()
    except RuntimeError:
        pass
    return time.time()


def a_ts(dt):
    return dt.replace(tzinfo=TZ).timestamp()


COM = Comunidad(
    {pid: set(gr["ids"]) for pid, gr in PARADAS.items()},
    {pid: (gr["lat"], gr["lon"]) for pid, gr in PARADAS.items()},
    TZ, reloj=ahora_ts)

@app.errorhandler(Error)
def error_comunidad(e):
    resp = jsonify(ok=False, error=str(e))
    if e.codigo == 429:
        resp.headers["Retry-After"] = "3600"
    return resp, e.codigo


@app.errorhandler(500)
def error_500(e):
    app.logger.exception("Error 500 en %s %s", request.method, request.path)  # detalle al log, no al cliente
    return jsonify(ok=False, error="Error interno del servidor"), 500


@app.before_request
def _mantenimiento():
    if request.path.startswith("/api/") and request.method != "GET":
        try:
            COM.mantenimiento(ahora_ts())
        except Exception:
            app.logger.exception("mantenimiento fallido")


def autenticar():
    h = request.headers
    try:
        dev, ts, nonce, firma = h["X-Dispositivo"], h["X-Ts"], h["X-Nonce"], h["X-Firma"]
    except KeyError:
        raise Error("faltan cabeceras de autenticación", 401)
    return COM.verificar(dev, ts, nonce, firma, mensaje(request.method, request.path, ts, nonce, request.get_data(as_text=True)), ahora_ts())


def lugares():
    """Núcleos (Las Playitas, Antigua…) para el buscador, aunque ninguna parada se llame así."""
    acc = {}
    for l in BASE.values():
        for p in l["esqueleto"]:
            acc.setdefault(p["n"], (p["lat"], p["lon"]))
    return [dict(n=n, lat=la, lon=lo) for n, (la, lo) in sorted(acc.items())]


_cache = {}


def ahora_local():
    if DEBUG and request.args.get("hora"):
        return datetime.fromisoformat(request.args["hora"])
    return datetime.now(TZ).replace(tzinfo=None)


def tablero(clave, objetivo, ahora, por_destino=6, modo="dinamico"):
    k = (clave, ahora.strftime("%Y%m%d%H%M"), modo)  # las respuestas cambian como mucho cada minuto
    if k in _cache:
        return _cache[k]
    if len(_cache) > 800:
        _cache.clear()
    grupos = {}
    for r in g.proximas(objetivo, ahora, HORARIOS, BASE, com=COM, gracia=1, modo=modo):
        lista = grupos.setdefault(r["destino"], [])
        if sum(1 for x in lista if x["linea"] == r["linea"]) < 3 and len(lista) < por_destino:
            dif = (r["llegada"] - ahora).total_seconds() / 60
            lista.append(dict(linea=r["linea"], texto=g.texto_espera(r, ahora), hora=f"{r['llegada']:%H:%M}", minutos=round(dif),
                           prec=r["prec"], cal=r["cal"], nota=r["nota"], viaje=r["viaje"], n=r["n"],
                           subir=-12 <= dif <= min(20, 12 + 0.25 * r["off"])))  # solo se puede avisar cuando está pasando
    orden = sorted(grupos.items(), key=lambda kv: kv[1][0]["minutos"])
    _cache[k] = dict(ahora=f"{ahora:%H:%M}", dia=f"{DIAS[ahora.weekday()]} {ahora:%d/%m}",
                 grupos=[dict(destino=d, salidas=s) for d, s in orden])
    return _cache[k]


@app.post("/api/registro")
def api_registro():
    """Alta de un dispositivo: envía su clave pública firmando la petición con la clave privada (prueba de posesión)."""
    pub = (request.get_json(silent=True) or {}).get("pub", "")
    try:
        raw = base64.b64decode(pub, validate=True)
        dev = Comunidad.id_de(raw)
    except Exception:
        raise Error("clave pública no válida")
    h = request.headers
    try:
        ts, nonce, firma = h["X-Ts"], h["X-Nonce"], h["X-Firma"]
    except KeyError:
        raise Error("faltan cabeceras de autenticación", 401)
    ahora = ahora_ts()
    COM.verificar(dev, ts, nonce, firma, mensaje("POST", request.path, ts, nonce, request.get_data(as_text=True)), ahora, pub_raw=raw)
    COM.registrar(pub, client_ip(), ahora)
    return jsonify(ok=True, dispositivo=dev, perfil=COM.perfil(dev, ahora))


@app.get("/api/yo")
def api_yo():
    dev = autenticar()
    return jsonify(ok=True, dispositivo=dev, perfil=COM.perfil(dev, ahora_ts()))


@app.post("/api/subida")
def api_subida():
    """'Me he subido a esta guagua en esta parada'. Se valida, pero solo cuenta cuando coinciden varias personas."""
    dev = autenticar()
    d = request.get_json(silent=True) or {}
    gr = PARADAS.get(d.get("parada", ""))
    if not gr:
        raise Error("parada desconocida", 404)
    ahora = ahora_ts()
    pred = g.prediccion_viaje(str(d.get("viaje", "")), gr["ids"], HORARIOS, BASE, COM)
    if not pred:
        raise Error("esa guagua no existe o no pasa por esta parada")
    if abs(ahora - a_ts(pred["inicio"])) > 30 * 3600:
        raise Error("ese viaje no es de hoy")
    dist = None
    try:  # ubicación opcional: si viene y es precisa, se comprueba que estás en la parada
        lat, lon, acc = float(d["lat"]), float(d["lon"]), float(d.get("acc", 50))
        if acc <= 200:
            dist = max(0.0, km_ll((lat, lon), (gr["lat"], gr["lon"])) * 1000 - min(acc, 100))
    except (KeyError, TypeError, ValueError):
        pass
    llegada_ts = int(a_ts(pred["llegada"]))
    res = COM.enviar_subida(dev, d["viaje"], d["parada"], dict(linea=pred["linea"], sentido=pred["sentido"], variante=pred["variante"],
                            llegada_ts=llegada_ts, pred_ts=llegada_ts, inicio_ts=int(a_ts(pred["inicio"])), off=pred["off"]), dist, int(ahora))
    _cache.clear()
    msg = ("¡Confirmado! Ya coincidís varias personas y la hora se ha actualizado para los demás." if res["estado"] == "confirmada"
           else "Anotado. Cuando otras personas confirmen esta guagua, la hora se actualizará."
           + ("" if res["gps"] else " (Con la ubicación activada tu aviso pesa más.)"))
    return jsonify(ok=True, estado=res["estado"], mensaje=msg, perfil=COM.perfil(dev, ahora))


@app.get("/")
def inicio():
    return app.send_static_file("index.html")


@app.get("/api/paradas")
def api_paradas():
    return jsonify([dict(id=p["id"], nombre=p["nombre"], lat=round(p["lat"], 5), lon=round(p["lon"], 5), lineas=sorted(p["lineas"]))
                     for p in PARADAS.values()])


@app.get("/api/lineas")
def api_lineas():
    return jsonify([dict(num=n, nombre=HORARIOS[n]["nombre"], color=COLOR[n], calibrada="duracion_min" in l, real=bool(l.get("real")),
                         trazado=(l["trazado"][0] if (l.get("trazado") and len(l["trazado"]) == 1 and isinstance(l["trazado"][0], list) and l["trazado"][0] and isinstance(l["trazado"][0][0], list)) else (l["trazado"] if "trazado" in l else [[p["lat"], p["lon"]] for p in l["paradas"] if "opt" not in p])))
                     for n, l in BASE.items()])


@app.get("/api/lugares")
def api_lugares():
    return jsonify(lugares())


@app.get("/api/osm")
def api_osm():
    return jsonify([dict(n=p["nombre"], lat=p["lat"], lon=p["lon"]) for p in OSM])


def _modo():
    m = request.args.get("modo", "dinamico")
    return m if m in ("dinamico", "estatico") else "dinamico"


@app.get("/api/parada/<pid>")
def api_parada(pid):
    gr = PARADAS.get(pid)
    if not gr:
        raise Error("parada desconocida", 404)
    modo = _modo()
    d = dict(tablero(pid, dict(nombre=gr["nombre"], ids=gr["ids"]), ahora_local(), modo=modo))
    d["parada"] = dict(id=pid, nombre=gr["nombre"], lineas=sorted(gr["lineas"]))
    return jsonify(d)


@app.get("/api/debug/pool")
def api_debug_pool():
    if not DEBUG:
        raise Error("no encontrado", 404)
    import postgresql_db as pg
    return jsonify(
        use_postgres=pg.USE_POSTGRES,
        pool_min=pg.POOL.minconn if pg.POOL else None,
        pool_max=pg.POOL.maxconn if pg.POOL else None,
        pool_size=len(pg.POOL._pool) if pg.POOL and hasattr(pg.POOL, '_pool') else None,
        pool_used=len(pg.POOL._used) if pg.POOL and hasattr(pg.POOL, '_used') else None,
        pool_rused=len(pg.POOL._rused) if pg.POOL and hasattr(pg.POOL, '_rused') else None,
    )


@app.get("/api/debug/version")
def api_debug_version():
    if not DEBUG:
        raise Error("no encontrado", 404)
    import subprocess, os
    try:
        # Use current working directory or __file__ location
        cwd = os.path.dirname(os.path.abspath(__file__))
        git_hash = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=cwd).decode().strip()
    except Exception:
        git_hash = "unknown"
    return jsonify(git_hash=git_hash, deploy_time="2026-11-21")


@app.get("/api/cerca")
def api_cerca():
    if "lat" not in request.args or "lon" not in request.args:
        raise Error("faltan lat/lon", 400)
    try:
        lat, lon = float(request.args["lat"]), float(request.args["lon"])
    except ValueError:
        raise Error("lat/lon no son números", 400)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise Error("lat/lon fuera de rango", 400)
    nombre = request.args.get("nombre", "Parada")[:80]
    modo = _modo()
    d = dict(tablero(("osm", round(lat, 5), round(lon, 5)), dict(nombre=nombre, lat=lat, lon=lon), ahora_local(), modo=modo))
    d["parada"] = dict(id="osm", nombre=nombre, lineas=[], aprox=True)
    return jsonify(d)


@app.post("/api/firebase-token")
def api_firebase_token():
    if not FIREBASE_AVAILABLE:
        raise Error("firebase-admin no instalado", 500)
    if not init_firebase():
        raise Error("Firebase no configurado (falta FIREBASE_CRED_PATH)", 500)
    d = request.get_json(silent=True) or {}
    id_token = d.get("id_token")
    if not id_token:
        raise Error("falta id_token", 400)
    try:
        decoded = firebase_auth.verify_id_token(id_token)
    except Exception as e:
        app.logger.info("firebase id_token rechazado: %s", e)
        raise Error("token de Firebase no válido", 401)
    uid = decoded.get("uid")
    email = decoded.get("email")
    name = decoded.get("name")
    internal_token = create_internal_token(uid, email, name)
    return jsonify(ok=True, token=internal_token, uid=uid, email=email, name=name)


@app.post("/api/register-fcm-token")
def api_register_fcm_token():
    # Verify device signature (X-Dispositivo, X-Ts, X-Nonce, X-Firma)
    dev = autenticar()
    d = request.get_json(silent=True) or {}
    fcm_token = d.get("fcm_token")
    if not fcm_token:
        raise Error("falta fcm_token", 400)
    if not isinstance(fcm_token, str) or not (20 <= len(fcm_token) <= 4096):
        raise Error("fcm_token no válido", 400)
    COM.guardar_fcm_token(dev, fcm_token)
    return jsonify(ok=True)


@app.post("/api/test-push")
def api_test_push():
    # Verify device signature
    dev = autenticar()
    fcm_token = COM.obtener_fcm_token(dev)
    if not fcm_token:
        raise Error("no hay token FCM registrado", 400)
    ok, motivo = send_fcm_push(fcm_token, "Guaguas FTV", "Notificación de prueba desde el backend")
    if motivo == "token_invalido":
        COM.borrar_fcm_token(dev)
    return jsonify(ok=ok, motivo=motivo), (200 if ok else 502)


# ── Proxy de Firebase Auth (/__/auth/*): permite usar authDomain = este dominio ──
# Requiere añadir https://<tu-dominio>/__/auth/handler como URI de redirección autorizada en el cliente OAuth de Google.
FIREBASE_AUTH_HOST = os.environ.get("FIREBASE_AUTH_HOST", "guaguas-ftv1.firebaseapp.com")


@app.route("/__/auth/<path:resto>", methods=["GET", "POST"])
@app.route("/__/firebase/<path:resto>", methods=["GET"])
def proxy_firebase_auth(resto):
    import requests as rq
    from flask import Response
    destino = f"https://{FIREBASE_AUTH_HOST}{request.path}"
    try:
        r = rq.request(request.method, destino, params=request.args, data=request.get_data() or None,
                       headers={"Content-Type": request.headers.get("Content-Type", ""),
                                "Accept-Language": request.headers.get("Accept-Language", "")},
                       timeout=10, allow_redirects=False)
    except rq.RequestException as e:
        app.logger.warning("proxy firebase auth falló: %s", e)
        return jsonify(ok=False, error="no se pudo contactar con Firebase Auth"), 502
    permitidas = ("Content-Type", "Location", "Content-Language")
    resp = Response(r.content, status=r.status_code, headers={k: v for k, v in r.headers.items() if k in permitidas})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/health")
def health():
    return jsonify(ok=True, service="guaguas-ftv", db="postgresql" if pg_db.USE_POSTGRES else "sqlite")


if __name__ == "__main__":
    print(f"{len(PARADAS)} paradas · {len(OSM)} paradas OSM · http://127.0.0.1:{os.environ.get('PORT', 5000)}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=DEBUG)