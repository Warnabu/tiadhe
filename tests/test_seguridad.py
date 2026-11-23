#!/usr/bin/env python3
"""Pruebas de seguridad: IP real detrás de proxy, cabeceras, límite por IP, modo pruebas y huella de IP.

    python tests/test_seguridad.py
"""
import os, sys
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "1")
os.environ["RENDER"] = "true"                 # como en producción
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ["GUAGUAS_DEBUG"] = "1"             # aunque alguien la deje puesta, en producción no debe hacer nada

from flask import Flask, request
from app import seguridad as S
from app import servidor as s
from app.comunidad import Comunidad

ok = fallos = 0
def check(cond, texto):
    global ok, fallos
    ok += bool(cond); fallos += not cond
    print(("[OK] " if cond else "[FAIL] ") + texto)

# --- IP real
f = Flask("t")
def ip(headers, remote="10.0.0.7", proxy=True):
    with f.test_request_context("/", headers=headers, environ_base={"REMOTE_ADDR": remote}):
        return S.ip_real(request, proxy)

check(ip({}) == "10.0.0.7", "sin cabeceras: la IP de la conexión")
check(ip({"X-Forwarded-For": "88.1.2.3"}) == "88.1.2.3", "Render: IP de X-Forwarded-For")
check(ip({"X-Forwarded-For": "1.1.1.1, 88.1.2.3"}) == "88.1.2.3", "X-Forwarded-For falseado por el cliente: manda la que añade Render (la última)")
check(ip({"X-Forwarded-For": "88.1.2.3", "CF-Connecting-IP": "5.5.5.5"}) == "88.1.2.3",
      "CF-Connecting-IP inventada sin pasar por Cloudflare: se ignora")
check(ip({"X-Forwarded-For": "88.1.2.3, 172.70.1.1", "CF-Connecting-IP": "88.1.2.3"}) == "88.1.2.3",
      "petición real a través de Cloudflare: CF-Connecting-IP")
check(ip({"X-Forwarded-For": "88.1.2.3"}, proxy=False) == "10.0.0.7", "fuera de un proxy no se miran las cabeceras")

# --- servidor en modo producción
check(not s.DEBUG, "GUAGUAS_DEBUG no activa el modo pruebas en producción")
c = s.app.test_client()
r = c.get("/api/debug/version")
check(r.status_code == 404, f"/api/debug/* cerrado en producción ({r.status_code})")
r = c.get("/")
h = r.headers
check(h.get("X-Content-Type-Options") == "nosniff" and h.get("X-Frame-Options") == "SAMEORIGIN", "cabeceras básicas")
check("frame-ancestors 'self'" in h.get("Content-Security-Policy", ""), "CSP aplicada (anti-clickjacking)")
check("Strict-Transport-Security" in h, "HSTS en producción")
check("integrity=" in r.get_data(as_text=True), "Leaflet con integridad (SRI)")

# --- límite por IP (escrituras: 30/min)
codigos = [c.post("/api/registro", json={}, environ_base={"REMOTE_ADDR": "10.0.0.9"},
                  headers={"X-Forwarded-For": "77.7.7.7"}).status_code for _ in range(32)]
check(codigos[-1] == 429 and 429 not in codigos[:30], f"límite de 30 escrituras/min por IP ({codigos.count(429)} rechazadas)")
otra = c.post("/api/registro", json={}, headers={"X-Forwarded-For": "66.6.6.6"}).status_code
check(otra != 429, "otra IP no se ve afectada por el límite de la primera")

# --- tamaño máximo
r = c.post("/api/registro", data="x" * 40000, content_type="application/json", headers={"X-Forwarded-For": "55.5.5.5"})
check(r.status_code == 413, f"petición de 40 KB rechazada ({r.status_code})")

# --- huella de IP
a, b = Comunidad.seudonimo_ip("88.1.2.3"), Comunidad.seudonimo_ip("88.1.2.4")
check(a != b and "." not in a and len(a) == 32 and a == Comunidad.seudonimo_ip("88.1.2.3"),
      "la base de datos guarda una huella de la IP, no la IP")

print(f"\n{ok} correctas, {fallos} fallos")
sys.exit(1 if fallos else 0)
