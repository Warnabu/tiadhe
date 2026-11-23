"""Seguridad del servidor: cabeceras HTTP, límite de peticiones por IP y tamaño máximo de las peticiones.

Se aplica con `proteger(app, client_ip)` desde servidor.py.
"""
import ipaddress
import os
import threading
import time

from flask import jsonify, request

MAX_CUERPO = 32 * 1024   # ninguna petición legítima de la app pasa de unos cientos de bytes

# Límite de peticiones por IP: (peticiones, segundos). Las consultas normales de horarios no se limitan (van
# cacheadas); sí lo que escribe en la base de datos, llama a Firebase o calcula para coordenadas arbitrarias.
LIMITES = {
    "escritura": (30, 60),      # POST /api/* (registro, subida, token FCM…)
    "token": (10, 60),          # POST /api/firebase-token (cada una es una verificación en Google)
    "cerca": (60, 60),          # GET /api/cerca (calcula horarios para cualquier punto)
    "auth": (60, 60),           # proxy de Firebase Auth /__/auth/*
}

# Política de contenidos. La estricta va en modo "solo informe" (Report-Only): el navegador avisa en la consola si
# algo la incumple pero no lo bloquea, para no romper el login de Google si Firebase cambia de dominios. Cuando se
# compruebe en producción que no hay avisos, se puede pasar a Content-Security-Policy.
CSP_INFORME = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://unpkg.com https://www.gstatic.com https://apis.google.com",
    "style-src 'self' 'unsafe-inline' https://unpkg.com",
    "img-src 'self' data: blob: https://tile.openstreetmap.org https://*.googleusercontent.com",
    "connect-src 'self' https://*.googleapis.com https://www.gstatic.com https://unpkg.com",
    "frame-src 'self' https://*.firebaseapp.com https://accounts.google.com https://apis.google.com",
    "worker-src 'self'",
    "manifest-src 'self'",
])
# Lo que no puede romper nada y sí se aplica: sin plugins, sin <base> ajeno, solo se puede enmarcar a sí misma
# (Firebase Auth usa un iframe propio servido por este mismo dominio) y los formularios solo envían aquí.
CSP_APLICADA = "object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'"


# Rangos oficiales de Cloudflare (https://www.cloudflare.com/ips/). Solo si la petición llega desde uno de ellos se
# cree la cabecera CF-Connecting-IP; si no, cualquiera podría inventársela para saltarse los límites por IP.
_CLOUDFLARE = [ipaddress.ip_network(r) for r in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18", "108.162.192.0/18",
    "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29",
    "2c0f:f248::/32")]


def _ip(texto):
    try:
        return ipaddress.ip_address((texto or "").strip())
    except ValueError:
        return None


def ip_real(req, detras_de_proxy):
    """IP del visitante.

    En Render la petición llega a la app desde un proxy interno (remote_addr = 10.x.x.x), así que hay que mirar
    X-Forwarded-For. El cliente puede escribir lo que quiera en esa cabecera, pero el proxy de Render AÑADE al final
    la IP desde la que le llegó la conexión: por eso se toma la última IP pública de la lista. Si esa IP es de
    Cloudflare, la petición pasó por Cloudflare y la del visitante es CF-Connecting-IP."""
    remota = req.remote_addr or "?"
    if not detras_de_proxy:
        return remota
    lista = [_ip(x) for x in req.headers.get("X-Forwarded-For", "").split(",")]
    vista = next((a for a in reversed(lista) if a and a.is_global), None)
    cf = _ip(req.headers.get("CF-Connecting-IP"))
    if vista and cf and cf.is_global and any(vista in red for red in _CLOUDFLARE):
        return str(cf)
    return str(vista) if vista else remota


class LimitadorIP:
    """Ventana deslizante en memoria (un solo proceso de gunicorn: no hace falta Redis)."""

    def __init__(self):
        self.marcas, self.lock = {}, threading.Lock()

    def permitir(self, clave, n, segundos):
        ahora = time.monotonic()
        with self.lock:
            if len(self.marcas) > 20000:   # limpieza: IPs que ya no están activas
                self.marcas = {k: v for k, v in self.marcas.items() if v and ahora - v[-1] < 3600}
            lista = [t for t in self.marcas.get(clave, []) if ahora - t < segundos]
            if len(lista) >= n:
                self.marcas[clave] = lista
                return False, int(segundos - (ahora - lista[0])) + 1
            lista.append(ahora)
            self.marcas[clave] = lista
            return True, 0


def _grupo():
    p = request.path
    if p.startswith("/__/"):
        return "auth"
    if p == "/api/firebase-token":
        return "token"
    if p == "/api/cerca":
        return "cerca"
    if p.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        return "escritura"
    return None


def proteger(app, client_ip):
    app.config["MAX_CONTENT_LENGTH"] = MAX_CUERPO
    limitador = LimitadorIP()
    produccion = bool(os.environ.get("RENDER"))

    @app.before_request
    def _limitar():
        g = _grupo()
        if g is None or os.environ.get("GUAGUAS_SIN_LIMITES"):   # GUAGUAS_SIN_LIMITES solo para pruebas locales
            return None
        n, segundos = LIMITES[g]
        ok, espera = limitador.permitir((client_ip(), g), n, segundos)
        if not ok:
            resp = jsonify(ok=False, error="demasiadas peticiones; espera un momento")
            resp.headers["Retry-After"] = str(espera)
            return resp, 429
        return None

    @app.errorhandler(413)
    def _demasiado_grande(e):
        return jsonify(ok=False, error="petición demasiado grande"), 413

    @app.after_request
    def _cabeceras(resp):
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("X-Frame-Options", "SAMEORIGIN")
        h.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=(), payment=(), usb=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")   # el login de Google usa una ventana
        if produccion:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if not request.path.startswith("/__/"):   # las páginas de Firebase Auth traen su propia política
            h.setdefault("Content-Security-Policy", CSP_APLICADA)
            h.setdefault("Content-Security-Policy-Report-Only", CSP_INFORME)
        if request.path.startswith("/api/") and (request.method != "GET" or request.path == "/api/yo"):
            h["Cache-Control"] = "no-store"   # respuestas con datos del dispositivo: que no se guarden en cachés
        return resp
