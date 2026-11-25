"""Tablero de una parada: sus próximas guaguas agrupadas por destino (lo que se ve al tocar una parada)."""
from . import guaguas as g
from .config import DIAS
from .contexto import COM
from .datos import BASE, HORARIOS

_cache = {}


def limpiar_cache():
    """Tras un aviso «me he subido» las horas cambian: se vuelve a calcular."""
    _cache.clear()


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
