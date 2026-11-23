#!/usr/bin/env python3
"""Revisa datos/paradas_reales.json y señala los trazados sospechosos.

    python herramientas/revisar_trazados.py

Para cada línea informa de:
  · tramos largos  (segmento > 400 m: casi seguro una curva cortada en diagonal)
  · puntas         (retroceso brusco >150°: el trazado va y vuelve sobre sí mismo)
  · paradas        (paradas a más de 60 m del trazado dibujado)
Es de solo lectura: no modifica nada.
"""
import json, math
from pathlib import Path

DATOS = Path(__file__).resolve().parent.parent / "datos"


def km(a, b):
    p = math.pi / 180
    h = math.sin((b[0] - a[0]) * p / 2) ** 2 + math.cos(a[0] * p) * math.cos(b[0] * p) * math.sin((b[1] - a[1]) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def angulo(a, b, c):
    k = math.cos(math.radians(b[0]))
    v1, v2 = ((a[1] - b[1]) * k, a[0] - b[0]), ((c[1] - b[1]) * k, c[0] - b[0])
    n = math.hypot(*v1) * math.hypot(*v2)
    return 180 if n == 0 else math.degrees(math.acos(max(-1, min(1, (v1[0] * v2[0] + v1[1] * v2[1]) / n))))


def dist_a_traza(t, lat, lon):
    k, mejor = math.cos(math.radians(lat)), 1e9
    for (ay, ax), (by, bx) in zip(t, t[1:]):
        dx, dy = (bx - ax) * k, by - ay
        L = dx * dx + dy * dy
        u = 0 if L == 0 else max(0, min(1, (((lon - ax) * k) * dx + (lat - ay) * dy) / L))
        mejor = min(mejor, km((ay + u * dy, ax + u * (bx - ax)), (lat, lon)))
    return mejor


def main():
    datos = json.loads((DATOS / "paradas_reales.json").read_text(encoding="utf-8"))
    print(f"{'línea':6}{'puntos':>8}{'m/seg':>8}{'largos':>8}{'puntas':>8}{'paradas>60m':>13}")
    for num, v in datos.items():
        t = v["trazado"][0] if v.get("trazado") else []
        if len(t) < 3:
            print(f"{num:6}  sin trazado"); continue
        seg = [km(a, b) for a, b in zip(t, t[1:])]
        largos = [i for i, d in enumerate(seg) if d > 0.4]
        puntas = [i for i in range(1, len(t) - 1) if angulo(t[i - 1], t[i], t[i + 1]) < 30 and seg[i - 1] > 0.02]
        lejos = [p["n"] for p in v.get("paradas", []) if dist_a_traza(t, p["lat"], p["lon"]) > 0.06]
        print(f"{num:6}{len(t):8}{sum(seg) / len(seg) * 1000:8.0f}{len(largos):8}{len(puntas):8}{len(lejos):13}")
        for i in puntas[:3]:
            print(f"        punta en ({t[i][0]:.5f}, {t[i][1]:.5f})")
        if lejos:
            print(f"        paradas lejos: {', '.join(lejos[:3])}")


if __name__ == "__main__":
    main()
