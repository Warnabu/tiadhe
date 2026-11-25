"""Comunidad: alta de dispositivos, «me he subido a esta guagua» y la actividad del día (tiempo real, en pruebas)."""
import base64

from flask import Blueprint, jsonify, request

from .. import guaguas as g
from ..comunidad import Comunidad, Error, km_ll, mensaje
from ..contexto import COM, a_ts, ahora_ts, autenticar, client_ip
from ..datos import BASE, HORARIOS, PARADAS
from ..tablero import limpiar_cache

bp = Blueprint("comunidad", __name__)
_estadisticas = {}


@bp.post("/api/registro")
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


@bp.get("/api/yo")
def api_yo():
    dev = autenticar()
    return jsonify(ok=True, dispositivo=dev, perfil=COM.perfil(dev, ahora_ts()))


@bp.post("/api/subida")
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
    limpiar_cache()
    _estadisticas.clear()
    n = pred.get("restantes", 0)
    paradas = f"{n} parada{'s' if n != 1 else ''} siguiente{'s' if n != 1 else ''}"
    msg = (f"¡Confirmado! Gracias a ti, quien espera en las {paradas} ya ve la hora real." if res["estado"] == "confirmada"
           else f"¡Gracias! En cuanto otra persona lo confirme, la hora se corregirá en las {paradas}."
           + ("" if res["gps"] else " (Con la ubicación activada tu aviso pesa más.)"))
    return jsonify(ok=True, estado=res["estado"], mensaje=msg, restantes=n, perfil=COM.perfil(dev, ahora))


@bp.get("/api/estadisticas")
def api_estadisticas():
    """Actividad de hoy en toda la isla (avisos, personas, guaguas confirmadas). Cacheada 1 minuto."""
    ahora = ahora_ts()
    if not _estadisticas or ahora - _estadisticas["t"] > 60:
        _estadisticas.update(t=ahora, d=COM.estadisticas_hoy(ahora))
    return jsonify(ok=True, **_estadisticas["d"])
