#!/usr/bin/env python3
"""Localiza las paradas de guagua tal como las pinta el mapa de OpenStreetMap (el icono azul del autobús).

    python herramientas/paradas_mapa.py        # → datos/paradas_mapa.json

Las paradas de OpenStreetMap son las que ve el usuario en el mapa de la app. Como desde el entorno de trabajo no se
puede consultar la base de datos de OpenStreetMap (Overpass), se leen de las propias imágenes del mapa
(tile.openstreetmap.org, zoom 17, ~1 m por píxel): se busca el icono del autobús y su centro es la parada.

Solo se descargan las teselas alrededor de las paradas de las líneas (datos/paradas_reales.json) y se guardan en
datos/.cache_osrm/teselas/ (no se vuelven a pedir).
"""
import json, math, sys, time, urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

DATOS = Path(__file__).resolve().parent.parent / "datos"
CACHE = DATOS / ".cache_osrm" / "teselas"
Z = 17
RADIO_M = 300
# icono del autobús (zoom 17): 1 = azul
PLANTILLA = np.array([[c == "#" for c in f] for f in [
    ".######.",
    "##....##",
    "########",
    "#......#",
    "#......#",
    "#......#",
    "########",
    "#.####.#",
    "########",
    ".#....#.",
]])


def a_pixel(la, lo, z=Z):
    n = 2 ** z
    return (lo + 180) / 360 * n * 256, (1 - math.asinh(math.tan(math.radians(la))) / math.pi) / 2 * n * 256


def a_latlon(x, y, z=Z):
    n = 2 ** z * 256
    lo = x / n * 360 - 180
    la = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return la, lo


def tesela(x, y):
    f = CACHE / f"{Z}_{x}_{y}.png"
    if not f.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(f"https://tile.openstreetmap.org/{Z}/{x}/{y}.png",
                                     headers={"User-Agent": "guaguas-ftv-herramientas/1.0 (paradas de guagua)"})
        for intento in range(3):
            try:
                f.write_bytes(urllib.request.urlopen(req, timeout=30).read())
                break
            except Exception:
                time.sleep(2 + 3 * intento)
        time.sleep(0.25)
    return np.array(Image.open(f).convert("RGB")).astype(int)


def iconos(img):
    """Centros (x, y en píxeles de la tesela) de los iconos de autobús."""
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    azul = (b > 180) & (r < 80) & (g > 100) & (g < 190)
    h, w = PLANTILLA.shape
    res = []
    ys, xs = np.nonzero(azul)
    vistos = set()
    for y0, x0 in zip(ys, xs):
        # la esquina superior izquierda del icono es el 2º píxel azul de su primera fila
        oy, ox = y0, x0 - 1
        if (oy, ox) in vistos or oy + h > azul.shape[0] or ox < 0 or ox + w > azul.shape[1]:
            continue
        ventana = azul[oy:oy + h, ox:ox + w]
        aciertos = (ventana == PLANTILLA).mean()
        if aciertos >= 0.93:
            # que no sea parte de algo más grande (texto, la «P»): borde de 2 px casi sin azul
            marco = azul[max(0, oy - 2):oy + h + 2, max(0, ox - 2):ox + w + 2].sum() - ventana.sum()
            if marco <= 3:
                vistos.add((oy, ox))
                res.append((ox + (w - 1) / 2, oy + 4.5))   # el punto de la parada: centro del icono
    return res


def hay_icono(la, lo):
    """¿Pinta el mapa el icono del autobús en ese punto (±4 px)? Comparación permisiva: solo se busca ahí."""
    x, y = a_pixel(la, lo)
    tx, ty = int(x // 256), int(y // 256)
    if not (CACHE / f"{Z}_{tx}_{ty}.png").exists():
        return None
    grande = np.zeros((768, 768, 3), int)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            f = CACHE / f"{Z}_{tx + dx}_{ty + dy}.png"
            if f.exists():
                grande[(dy + 1) * 256:(dy + 2) * 256, (dx + 1) * 256:(dx + 2) * 256] = np.array(Image.open(f).convert("RGB"))
    r, g, b = grande[..., 0], grande[..., 1], grande[..., 2]
    azul = (b > 170) & (r < 110) & (g > 90) & (g < 200)
    cx, cy = x - (tx - 1) * 256, y - (ty - 1) * 256
    h, w = PLANTILLA.shape
    mejor = 0.0
    for oy in range(int(cy - 4.5) - 4, int(cy - 4.5) + 5):
        for ox in range(int(cx - 3.5) - 4, int(cx - 3.5) + 5):
            v = azul[oy:oy + h, ox:ox + w]
            if v.shape == PLANTILLA.shape:
                mejor = max(mejor, (v & PLANTILLA).sum() / PLANTILLA.sum() - (v & ~PLANTILLA).sum() / (~PLANTILLA).sum())
    return mejor >= 0.75


def main():
    reales = json.loads((DATOS / "paradas_reales.json").read_text(encoding="utf-8"))
    teselas = set()
    for l in reales.values():
        for p in l["paradas"]:
            x, y = a_pixel(p["lat"], p["lon"])
            r = RADIO_M / (40075016 * math.cos(math.radians(p["lat"])) / 2 ** Z / 256)   # en píxeles
            for tx in range(int((x - r) // 256), int((x + r) // 256) + 1):
                for ty in range(int((y - r) // 256), int((y + r) // 256) + 1):
                    teselas.add((tx, ty))
    print(f"{len(teselas)} teselas ({sum((CACHE / f'{Z}_{x}_{y}.png').exists() for x, y in teselas)} ya en caché)")
    encontradas = {}
    for k, (tx, ty) in enumerate(sorted(teselas)):
        for cx, cy in iconos(tesela(tx, ty)):
            la, lo = a_latlon(tx * 256 + cx, ty * 256 + cy)
            encontradas[(round(la, 6), round(lo, 6))] = True
        if k % 200 == 0:
            print(f"  {k}/{len(teselas)} · {len(encontradas)} paradas")
    # las paradas conocidas de OpenStreetMap (datos/paradas_osm.json, posición exacta del nodo) que el mapa sigue
    # pintando; el detector de arriba se salta iconos sobre carreteras de color o tapados por textos
    osm = json.loads((DATOS / "paradas_osm.json").read_text(encoding="utf-8"))
    confirmadas = 0
    for p in osm:
        if hay_icono(p["lat"], p["lon"]):
            confirmadas += 1
            if not any(abs(la - p["lat"]) < 8e-5 and abs(lo - p["lon"]) < 8e-5 for la, lo in encontradas):
                encontradas[(round(p["lat"], 6), round(p["lon"], 6))] = True
    print(f"{confirmadas} de {len(osm)} paradas conocidas de OpenStreetMap siguen en el mapa")
    res = [dict(lat=la, lon=lo) for la, lo in sorted(encontradas)]
    (DATOS / "paradas_mapa.json").write_text(json.dumps(res, separators=(",", ":")))
    print(f"{len(res)} paradas en el mapa → datos/paradas_mapa.json")


if __name__ == "__main__":
    sys.exit(main())
