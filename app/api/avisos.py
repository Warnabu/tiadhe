"""Avisos al usuario: «avísame 5 min antes», su token de notificaciones y sus paradas favoritas."""
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from .. import guaguas as g
from ..avisos import salida
from ..comunidad import Error
from ..config import MINUTOS_AVISO, TZ
from ..contexto import COM, ahora_ts, autenticar
from ..cuenta import send_fcm_push, uid_sesion
from ..datos import PARADAS

bp = Blueprint("avisos", __name__)


@bp.get("/api/recordatorios")
def api_recordatorios():
    dev = autenticar()
    return jsonify(ok=True, recordatorios=COM.recordatorios_de(dev))


@bp.post("/api/recordatorio")
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
    r, gr = salida(pid, viaje, ahora_l)
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


@bp.post("/api/register-fcm-token")
def api_register_fcm_token():
    dev = autenticar()
    fcm_token = (request.get_json(silent=True) or {}).get("fcm_token")
    if not fcm_token:
        raise Error("falta fcm_token", 400)
    if not isinstance(fcm_token, str) or not (20 <= len(fcm_token) <= 4096):
        raise Error("fcm_token no válido", 400)
    COM.guardar_fcm_token(dev, fcm_token)
    return jsonify(ok=True)


@bp.post("/api/test-push")
def api_test_push():
    dev = autenticar()
    fcm_token = COM.obtener_fcm_token(dev)
    if not fcm_token:
        raise Error("no hay token FCM registrado", 400)
    ok, motivo = send_fcm_push(fcm_token, "Guaguas FTV", "Notificación de prueba desde el backend")
    if motivo == "token_invalido":
        COM.borrar_fcm_token(dev)
    return jsonify(ok=ok, motivo=motivo), (200 if ok else 502)


@bp.get("/api/favoritas")
def api_favoritas():
    return jsonify(ok=True, paradas=[p for p in COM.favoritas(uid_sesion()) if p in PARADAS])


@bp.post("/api/favoritas")
def api_favoritas_poner():
    uid = uid_sesion()
    d = request.get_json(silent=True) or {}
    pid = str(d.get("parada", ""))[:40]
    if pid not in PARADAS:
        raise Error("parada desconocida", 404)
    COM.poner_favorita(uid, pid, bool(d.get("si", True)), ahora_ts())
    return jsonify(ok=True, paradas=[p for p in COM.favoritas(uid) if p in PARADAS])
