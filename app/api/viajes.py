"""Cómo llegar: opciones de un sitio a otro (/api/ruta) y su dibujo en el mapa (/api/camino)."""
import json

from flask import Blueprint, jsonify, request

from ..comunidad import Error
from ..config import RADIO_PARADA, RADIO_PUNTO
from ..contexto import ahora_local
from ..datos import BASE, CAMINOS, COLOR, PARADAS, PLAN, nombre_corto

bp = Blueprint("viajes", __name__)


def _extremo(valor, nombre):
    """Parada ('id') o punto ('@lat,lon') → (nombre, {parada: minutos andando}, punto o None)."""
    if valor in PARADAS:
        v = PARADAS[valor]
        return nombre_corto(valor), {valor: 0, **{k: m for k, m in PLAN.alrededor(v["lat"], v["lon"], RADIO_PARADA).items() if k != valor}}, None
    try:
        lat, lon = map(float, valor.lstrip("@").split(","))
        assert valor.startswith("@") and 27.6 < lat < 29.0 and -14.7 < lon < -13.3
    except (ValueError, AssertionError):
        raise Error("parada o punto desconocido", 404)
    paradas = PLAN.alrededor(lat, lon, RADIO_PUNTO, minimo=3)
    if not paradas:
        raise Error("no hay paradas de guagua cerca de ese punto", 404)
    nombre = "".join(ch for ch in (nombre or "") if ch.isprintable())[:60].strip() or "tu ubicación"
    return nombre, paradas, [round(lat, 5), round(lon, 5)]


@bp.get("/api/ruta")
def api_ruta():
    """Cómo ir de 'desde' a 'hasta' (paradas o puntos '@lat,lon'): directas, con transbordos y andando lo justo."""
    o, d = request.args.get("desde", ""), request.args.get("hasta", "")
    if o == d:
        raise Error("el origen y el destino son el mismo sitio")
    no, origenes, po = _extremo(o, request.args.get("dn"))
    nd, destinos, pd = _extremo(d, request.args.get("hn"))
    ahora = ahora_local()
    opciones = []
    for it in PLAN.itinerarios(origenes, destinos, ahora):
        salir, llegar, nbus, _ = PLAN.resumen(it)
        buses = [p for p in it if p["tipo"] == "bus"]
        pasos, camino, prev = [], [], None
        for k, p in enumerate(it):
            if p["tipo"] == "pie":
                desde = p["desde"] or (o if po is None else None)
                hasta = p["hasta"] or (d if pd is None else None)
                pasos.append(dict(tipo="pie", desde=nombre_corto(desde) if desde else no, hasta=nombre_corto(hasta) if hasta else nd,
                                  minutos=p["minutos"], salir=f"{salir:%H:%M}" if k == 0 else None))
                camino.append(["pie", desde or f"@{po[0]},{po[1]}", hasta or f"@{pd[0]},{pd[1]}"])
                continue
            espera = round((p["sale"] - prev["llega"]).total_seconds() / 60) if prev else None
            pasos.append(dict(tipo="bus", linea=p["linea"], destino=p["destino"], desde=nombre_corto(p["desde"]),
                              hasta=nombre_corto(p["hasta"]), sale=f"{p['sale']:%H:%M}", llega=f"{p['llega']:%H:%M}",
                              espera=espera, nota=p["nota"]))
            camino.append([p["linea"], p["viaje"].split("|")[2], p["desde"], p["hasta"]])
            prev = p
        opciones.append(dict(sale=f"{buses[0]['sale']:%H:%M}", salir=f"{salir:%H:%M}", llega=f"{llegar:%H:%M}",
                             manana=salir.date() > ahora.date(), transbordos=nbus - 1,
                             duracion=round((llegar - salir).total_seconds() / 60), pasos=pasos, camino=camino))
    return jsonify(ok=True, desde=dict(nombre=no, punto=po), hasta=dict(nombre=nd, punto=pd), opciones=opciones)


def _lugar(x):
    """[lat, lon] de una parada o de un punto '@lat,lon' de la isla (None si no vale)."""
    if not isinstance(x, str):
        return None
    if x in PARADAS:
        return [PARADAS[x]["lat"], PARADAS[x]["lon"]]
    try:
        lat, lon = map(float, x.lstrip("@").split(","))
        return [lat, lon] if x.startswith("@") and 27.6 < lat < 29.0 and -14.7 < lon < -13.3 else None
    except ValueError:
        return None


@bp.get("/api/camino")
def api_camino():
    """Dibujo de un camino de /api/ruta: t = JSON [[línea, sentido, desde, hasta] | ["pie", desde, hasta], …]."""
    try:
        t = json.loads(request.args.get("t", ""))
        assert isinstance(t, list) and 1 <= len(t) <= 6 and all(isinstance(x, list) for x in t)
    except (ValueError, AssertionError):
        raise Error("camino no válido")
    res = []
    for x in t:
        if len(x) == 3 and x[0] == "pie" and _lugar(x[1]) and _lugar(x[2]):
            a, b = _lugar(x[1]), _lugar(x[2])
            res.append(dict(tipo="pie", p=[[a, b]], paradas=[y for y in x[1:] if y in PARADAS]))
        elif len(x) == 4 and x[0] in BASE and x[1] in ("ida", "vuelta") and x[2] in PARADAS and x[3] in PARADAS:
            tr = CAMINOS.tramo(x[0], x[1], x[2], x[3])
            if not tr:
                raise Error("esa línea no va de una parada a la otra", 404)
            res.append(dict(tipo="bus", linea=x[0], color=COLOR[x[0]], **tr))
        else:
            raise Error("camino no válido")
    return jsonify(ok=True, tramos=res)
