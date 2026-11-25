"""Datos que se cargan al arrancar (horarios, líneas, paradas del mapa) y lo que se calcula a partir de ellos una vez.

Todo es de solo lectura mientras el servidor está en marcha: para cambiar algo se cambian los archivos de datos/ (con
las herramientas) y se reinicia.
"""
import json

from . import bandas
from . import guaguas as g
from .paradas import agrupar
from .planificador import Caminos, Planificador

HORARIOS, BASE = g.cargar()
COLOR = {num: g.COLORES[i % len(g.COLORES)] for i, num in enumerate(BASE)}

_osm = g.DATOS / "paradas_osm.json"
OSM = [p for p in json.loads(_osm.read_text(encoding="utf-8")) if p["nombre"]] if _osm.exists() else []

PARADAS = agrupar(BASE, HORARIOS)                                              # puntos del mapa
PARADA_DE = {i: pid for pid, gr in PARADAS.items() for i in gr["ids"]}         # parada de línea → punto del mapa
PLAN = Planificador(HORARIOS, BASE, PARADA_DE, PARADAS)                         # cómo llegar (con transbordos)
CAMINOS = Caminos(BASE, PARADA_DE, PARADAS)                                     # el camino elegido, en el mapa


def nombre_corto(pid):
    """Nombre del punto sin el « · L01, L04» que distingue los repetidos."""
    return PARADAS[pid]["nombre"].split(" · L")[0]


def lugares():
    """Núcleos (Las Playitas, Antigua…) para el buscador, aunque ninguna parada se llame así."""
    acc = {}
    for l in BASE.values():
        for p in l["esqueleto"]:
            acc.setdefault(p["n"], (p["lat"], p["lon"]))
    return [dict(n=n, lat=la, lon=lo) for n, (la, lo) in sorted(acc.items())]


def partes(l):
    """Trazado de una línea como lista de tramos [[lat, lon], …]."""
    tr = l.get("trazado")
    if not tr:
        return [[[p["lat"], p["lon"]] for p in l["paradas"] if "opt" not in p]]
    return [tr] if isinstance(tr[0][0], (int, float)) else tr


BANDAS = bandas.calcular({n: partes(l) for n, l in BASE.items()})   # líneas en paralelo donde comparten calle
