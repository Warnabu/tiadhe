"""Paradas y líneas: lo que pinta el mapa y lo que sale al tocar una parada o una línea."""
import math
from datetime import timedelta

from flask import Blueprint, jsonify, request

from .. import guaguas as g
from ..comunidad import Error
from ..contexto import ahora_local
from ..datos import BANDAS, BASE, COLOR, HORARIOS, OSM, PARADA_DE, PARADAS, lugares, nombre_corto, partes
from ..tablero import tablero

bp = Blueprint("paradas", __name__)


def _modo():
    m = request.args.get("modo", "dinamico")
    return m if m in ("dinamico", "estatico") else "dinamico"


@bp.get("/api/paradas")
def api_paradas():
    return jsonify([dict(id=p["id"], nombre=p["nombre"], lat=round(p["lat"], 5), lon=round(p["lon"], 5), lineas=sorted(p["lineas"]))
                    for p in PARADAS.values()])


@bp.get("/api/parada/<pid>")
def api_parada(pid):
    gr = PARADAS.get(pid)
    if not gr:
        raise Error("parada desconocida", 404)
    d = dict(tablero(pid, dict(nombre=gr["nombre"], ids=gr["ids"]), ahora_local(), modo=_modo()))
    d["parada"] = dict(id=pid, nombre=gr["nombre"], lineas=sorted(gr["lineas"]))
    return jsonify(d)


@bp.get("/api/cerca")
def api_cerca():
    """Tablero de un punto cualquiera (una parada de OpenStreetMap que no es de las líneas)."""
    if "lat" not in request.args or "lon" not in request.args:
        raise Error("faltan lat/lon", 400)
    try:
        lat, lon = float(request.args["lat"]), float(request.args["lon"])
    except ValueError:
        raise Error("lat/lon no son números", 400)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise Error("lat/lon fuera de rango", 400)
    nombre = request.args.get("nombre", "Parada")[:80]
    d = dict(tablero(("osm", round(lat, 5), round(lon, 5)), dict(nombre=nombre, lat=lat, lon=lon), ahora_local(), modo=_modo()))
    d["parada"] = dict(id="osm", nombre=nombre, lineas=[], aprox=True)
    return jsonify(d)


@bp.get("/api/lineas")
def api_lineas():
    if request.args.get("bandas"):  # la web: tramos con desplazamiento lateral, sin el trazado completo
        return jsonify([dict(num=n, nombre=HORARIOS[n]["nombre"], color=COLOR[n], tramos=BANDAS.get(n, [])) for n in BASE])
    return jsonify([dict(num=n, nombre=HORARIOS[n]["nombre"], color=COLOR[n], calibrada="duracion_min" in l, real=bool(l.get("real")),
                         trazado=(partes(l)[0] if len(partes(l)) == 1 else partes(l)))
                    for n, l in BASE.items()])


@bp.get("/api/linea/<num>")
def api_linea(num):
    """Paradas de una línea en orden (sentido ida) y sus próximas salidas de cabecera en cada sentido."""
    if num not in BASE:
        raise Error("línea desconocida", 404)
    paradas = []
    for p in BASE[num]["paradas"]:
        pid = PARADA_DE.get(p.get("id")) if "opt" not in p else None
        if pid and (not paradas or paradas[-1]["id"] != pid):
            paradas.append(dict(id=pid, nombre=nombre_corto(pid)))
    ahora = ahora_local()
    salidas = {}
    for sent in ("ida", "vuelta"):
        lista = []
        for dia_off in (0, 1):
            fecha = (ahora + timedelta(days=dia_off)).date()
            horas = sorted({x["h"] for x in HORARIOS[num]["salidas"]
                            if x["s"] == sent and g.tipo_dia(fecha) in x["d"].split(",") and (dia_off or x["h"] >= f"{ahora:%H:%M}")})
            lista += [h + (" (mañana)" if dia_off else "") for h in horas]
        salidas[sent] = lista[:5]
    h = HORARIOS[num]
    return jsonify(ok=True, num=num, nombre=h["nombre"], color=COLOR[num], origen=h.get("origen"), destino=h.get("destino"),
                   paradas=paradas, salidas=salidas)


@bp.get("/api/lugares")
def api_lugares():
    return jsonify(lugares())


@bp.get("/api/osm")
def api_osm():
    return jsonify([dict(n=p["nombre"], lat=p["lat"], lon=p["lon"]) for p in OSM])
