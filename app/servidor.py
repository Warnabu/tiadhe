#!/usr/bin/env python3
"""Web de guaguas de Fuerteventura (Guaguas FTV).

    python servidor.py          # http://127.0.0.1:5000  (servidor.py de la raíz es el punto de entrada)
En producción:  gunicorn -w 2 servidor:app   (detrás de nginx con HTTPS)
Pruebas: GUAGUAS_DEBUG=1 permite ?hora=2026-09-24T10:05 en las consultas.
"""
import base64, hashlib, json, logging, math, os, time, jwt
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request

from .comunidad import Comunidad, Error, km_ll, mensaje

from . import bandas
from .planificador import Caminos, Planificador
from .seguridad import ip_real, proteger
from . import guaguas as g
from . import db as pg_db

try:
    import firebase_admin
    from firebase_admin import credentials, auth as firebase_auth, messaging
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False

if not hasattr(g, "nombre_destino"):
    raise SystemExit("guaguas.py es una versión antigua: sustitúyelo por el último y reinicia el servidor. \nLos archivos guaguas.py, servidor.py, importar_mapas.py y web/index.html deben ser de la misma entrega.")

TZ = ZoneInfo("Atlantic/Canary")
# Modo pruebas (simular la hora con ?hora=, endpoints /api/debug/*). Nunca en producción, aunque se ponga la variable.
DEBUG = bool(os.environ.get("GUAGUAS_DEBUG")) and not os.environ.get("RENDER")
RAIZ = Path(__file__).resolve().parent.parent
app = Flask(__name__, static_folder=str(RAIZ / "web"), static_url_path="")
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


def agrupar(radio_km=0.015, BASE=None, HORARIOS=None):
    """Une en un punto del mapa las paradas (de cualquier línea) que están en el mismo sitio (≤ 15 m; ≤ 60 m si no se
    sabe su sentido), y las del mismo
    nombre a ≤ 200 m (marquesinas de una estación) si no son de sentidos contrarios. Las paradas de una acera y de la
    de enfrente quedan separadas: cada una muestra su sentido. Se conservan los ids de cada parada de cada línea."""
    if BASE is None:
        BASE = globals().get('BASE', {})
    if HORARIOS is None:
        HORARIOS = globals().get('HORARIOS', {})
    pts = sorted((p["lat"], p["lon"], p["n"], p["id"], num, p.get("solo")) for num, l in BASE.items() for p in l["paradas"])
    grupos = []
    for lat, lon, n, pid, num, solo in pts:
        for gr in grupos:
            d = g.km({"lat": lat, "lon": lon}, gr)
            contrarios = solo and gr["solos"] and solo not in gr["solos"]
            sin_sentido = not solo and not gr["solos"]   # sin sentido conocido: como antes, una sola parada a ≤ 60 m
            if (d <= radio_km and not contrarios) or (sin_sentido and d <= 0.06) or (d <= 0.2 and not contrarios and g.norm(n) in {g.norm(x) for x in gr["nombres"]}
                                                     and (not solo) == (not gr["solos"])):
                gr["c"] += 1
                gr["lat"] += (lat - gr["lat"]) / gr["c"]
                gr["lon"] += (lon - gr["lon"]) / gr["c"]
                gr["ids"].add(pid); gr["nombres"].append(n); gr["lineas"].add(num)
                gr["hacia"].append(HORARIOS.get(num, {}).get("destino" if solo == "ida" else "origen") if solo else None)
                if solo:
                    gr["solos"].add(solo)
                break
        else:
            grupos.append(dict(lat=lat, lon=lon, c=1, ids={pid}, nombres=[n], lineas={num}, solos={solo} if solo else set(),
                               hacia=[HORARIOS.get(num, {}).get("destino" if solo == "ida" else "origen") if solo else None]))
    res = {}
    for gr in grupos:
        gr["nombre"] = Counter(gr["nombres"]).most_common(1)[0][0]
        hacia = [h for h in gr.pop("hacia") if h]
        if hacia and len(gr["solos"]) == 1:   # parada de un solo sentido: hacia dónde van sus guaguas
            dest = Counter(hacia).most_common(1)[0][0]
            if not set(g.norm(dest).split()) & set(g.norm(gr["nombre"]).split()):   # «Ayto. Tuineje → Tuineje», no
                gr["nombre"] += " → " + dest
        gr["solos"] = sorted(gr["solos"])
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
# Detrás del proxy de Render (o de otro, con TRUST_CF_CONNECTING_IP=1): la IP real va en las cabeceras.
DETRAS_DE_PROXY = bool(os.environ.get("TRUST_CF_CONNECTING_IP") or os.environ.get("RENDER")) and not _HOPS


def client_ip():
    """IP real del visitante (ver seguridad.ip_real). Sin esto todos comparten la IP del proxy de Render."""
    ip = ip_real(request, DETRAS_DE_PROXY)
    if os.environ.get("GUAGUAS_LOG_IP"):  # diagnóstico: ver qué cabeceras llegan de verdad
        app.logger.warning("client_ip=%s remote_addr=%s CF=%s XFF=%s", ip, request.remote_addr,
                           request.headers.get("CF-Connecting-IP"), request.headers.get("X-Forwarded-For"))
    return ip


proteger(app, client_ip)   # cabeceras de seguridad, límite por IP, tamaño máximo (app/seguridad.py)


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
            lista.append(dict(linea=r["linea"], texto=g.texto_espera(r, ahora), hora=f"{g.llegada_minima(r):%H:%M}", minutos=round(dif),
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
    n = pred.get("restantes", 0)
    paradas = f"{n} parada{'s' if n != 1 else ''} siguiente{'s' if n != 1 else ''}"
    msg = (f"¡Confirmado! Gracias a ti, quien espera en las {paradas} ya ve la hora real." if res["estado"] == "confirmada"
           else f"¡Gracias! En cuanto otra persona lo confirme, la hora se corregirá en las {paradas}."
           + ("" if res["gps"] else " (Con la ubicación activada tu aviso pesa más.)"))
    _estadisticas.clear()
    return jsonify(ok=True, estado=res["estado"], mensaje=msg, restantes=n, perfil=COM.perfil(dev, ahora))


_estadisticas = {}


@app.get("/api/estadisticas")
def api_estadisticas():
    """Actividad de hoy en toda la isla (avisos, personas, guaguas confirmadas). Cacheada 1 minuto."""
    ahora = ahora_ts()
    if not _estadisticas or ahora - _estadisticas["t"] > 60:
        _estadisticas.update(t=ahora, d=COM.estadisticas_hoy(ahora))
    return jsonify(ok=True, **_estadisticas["d"])


# ── Líneas y "cómo llegar" ──────────────────────────────────────────────────────────────────────────────────
_PARADA_DE = {i: pid for pid, gr in PARADAS.items() for i in gr["ids"]}   # id de parada de línea → parada del mapa
PLAN = Planificador(HORARIOS, BASE, _PARADA_DE, PARADAS)   # cómo llegar con transbordos
CAMINOS = Caminos(BASE, _PARADA_DE, PARADAS)                 # el camino elegido, dibujado en el mapa


def nombre_corto(pid):
    return PARADAS[pid]["nombre"].split(" · L")[0]


@app.get("/api/linea/<num>")
def api_linea(num):
    """Paradas de una línea en orden (sentido ida) y sus próximas salidas de cabecera en cada sentido."""
    if num not in BASE:
        raise Error("línea desconocida", 404)
    paradas = []
    for p in BASE[num]["paradas"]:
        pid = _PARADA_DE.get(p.get("id")) if "opt" not in p else None
        if pid and (not paradas or paradas[-1]["id"] != pid):
            paradas.append(dict(id=pid, nombre=nombre_corto(pid)))
    ahora = ahora_local()
    salidas = {}
    for sent in ("ida", "vuelta"):
        lista = []
        for dia_off in (0, 1):
            fecha = (ahora + timedelta(days=dia_off)).date()
            horas = sorted({x["h"] for x in HORARIOS[num]["salidas"]
                           if x["s"] == sent and g.tipo_dia(fecha) in x["d"].split(",") and (dia_off or x["h"] >= f"{ahora:%H:%M}")})
            lista += [h + (" (mañana)" if dia_off else "") for h in horas]
        salidas[sent] = lista[:5]
    h = HORARIOS[num]
    return jsonify(ok=True, num=num, nombre=h["nombre"], color=COLOR[num], origen=h.get("origen"), destino=h.get("destino"),
                   paradas=paradas, salidas=salidas)


RADIO_PARADA = 500    # desde una parada también se miran las de alrededor (a ≤ 500 m andando)
RADIO_PUNTO = 1500    # desde tu ubicación o un pueblo: todas las paradas a ≤ 1,5 km


def _extremo(valor, nombre):
    """Parada ('id') o punto ('@lat,lon') → (nombre, {parada: minutos andando}, punto o None)."""
    if valor in PARADAS:
        v = PARADAS[valor]
        return nombre_corto(valor), {valor: 0, **{k: m for k, m in PLAN.alrededor(v["lat"], v["lon"], RADIO_PARADA).items() if k != valor}}, None
    try:
        lat, lon = map(float, valor.lstrip("@").split(","))
        assert valor.startswith("@") and 27.6 < lat < 29.0 and -14.7 < lon < -13.3
    except (ValueError, AssertionError):
        raise Error("parada o punto desconocido", 404)
    paradas = PLAN.alrededor(lat, lon, RADIO_PUNTO, minimo=3)
    if not paradas:
        raise Error("no hay paradas de guagua cerca de ese punto", 404)
    nombre = "".join(ch for ch in (nombre or "") if ch.isprintable())[:60].strip() or "tu ubicación"
    return nombre, paradas, [round(lat, 5), round(lon, 5)]


@app.get("/api/ruta")
def api_ruta():
    """Cómo ir de 'desde' a 'hasta' (paradas o puntos '@lat,lon'): directas, con transbordos y andando lo justo."""
    o, d = request.args.get("desde", ""), request.args.get("hasta", "")
    if o == d:
        raise Error("el origen y el destino son el mismo sitio")
    no, origenes, po = _extremo(o, request.args.get("dn"))
    nd, destinos, pd = _extremo(d, request.args.get("hn"))
    ahora = ahora_local()
    opciones = []
    for it in PLAN.itinerarios(origenes, destinos, ahora):
        salir, llegar, nbus, _ = PLAN.resumen(it)
        buses = [p for p in it if p["tipo"] == "bus"]
        pasos, camino, prev = [], [], None
        for k, p in enumerate(it):
            if p["tipo"] == "pie":
                desde = p["desde"] or (o if po is None else None)
                hasta = p["hasta"] or (d if pd is None else None)
                pasos.append(dict(tipo="pie", desde=nombre_corto(desde) if desde else no, hasta=nombre_corto(hasta) if hasta else nd,
                                  minutos=p["minutos"], salir=f"{salir:%H:%M}" if k == 0 else None))
                camino.append(["pie", desde or f"@{po[0]},{po[1]}", hasta or f"@{pd[0]},{pd[1]}"])
                continue
            espera = round((p["sale"] - prev["llega"]).total_seconds() / 60) if prev else None
            pasos.append(dict(tipo="bus", linea=p["linea"], destino=p["destino"], desde=nombre_corto(p["desde"]),
                              hasta=nombre_corto(p["hasta"]), sale=f"{p['sale']:%H:%M}", llega=f"{p['llega']:%H:%M}",
                              espera=espera, nota=p["nota"]))
            camino.append([p["linea"], p["viaje"].split("|")[2], p["desde"], p["hasta"]])
            prev = p
        opciones.append(dict(sale=f"{buses[0]['sale']:%H:%M}", salir=f"{salir:%H:%M}", llega=f"{llegar:%H:%M}",
                             manana=salir.date() > ahora.date(), transbordos=nbus - 1,
                             duracion=round((llegar - salir).total_seconds() / 60), pasos=pasos, camino=camino))
    return jsonify(ok=True, desde=dict(nombre=no, punto=po), hasta=dict(nombre=nd, punto=pd), opciones=opciones)


def _lugar(x):
    """[lat, lon] de una parada o de un punto '@lat,lon' de la isla (None si no vale)."""
    if not isinstance(x, str):
        return None
    if x in PARADAS:
        return [PARADAS[x]["lat"], PARADAS[x]["lon"]]
    try:
        lat, lon = map(float, x.lstrip("@").split(","))
        return [lat, lon] if x.startswith("@") and 27.6 < lat < 29.0 and -14.7 < lon < -13.3 else None
    except ValueError:
        return None


@app.get("/api/camino")
def api_camino():
    """Dibujo de un camino de /api/ruta: t = JSON [[línea, sentido, desde, hasta] | ["pie", desde, hasta], …]."""
    try:
        t = json.loads(request.args.get("t", ""))
        assert isinstance(t, list) and 1 <= len(t) <= 6 and all(isinstance(x, list) for x in t)
    except (ValueError, AssertionError):
        raise Error("camino no válido")
    res = []
    for x in t:
        if len(x) == 3 and x[0] == "pie" and _lugar(x[1]) and _lugar(x[2]):
            a, b = _lugar(x[1]), _lugar(x[2])
            res.append(dict(tipo="pie", p=[[a, b]], paradas=[y for y in x[1:] if y in PARADAS]))
        elif len(x) == 4 and x[0] in BASE and x[1] in ("ida", "vuelta") and x[2] in PARADAS and x[3] in PARADAS:
            tr = CAMINOS.tramo(x[0], x[1], x[2], x[3])
            if not tr:
                raise Error("esa línea no va de una parada a la otra", 404)
            res.append(dict(tipo="bus", linea=x[0], color=COLOR[x[0]], **tr))
        else:
            raise Error("camino no válido")
    return jsonify(ok=True, tramos=res)


# ── "Avísame 5 min antes" ────────────────────────────────────────────────────────────────────────────────
MINUTOS_AVISO = 5


def _salida(pid, viaje, ahora_l):
    """La salida 'viaje' tal como la ve ahora la parada pid (con correcciones en vivo), o None si ya pasó."""
    gr = PARADAS.get(pid)
    if not gr:
        return None, None
    for r in g.proximas(dict(nombre=gr["nombre"], ids=gr["ids"]), ahora_l, HORARIOS, BASE, com=COM, gracia=3, modo="dinamico"):
        if r["viaje"] == viaje:
            return r, gr
    return None, gr


@app.get("/api/recordatorios")
def api_recordatorios():
    dev = autenticar()
    return jsonify(ok=True, recordatorios=COM.recordatorios_de(dev))


@app.post("/api/recordatorio")
def api_recordatorio():
    """Pedir (o cancelar) un aviso push unos minutos antes de que una guagua concreta llegue a una parada."""
    dev = autenticar()
    d = request.get_json(silent=True) or {}
    pid, viaje = str(d.get("parada", ""))[:40], str(d.get("viaje", ""))[:120]
    if d.get("cancelar"):
        COM.cancelar_recordatorio(dev, viaje, pid)
        return jsonify(ok=True, activo=False, mensaje="Aviso cancelado.")
    if pid not in PARADAS:
        raise Error("parada desconocida", 404)
    ahora_l = datetime.now(TZ).replace(tzinfo=None)
    r, gr = _salida(pid, viaje, ahora_l)
    if not r:
        raise Error("esa guagua ya ha pasado o no para aquí")
    minima = g.llegada_minima(r)
    if (minima - ahora_l).total_seconds() / 60 <= MINUTOS_AVISO + 1:
        raise Error("ya está a punto de llegar: ¡ve a la parada!")
    if not COM.obtener_fcm_token(dev):
        raise Error("activa las notificaciones para poder avisarte", 409)
    COM.crear_recordatorio(dev, viaje, pid, MINUTOS_AVISO, ahora_ts())
    return jsonify(ok=True, activo=True,
                   mensaje=f"Te avisaremos {MINUTOS_AVISO} min antes de que llegue (hacia las {minima - timedelta(minutes=MINUTOS_AVISO):%H:%M}).")


def revisar_recordatorios():
    """Envía los avisos cuya guagua está a ≤5 min (con la hora más temprana posible, para no llegar tarde)."""
    ahora, ahora_l = ahora_ts(), datetime.now(TZ).replace(tzinfo=None)
    for rec in COM.recordatorios_pendientes():
        r, gr = _salida(rec["parada"], rec["viaje"], ahora_l)
        if r is None:
            COM.marcar_recordatorio(rec["dev"], rec["viaje"], rec["parada"], ahora)   # ya pasó
            continue
        minima = g.llegada_minima(r)
        faltan = (minima - ahora_l).total_seconds() / 60
        if faltan > rec["minutos"]:
            continue
        token = COM.obtener_fcm_token(rec["dev"])
        if token:
            cuando = "ya mismo" if faltan < 1 else f"en unos {round(faltan)} min"
            ok, motivo = send_fcm_push(token, f"Línea {r['linea']} → {r['destino']}",
                                       f"Pasa por {gr['nombre'].split(' · L')[0]} {cuando} (desde las {minima:%H:%M}).",
                                       data={"url": f"/?parada={rec['parada']}"})
            if motivo == "token_invalido":
                COM.borrar_fcm_token(rec["dev"])
        COM.marcar_recordatorio(rec["dev"], rec["viaje"], rec["parada"], ahora)


def _bucle_recordatorios():
    while True:
        time.sleep(30)
        try:
            revisar_recordatorios()
        except Exception:
            app.logger.exception("revisando recordatorios")


if os.environ.get("RENDER") or os.environ.get("GUAGUAS_RECORDATORIOS"):   # en producción (un solo proceso de gunicorn)
    import threading
    threading.Thread(target=_bucle_recordatorios, daemon=True, name="recordatorios").start()


# ── Paradas favoritas (con sesión de Google) ───────────────────────────────────────────────────────────────
def uid_sesion():
    cab = request.headers.get("Authorization", "")
    if not cab.startswith("Bearer "):
        raise Error("inicia sesión para guardar favoritas", 401)
    return verify_internal_token(cab[7:])["uid"]


@app.get("/api/favoritas")
def api_favoritas():
    return jsonify(ok=True, paradas=[p for p in COM.favoritas(uid_sesion()) if p in PARADAS])


@app.post("/api/favoritas")
def api_favoritas_poner():
    uid = uid_sesion()
    d = request.get_json(silent=True) or {}
    pid = str(d.get("parada", ""))[:40]
    if pid not in PARADAS:
        raise Error("parada desconocida", 404)
    COM.poner_favorita(uid, pid, bool(d.get("si", True)), ahora_ts())
    return jsonify(ok=True, paradas=[p for p in COM.favoritas(uid) if p in PARADAS])


@app.get("/")
def inicio():
    """La web, con la vista previa (Open Graph) adaptada si el enlace es de una parada: ?parada=<id>."""
    from html import escape
    from flask import Response
    if "_index" not in _cache_web:
        _cache_web["_index"] = (RAIZ / "web" / "index.html").read_text(encoding="utf-8")
    html_ = _cache_web["_index"]
    base = ("https://" if os.environ.get("RENDER") else request.scheme + "://") + request.host
    titulo, desc = "Guaguas Fuerteventura", ("Cuándo pasa la guagua por tu parada en Fuerteventura: horarios de todas las "
                                             "líneas y tiempo real gracias a la comunidad.")
    url = base + "/"
    pid = request.args.get("parada", "")
    if pid in PARADAS:
        gr = PARADAS[pid]
        titulo = f"Guaguas en {nombre_corto(pid)}"
        desc = f"Próximas guaguas de las líneas {', '.join(sorted(gr['lineas']))} en esta parada, con tiempo real."
        url = f"{base}/?parada={pid}"
    og = "\n".join([
        f'<meta name="description" content="{escape(desc)}">',
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="Guaguas Fuerteventura">',
        f'<meta property="og:title" content="{escape(titulo)}">',
        f'<meta property="og:description" content="{escape(desc)}">',
        f'<meta property="og:url" content="{escape(url)}">',
        f'<meta property="og:image" content="{base}/icons/icon-512.png">',
        '<meta name="twitter:card" content="summary">'])
    i, j = html_.index("<!--og-->"), html_.index("<!--/og-->") + len("<!--/og-->")
    html_ = html_[:i] + og + html_[j:]
    if pid in PARADAS:
        html_ = html_.replace("<title>Guaguas Fuerteventura</title>", f"<title>{escape(titulo)} · Guaguas FTV</title>", 1)
    return Response(html_, mimetype="text/html")


_cache_web = {}


@app.get("/api/paradas")
def api_paradas():
    return jsonify([dict(id=p["id"], nombre=p["nombre"], lat=round(p["lat"], 5), lon=round(p["lon"], 5), lineas=sorted(p["lineas"]))
                     for p in PARADAS.values()])


def _partes(l):
    """Trazado de una línea como lista de tramos [[lat, lon], …]."""
    tr = l.get("trazado")
    if not tr:
        return [[[p["lat"], p["lon"]] for p in l["paradas"] if "opt" not in p]]
    return [tr] if isinstance(tr[0][0], (int, float)) else tr


BANDAS = bandas.calcular({n: _partes(l) for n, l in BASE.items()})  # líneas paralelas donde comparten calle


@app.get("/api/lineas")
def api_lineas():
    if request.args.get("bandas"):  # la web: tramos con desplazamiento lateral, sin el trazado completo
        return jsonify([dict(num=n, nombre=HORARIOS[n]["nombre"], color=COLOR[n], tramos=BANDAS.get(n, [])) for n in BASE])
    return jsonify([dict(num=n, nombre=HORARIOS[n]["nombre"], color=COLOR[n], calibrada="duracion_min" in l, real=bool(l.get("real")),
                         trazado=(_partes(l)[0] if len(_partes(l)) == 1 else _partes(l)))
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
    pg = pg_db
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