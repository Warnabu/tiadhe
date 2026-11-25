"""«Avísame 5 min antes»: un hilo revisa cada 30 s los avisos pendientes y manda la notificación push a tiempo."""
import logging
import threading
import time
from datetime import datetime

from . import guaguas as g
from .config import TZ
from .contexto import COM, ahora_ts
from .cuenta import send_fcm_push
from .datos import BASE, HORARIOS, PARADAS

log = logging.getLogger("guaguas")


def salida(pid, viaje, ahora_l):
    """La salida 'viaje' tal como la ve ahora la parada pid (con correcciones en vivo), o None si ya pasó."""
    gr = PARADAS.get(pid)
    if not gr:
        return None, None
    for r in g.proximas(dict(nombre=gr["nombre"], ids=gr["ids"]), ahora_l, HORARIOS, BASE, com=COM, gracia=3, modo="dinamico"):
        if r["viaje"] == viaje:
            return r, gr
    return None, gr


def revisar_recordatorios():
    """Envía los avisos cuya guagua está a ≤5 min (con la hora más temprana posible, para no llegar tarde)."""
    ahora, ahora_l = ahora_ts(), datetime.now(TZ).replace(tzinfo=None)
    for rec in COM.recordatorios_pendientes():
        r, gr = salida(rec["parada"], rec["viaje"], ahora_l)
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


def _bucle():
    while True:
        time.sleep(30)
        try:
            revisar_recordatorios()
        except Exception:
            log.exception("revisando recordatorios")


def iniciar():
    threading.Thread(target=_bucle, daemon=True, name="recordatorios").start()
