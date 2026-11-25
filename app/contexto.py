"""Lo que depende de cada petición: qué hora es, quién la hace (IP, dispositivo firmado) y la comunidad (avisos).

En modo pruebas (GUAGUAS_DEBUG=1, nunca en producción) ?hora=2026-09-24T10:05 simula la hora.
"""
import time
from datetime import datetime

from flask import current_app, request

from .comunidad import Comunidad, Error, mensaje
from .config import DEBUG, DETRAS_DE_PROXY, LOG_IP, TZ
from .datos import PARADAS
from .seguridad import ip_real


def ahora_ts():
    """Segundos epoch «de ahora» (en pruebas, ?hora=…)."""
    try:
        if DEBUG and request.args.get("hora"):
            return datetime.fromisoformat(request.args["hora"]).replace(tzinfo=TZ).timestamp()
    except RuntimeError:   # fuera de una petición (hilo de recordatorios)
        pass
    return time.time()


def ahora_local():
    """Fecha y hora de Canarias, sin zona (en pruebas, ?hora=…)."""
    if DEBUG and request.args.get("hora"):
        return datetime.fromisoformat(request.args["hora"])
    return datetime.now(TZ).replace(tzinfo=None)


def a_ts(dt):
    return dt.replace(tzinfo=TZ).timestamp()


def client_ip():
    """IP real del visitante (ver seguridad.ip_real). Sin esto todos comparten la IP del proxy de Render."""
    ip = ip_real(request, DETRAS_DE_PROXY)
    if LOG_IP:
        current_app.logger.warning("client_ip=%s remote_addr=%s CF=%s XFF=%s", ip, request.remote_addr,
                                   request.headers.get("CF-Connecting-IP"), request.headers.get("X-Forwarded-For"))
    return ip


# La comunidad («me he subido», reputación, recordatorios, favoritas): base de datos detrás (app/db.py)
COM = Comunidad(
    {pid: set(gr["ids"]) for pid, gr in PARADAS.items()},
    {pid: (gr["lat"], gr["lon"]) for pid, gr in PARADAS.items()},
    TZ, reloj=ahora_ts)


def autenticar():
    """Dispositivo que firma la petición (cabeceras X-Dispositivo, X-Ts, X-Nonce, X-Firma). Error 401 si no vale."""
    h = request.headers
    try:
        dev, ts, nonce, firma = h["X-Dispositivo"], h["X-Ts"], h["X-Nonce"], h["X-Firma"]
    except KeyError:
        raise Error("faltan cabeceras de autenticación", 401)
    return COM.verificar(dev, ts, nonce, firma, mensaje(request.method, request.path, ts, nonce, request.get_data(as_text=True)), ahora_ts())
