#!/usr/bin/env python3
"""Comprueba los datos que usa la app (solo lectura: no cambia nada). Ejecútalo después de tocar paradas o rutas.

    python herramientas/verificar.py

  1. Qué guaguas pasan por cada parada: ninguna línea puede aparecer en una parada sin que salga nunca una guagua.
  2. Contra los mapas de Tiadhe (datos/.cache_osrm/kml/): cada parada de Tiadhe (línea y sentido) debe tener en la app un
     punto a ≤ 80 m con esa línea y guaguas en ese sentido. Las que no, se listan con el punto más cercano.
  3. Trazos: tramos fuera de la calle (según la red de calles en caché) y paradas lejos (> 30 m) del trazo de su línea.
"""
import os
import sys
from collections import defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "herramientas"))
os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "1")

from app import guaguas as g  # noqa: E402
from app.datos import BASE, HORARIOS, PARADAS  # noqa: E402
from ajustar_rutas import m, proyectar_seg  # noqa: E402
from colocar_paradas import sentidos_tiadhe  # noqa: E402


def main():
    sale, pasa = defaultdict(set), defaultdict(set)   # id de parada de línea → sentidos en que sale / pasa alguna guagua
    for num, h in HORARIOS.items():
        for x in h["salidas"]:
            seq = g.recorrido(num, x["s"], x, BASE)
            for p in seq[:-1]:
                sale[p["id"]].add(x["s"])
            for p in seq:
                pasa[p["id"]].add(x["s"])

    print("1) Paradas con una línea que no tiene ninguna salida")
    malas = [(v["nombre"], num) for v in PARADAS.values() for num in sorted(v["lineas"])
             if not set().union(*[sale[i] for i in v["ids"] if i.split(":")[0] == num])]
    print(f"   {len(malas)}", *[f"\n   - {n} (línea {l})" for n, l in malas])

    print("2) Paradas de Tiadhe sin punto de la app con esa línea y sentido (≤ 80 m)")
    total, faltan = 0, []
    for num, l in BASE.items():
        ps = sorted((p for p in l["paradas"] if "d" not in p and "opt" not in p), key=lambda p: p["k"])
        T = sentidos_tiadhe(num, (ps[0].get("lat0", ps[0]["lat"]), ps[0].get("lon0", ps[0]["lon"])),
                            (ps[-1].get("lat0", ps[-1]["lat"]), ps[-1].get("lon0", ps[-1]["lon"])), avisar=False) or {}
        for nom, lst in T.items():
            for la, lo, ss in lst:
                for sn in ss:
                    total += 1
                    if not any(m((la, lo), (v["lat"], v["lon"])) <= 80 and any(i.split(":")[0] == num and sn in pasa[i] for i in v["ids"])
                               for v in PARADAS.values()):
                        cerca = min(((m((la, lo), (v["lat"], v["lon"])), v["nombre"]) for v in PARADAS.values() if num in v["lineas"]),
                                    default=(0, "-"))
                        faltan.append(f"línea {num} {sn:6} {nom[:28]:28} → la más cercana con la {num}: {cerca[1][:30]} a {cerca[0]:.0f} m")
    print(f"   {len(faltan)} de {total}", *[f"\n   - {x}" for x in faltan])

    print("3) Trazos")
    for num, l in BASE.items():
        t = l["trazado"][0] if l.get("trazado") else []
        if not t:
            continue
        lejos = []
        for p in l["paradas"]:
            if "d" in p or "opt" in p or p.get("solo") == "vuelta":
                continue
            q = (p["lat"], p["lon"])
            d = min((proyectar_seg(q, a, b)[0] for a, b in zip(t, t[1:])
                     if min(a[0], b[0]) - .003 < q[0] < max(a[0], b[0]) + .003 and min(a[1], b[1]) - .003 < q[1] < max(a[1], b[1]) + .003),
                    default=1e9)
            if d > 30:
                lejos.append(f"{p['n']} ({d:.0f} m)" if d < 1e8 else p["n"])
        if lejos:
            print(f"   línea {num}: paradas lejos del trazo: {', '.join(lejos)}")
    print("   (tramos fuera de la calle: python herramientas/rutas_osrm.py --dry-run imprime su informe por línea)")


if __name__ == "__main__":
    main()
