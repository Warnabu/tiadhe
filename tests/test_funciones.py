#!/usr/bin/env python3
"""Pruebas de las funciones de la app: avísame 5 min antes, favoritas, ver línea, cómo llegar, estadísticas,
horas conservadoras y vista previa al compartir.

    python tests/test_funciones.py
"""
import base64, json, os, sys, time, uuid
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "1")
os.environ["GUAGUAS_SIN_LIMITES"] = "1"
os.environ.pop("RENDER", None)
for f in ("comunidad_test.db", "comunidad_test.db-wal", "comunidad_test.db-shm"):
    try:
        os.remove(os.path.join(RAIZ, "datos", f))
    except OSError:
        pass

from datetime import datetime, timedelta
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from app import servidor as s, guaguas as g
from app.comunidad import mensaje

ok = fallos = 0
def check(cond, texto):
    global ok, fallos
    ok += bool(cond); fallos += not cond
    print(("[OK] " if cond else "[FAIL] ") + texto)

c = s.app.test_client()
k = ec.generate_private_key(ec.SECP256R1())
raw = k.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
dev = s.Comunidad.id_de(raw)

def pedir(m, ruta, body=None, sin_dev=False):
    ts, n = str(int(time.time())), uuid.uuid4().hex
    txt = json.dumps(body, separators=(",", ":")) if body is not None else ""
    r_, s_ = decode_dss_signature(k.sign(mensaje(m, ruta, ts, n, txt), ec.ECDSA(hashes.SHA256())))
    h = {"X-Ts": ts, "X-Nonce": n, "Content-Type": "application/json",
         "X-Firma": base64.b64encode(r_.to_bytes(32, "big") + s_.to_bytes(32, "big")).decode()}
    if not sin_dev:
        h["X-Dispositivo"] = dev
    r = c.open(ruta, method=m, data=txt or None, headers=h)
    return r.status_code, r.get_json()

check(pedir("POST", "/api/registro", {"pub": base64.b64encode(raw).decode()}, sin_dev=True)[0] == 200, "alta de dispositivo")

# --- horas conservadoras: lo que se muestra es siempre lo más pronto
ahora = datetime(2026, 11, 24, 10, 0)
r = dict(prec="estimado", cal=False, off=80, llegada=ahora + timedelta(minutes=90))
check(g.texto_espera(r, ahora).startswith("desde las") and g.llegada_minima(r) < r["llegada"], f"más de 1 h: «{g.texto_espera(r, ahora)}»")
r = dict(prec="estimado", cal=False, off=40, llegada=ahora + timedelta(minutes=25))
lo = int(g.texto_espera(r, ahora).split()[1].split("-")[0])
check(lo <= 25 - g.margenes(r)[0], f"rango: «{g.texto_espera(r, ahora)}» (empieza en lo más pronto)")
check(g.margenes(dict(prec="oficial"))[0] == 0, "hora oficial de cabecera sin margen")

# --- paradas fusionadas: la estación es una sola
est = [v for v in s.PARADAS.values() if v["nombre"].lower().startswith("estacion puerto")]
check(len(est) == 1 and len(est[0]["lineas"]) >= 8, f"la estación de Puerto es una sola parada con {len(est[0]['lineas']) if est else 0} líneas")
pid_est = next(k_ for k_, v in s.PARADAS.items() if v["nombre"].lower().startswith("estacion puerto"))

# --- avísame 5 min antes
ahora_l = datetime.now(s.TZ).replace(tzinfo=None)
sal = next(x for x in g.proximas(dict(nombre="e", ids=s.PARADAS[pid_est]["ids"]), ahora_l, s.HORARIOS, s.BASE, com=s.COM)
           if (g.llegada_minima(x) - ahora_l).total_seconds() > 15 * 60)
st, j = pedir("POST", "/api/recordatorio", {"parada": pid_est, "viaje": sal["viaje"]})
check(st == 409, "sin notificaciones activadas pide activarlas")
pedir("POST", "/api/register-fcm-token", {"fcm_token": "t" * 40})
st, j = pedir("POST", "/api/recordatorio", {"parada": pid_est, "viaje": sal["viaje"]})
check(st == 200 and j["activo"], f"aviso creado: {j.get('mensaje')}")
check(pedir("GET", "/api/recordatorios")[1]["recordatorios"] == [{"parada": pid_est, "viaje": sal["viaje"]}], "aparece en los pendientes")
enviados = []
s.send_fcm_push = lambda tok, t, b, data=None: (enviados.append((t, b, data)) or (True, None))
s.revisar_recordatorios()
check(not enviados, "no se envía mientras la guagua está lejos")
real = s.datetime
class Reloj(real):
    @classmethod
    def now(cls, tz=None):
        return real.now(tz) + (g.llegada_minima(sal) - ahora_l) - timedelta(minutes=4)
s.datetime = Reloj
s.revisar_recordatorios()
s.datetime = real
check(len(enviados) == 1 and "en unos 4 min" in enviados[0][1] and enviados[0][2]["url"].endswith(pid_est),
      f"a 4 min se envía la notificación: {enviados[0][1] if enviados else '-'}")
check(pedir("GET", "/api/recordatorios")[1]["recordatorios"] == [], "y ya no está pendiente")
st, _ = pedir("POST", "/api/recordatorio", {"parada": pid_est, "viaje": "20200101|01|ida|10:00|||"})
check(st == 400, "un viaje que no existe se rechaza")

# --- favoritas (solo con sesión)
tok = s.create_internal_token("uid-prueba")
check(c.get("/api/favoritas").status_code == 401, "favoritas sin sesión: 401")
check(c.get("/api/favoritas", headers={"Authorization": "Bearer falso"}).status_code == 401, "token falso: 401")
j = c.post("/api/favoritas", json={"parada": pid_est, "si": True}, headers={"Authorization": "Bearer " + tok}).get_json()
check(j["paradas"] == [pid_est], "guardar favorita")
j = c.post("/api/favoritas", json={"parada": pid_est, "si": False}, headers={"Authorization": "Bearer " + tok}).get_json()
check(j["paradas"] == [], "quitar favorita")

# --- ver línea y cómo llegar
j = c.get("/api/linea/01").get_json()
check(len(j["paradas"]) > 50 and j["paradas"][0]["nombre"].lower().startswith("estacion puerto"), f"línea 01: {len(j['paradas'])} paradas desde la estación")
check(all(len(v) == len(set(v)) for v in j["salidas"].values()), "salidas sin horas repetidas")
check(c.get("/api/linea/xx").status_code == 404, "línea inexistente: 404")
mj = next(k_ for k_, v in s.PARADAS.items() if v["nombre"] == "Morro Jable" and "01" in v["lineas"])
j = c.get(f"/api/ruta?desde={pid_est}&hasta={mj}").get_json()
check(j["opciones"] and all(o["linea"] in ("01", "10", "15") for o in j["opciones"]), f"Puerto → Morro Jable: {len(j['opciones'])} guaguas directas")
o = j["opciones"][0]
check(o["llega"] > o["sale"] or o["manana"], "llega después de salir")
j2 = c.get(f"/api/ruta?desde={mj}&hasta={pid_est}").get_json()
check(all(x["linea"] in ("01", "10", "15") for x in j2["opciones"]), "y al revés, solo líneas que van en ese sentido")

# --- cómo llegar con transbordos
from app.planificador import MIN_TRANSBORDO
tui = next(k_ for k_, v in s.PARADAS.items() if "tuineje" in v["nombre"].lower() and "16" in v["lineas"])
cot = next(k_ for k_, v in s.PARADAS.items() if "cotillo" in v["nombre"].lower() and "08" in v["lineas"])
j = c.get(f"/api/ruta?desde={tui}&hasta={cot}").get_json()
check(not j["opciones"] and j["transbordos"], f"Tuineje → El Cotillo: sin directa, {len(j['transbordos'])} opciones con transbordo")
bien = True
for t in j["transbordos"]:
    buses = [p for p in t["pasos"] if p["tipo"] == "bus"]
    bien &= t["transbordos"] == len(buses) - 1 >= 1 and buses[0]["sale"] == t["sale"] and buses[-1]["llega"] == t["llega"]
    bien &= all(p["espera"] is None or p["espera"] >= MIN_TRANSBORDO for p in buses)
check(bien, f"cada transbordo deja ≥{MIN_TRANSBORDO} min (contando lo más tarde que llega y lo más pronto que sale)")
check(any(any("puerto" in p.get("hasta", "").lower() for p in t["pasos"]) for t in j["transbordos"]), "propone cambiar en Puerto del Rosario")
its = s.PLAN.itinerarios(tui, cot, datetime(2026, 11, 24, 6, 0))
check(all(b["sale"] >= a["llega"] + timedelta(minutes=MIN_TRANSBORDO) for it in its
          for a, b in zip([p for p in it if p["tipo"] == "bus"], [p for p in it if p["tipo"] == "bus"][1:])),
      f"planificador: {len(its)} itinerarios con margen real en los cambios")
j = c.get(f"/api/ruta?desde={pid_est}&hasta={mj}").get_json()
check(j["transbordos"] == [], "Puerto → Morro Jable: con directas no propone dar vueltas")

# --- elegir camino: en el mapa solo el trozo de cada línea que se usa
import json as _json
def camino(t):
    r = c.get("/api/camino?t=" + _json.dumps(t))
    return r.status_code, r.get_json()
st, jc = camino(j["opciones"][0]["camino"])
tr = jc["tramos"][0]
ini, fin = tr["p"][0][0], tr["p"][-1][-1]
check(st == 200 and tr["paradas"][0] == pid_est and tr["paradas"][-1] == mj, f"directa: de la estación a Morro Jable, {len(tr['paradas'])} paradas")
check(g.km(dict(lat=ini[0], lon=ini[1]), s.PARADAS[pid_est]) < 0.15 and g.km(dict(lat=fin[0], lon=fin[1]), s.PARADAS[mj]) < 0.15,
      "el dibujo empieza en la parada de subida y acaba en la de bajada")
jt = c.get(f"/api/ruta?desde={tui}&hasta={cot}").get_json()["transbordos"][0]
st, jc = camino(jt["camino"])
buses = [x for x in jc["tramos"] if x["tipo"] == "bus"]
check(st == 200 and len(buses) == jt["transbordos"] + 1,
      f"transbordo: {len(buses)} trozos de línea ({', '.join(x['linea'] for x in buses)})")
ok_union = all(g.km(dict(lat=x["p"][-1][-1][0], lon=x["p"][-1][-1][1]), dict(lat=y["p"][0][0][0], lon=y["p"][0][0][1])) < 0.4
               for x, y in zip(jc["tramos"], jc["tramos"][1:]))
check(ok_union, "cada trozo empieza donde acaba el anterior (o a un paseo corto)")
largo = sum(g.km(dict(lat=a_[0], lon=a_[1]), dict(lat=b_[0], lon=b_[1])) for x in buses for parte in x["p"] for a_, b_ in zip(parte, parte[1:]))
check(largo < 150, f"no dibuja las líneas enteras: {largo:.0f} km de recorrido")
check(camino([["01", "ida", mj, pid_est]])[0] == 404, "un sentido que no va de una parada a la otra: 404")
check(camino("x")[0] == 400 and c.get("/api/camino?t=[[1,2]]").status_code == 400, "camino mal formado: 400")

# --- estadísticas y vista previa
j = c.get("/api/estadisticas").get_json()
check(j["ok"] and {"avisos", "personas", "confirmadas"} <= set(j), f"estadísticas de hoy: {j}")
h = c.get("/?parada=" + pid_est).get_data(as_text=True)
check('og:title" content="Guaguas en Estacion Puerto' in h and "<title>Guaguas en Estacion Puerto" in h, "vista previa de una parada")
h = c.get("/?parada=%3Cscript%3E").get_data(as_text=True)
import re
og = " ".join(re.findall(r'<meta (?:property|name)="(?:og:|twitter:|description)[^>]*>', h))
check("<script" not in og.lower() and "%3c" not in og.lower() and 'og:title" content="Guaguas Fuerteventura' in og, "parámetro manipulado: vista previa normal, sin inyectar nada")

print(f"\n{ok} correctas, {fallos} fallos")
sys.exit(1 if fallos else 0)
