#!/usr/bin/env python3
"""Regresiones de producción (Render + Supabase). Ejecutar contra PostgreSQL REAL:
    DATABASE_URL=postgresql://... python test_produccion.py
Sin DATABASE_URL usa SQLite local. Cubre fallos que SQLite NO detecta."""
import base64, json, os, sys, time, uuid
os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "1")
os.environ["RENDER"] = "true"; os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ["MAX_REGISTROS_IP"] = "5"
os.environ.pop("GUAGUAS_DEBUG", None)
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import servidor as s
import postgresql_db as pg
from comunidad_pg import Comunidad, mensaje

for t in ("subidas", "observaciones", "nonces", "dispositivos", "registros", "ubicaciones_activas"):
    pg.execute(f"DELETE FROM {t}")
c = s.app.test_client()
ok = fallos = 0
def check(cond, texto):
    global ok, fallos
    ok += bool(cond); fallos += not cond
    print(("[OK] " if cond else "[FAIL] ") + texto)

class Disp:
    def __init__(d):
        d.k = ec.generate_private_key(ec.SECP256R1())
        d.raw = d.k.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        d.b64, d.id = base64.b64encode(d.raw).decode(), Comunidad.id_de(d.raw)
    def pedir(d, m, r, body=None, cf_ip="203.0.113.1", ts=None):
        ts = str(ts if ts is not None else int(time.time())); n = uuid.uuid4().hex
        txt = json.dumps(body, separators=(",", ":")) if body is not None else ""
        r_, s_ = decode_dss_signature(d.k.sign(mensaje(m, r, ts, n, txt), ec.ECDSA(hashes.SHA256())))
        h = {"X-Dispositivo": d.id, "X-Ts": ts, "X-Nonce": n, "Content-Type": "application/json", "CF-Connecting-IP": cf_ip,
             "X-Firma": base64.b64encode(r_.to_bytes(32, "big") + s_.to_bytes(32, "big")).decode()}
        rr = c.open(r, method=m, data=txt or None, headers=h, environ_base={"REMOTE_ADDR": "10.0.0.1"})
        return rr.status_code, rr.get_json()

print("USE_POSTGRES =", pg.USE_POSTGRES)
a = Disp(); st, j = a.pedir("POST", "/api/registro", {"pub": a.b64}); check(st == 200, "alta de dispositivo")
st, j = a.pedir("GET", "/api/yo"); check(st == 200 and j["perfil"]["nivel"] == "Nuevo", "petición firmada de dispositivo YA registrado (BYTEA->bytes)")
tok = "fcm-token-de-prueba-" + "x" * 40
st, j = a.pedir("POST", "/api/register-fcm-token", {"fcm_token": tok}); check(st == 200, "register-fcm-token responde 200")
check(s.COM.obtener_fcm_token(a.id) == tok, "el token FCM se guarda de verdad en la BD")
st, j = a.pedir("POST", "/api/register-fcm-token", {"fcm_token": "corto"}); check(st == 400, "token FCM no válido -> 400")
st, j = a.pedir("POST", "/api/test-push", {}); check(st in (200, 502) and "motivo" in j, f"test-push no revienta sin credenciales ({j})")
codigos = []
for i in range(6):
    u = Disp(); codigos.append(u.pedir("POST", "/api/registro", {"pub": u.b64}, cf_ip="198.51.100.7")[0])
check(codigos[:4] == [200] * 4 and 429 in codigos, f"límite por IP real: {codigos}")
u = Disp(); st, _ = u.pedir("POST", "/api/registro", {"pub": u.b64}, cf_ip="198.51.100.99")
check(st == 200, "otra IP real tras el mismo proxy NO queda bloqueada")
r = c.get("/api/yo", headers={"X-Dispositivo": "x", "X-Ts": "abc", "X-Nonce": "n", "X-Firma": "f"}); check(r.status_code == 401, f"X-Ts no numérico -> 401 ({r.status_code})")
check(c.get("/api/debug/pool").status_code == 404 and c.get("/api/debug/version").status_code == 404, "/api/debug/* ocultos en producción")
check(c.get("/api/cerca?lat=nan&lon=nan").status_code == 400, "lat/lon NaN rechazados")
check(c.get("/api/cerca?lat=28.3&lon=-14.0&modo=zzz").status_code == 200, "modo inválido -> por defecto")
body = c.post("/api/firebase-token", json={"id_token": "x"}).get_data(as_text=True)
check("Traceback" not in body and "File \"" not in body, "errores sin traceback")
r = c.open("/api/register-fcm-token", method="POST", data="{}", headers={"Content-Type": "application/json"}); check(r.status_code == 401, "sin cabeceras firmadas -> 401")
s.COM._ult_mant = 0; s.COM.mantenimiento(time.time()); check(True, "mantenimiento periódico sin errores")
# Retry-After en el 429
# (la IP 198.51.100.7 ya agotó su cupo arriba)
u2 = Disp(); ts = str(int(time.time())); n = uuid.uuid4().hex; txt = json.dumps({"pub": u2.b64}, separators=(",", ":"))
r_, s_ = decode_dss_signature(u2.k.sign(mensaje("POST", "/api/registro", ts, n, txt), ec.ECDSA(hashes.SHA256())))
rr = c.open("/api/registro", method="POST", data=txt, headers={"Content-Type": "application/json", "CF-Connecting-IP": "198.51.100.7", "X-Dispositivo": u2.id, "X-Ts": ts, "X-Nonce": n,
            "X-Firma": base64.b64encode(r_.to_bytes(32, "big") + s_.to_bytes(32, "big")).decode()}, environ_base={"REMOTE_ADDR": "10.0.0.1"})
check(rr.status_code == 429 and rr.headers.get("Retry-After"), f"429 incluye Retry-After ({rr.headers.get('Retry-After')})")

# Proxy de Firebase Auth (con requests simulado: aquí no hay salida a internet)
import requests as rq
class _R:
    status_code = 200; content = b'{"ok":1}'; headers = {"Content-Type": "application/json", "Set-Cookie": "x=1"}
llamadas = []
orig = rq.request
rq.request = lambda m, url, **kw: (llamadas.append((m, url, kw.get("params"))), _R())[1]
rr = c.get("/__/firebase/init.json?x=1")
rq.request = orig
check(rr.status_code == 200 and llamadas and llamadas[0][1] == "https://guaguas-ftv1.firebaseapp.com/__/firebase/init.json", f"proxy /__/firebase/init.json -> {llamadas[:1]}")
check("Set-Cookie" not in rr.headers and rr.headers.get("Cache-Control") == "no-store", "el proxy no reenvía cookies y no cachea")
idx = c.get("/").get_data(as_text=True); sw = c.get("/sw.js").get_data(as_text=True)
check("firebaseListo" in idx and "VERSION" in sw and "'/__/'" in sw, "index.html y sw.js servidos con las versiones nuevas")
print(f"\n{ok} correctas, {fallos} fallos"); sys.exit(1 if fallos else 0)
