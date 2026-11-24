#!/usr/bin/env python3
"""Repasa los trazados ya ajustados (datos/paradas_reales.json) y quita lo que no es una calle de verdad.

    python herramientas/limpiar_trazados.py              # todas las líneas
    python herramientas/limpiar_trazados.py 01 09        # solo esas
    python herramientas/limpiar_trazados.py --dry-run    # informa sin escribir

Se ejecuta después de ajustar_rutas.py y usa su misma red de calles (datos/.cache_osrm/red/). Dos arreglos:

  1. Tramos fuera de la calle. Donde el mapa original de Tiadhe corta en línea recta por el campo (y el ajuste conservó
     ese dibujo porque no encontró calle), el tramo se cambia por el camino más corto por calles reales entre los dos
     puntos en los que el trazado vuelve a estar en la calle. Solo si ese camino no es mucho más largo que el dibujo:
     si lo es, se deja y se avisa (puede ser una calle nueva que aún no está en OpenStreetMap).
  2. Ramales de ida y vuelta. Si el trazado se mete por una calle y vuelve por ella al mismo punto sin que haya una
     parada de la línea en la punta (p. ej. el ramal sin salida del enlace de La Lajita), se quita. Solo los que
     inventó el ajuste: si el mapa original de Tiadhe también entra y vuelve (un desvío de verdad), se queda.
  3. Picos: un vértice en el que el trazado da media vuelta en seco se quita.
"""
import argparse, heapq, json, math, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ajustar_rutas import DATOS, Red, acumulado, m, proyectar_seg, teselas_necesarias  # noqa: E402

FUERA_M = 25         # una muestra a más de esto de cualquier calle está "fuera de la calle"
MUESTRA_M = 10
EN_CALLE_M = 12      # los extremos de un tramo arreglado tienen que estar en la calle
MAS_LARGO = 1.35     # el camino por calles puede ser como mucho así de largo que el dibujo (+150 m)
RAMAL_MAX_M = 1200   # longitud máxima de un ramal de ida y vuelta
RAMAL_MIN_M = 40
PARADA_PUNTA_M = 60  # con una parada de la línea en el ramal, el ramal se queda (la guagua entra a por ella)
ORIGINAL_M = 60      # ni si el trazado original de Tiadhe llega a la punta (solo se quitan los ramales que inventó el ajuste)
PICO_GRADOS = 150    # media vuelta en seco en un vértice: es un pico del ajuste, no una calle
NUCLEO_M = 300       # ni si cerca de la punta hay un núcleo de la línea (lineas_base.json, coordenadas aproximadas:
                     # ahí están los desvíos de las variantes, p. ej. Las Playitas o Triquivijate)


def dist_calle(red, p):
    """(distancia a la calle útil más cercana, arista, punto proyectado)."""
    mejor = (1e9, None, None)
    for e in red.celdas.get((int(p[0] / 0.001), int(p[1] / 0.001)), ()):
        u, v, _, _ = red.aristas[e]
        d, _, q = proyectar_seg(p, red.pos[u], red.pos[v])
        if d < mejor[0]:
            mejor = (d, e, q)
    return mejor


def camino_calles(red, a, b, limite):
    """Camino más corto por la red entre los puntos a y b (sobre sus calles). [(lat, lon)] o None."""
    da, ea, qa = dist_calle(red, a)
    db, eb, qb = dist_calle(red, b)
    if ea is None or eb is None:
        return None
    if ea == eb:
        return [qa, qb]
    ua, va = red.aristas[ea][:2]
    ub, vb = red.aristas[eb][:2]
    dist, prev, pq = {}, {}, []
    for n in (ua, va):   # salir de a hacia cualquiera de los dos extremos de su calle
        d0 = m(qa, red.pos[n])
        if d0 < dist.get(n, 1e18):
            dist[n] = d0
            heapq.heappush(pq, (d0, n))
    fin = {ub: m(red.pos[ub], qb), vb: m(red.pos[vb], qb)}
    mejor, nodo_fin = 1e18, None
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, 1e18) or d > limite or d >= mejor:
            continue
        if u in fin and d + fin[u] < mejor:
            mejor, nodo_fin = d + fin[u], u
        for e in red.sal.get(u, ()):
            _, v, L, _ = red.aristas[e]
            if d + L < dist.get(v, 1e18):
                dist[v], prev[v] = d + L, u
                heapq.heappush(pq, (d + L, v))
    if nodo_fin is None or mejor > limite:
        return None
    nodos, x = [], nodo_fin
    while x in prev:
        nodos.append(x)
        x = prev[x]
    nodos.append(x)
    return [qa] + [red.pos[n] for n in reversed(nodos)] + [qb]


def arreglar_fuera(red, t, log):
    """Cambia los tramos fuera de la calle por el camino por calles. Devuelve la polilínea nueva."""
    fuera = []
    for a, b in zip(t, t[1:]):
        n = max(1, int(m(a, b) / MUESTRA_M))
        fuera.append(any(dist_calle(red, (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n))[0] > FUERA_M
                         for k in range(n + 1)))
    # tramos [a, b] (índices de puntos) con algún segmento fuera, ampliados hasta puntos que estén en la calle
    tramos, i = [], 0
    while i < len(fuera):
        if not fuera[i]:
            i += 1
            continue
        j = i
        while j < len(fuera) and fuera[j]:
            j += 1
        a, b = i, j
        while a > 0 and dist_calle(red, t[a])[0] > EN_CALLE_M:
            a -= 1
        while b < len(t) - 1 and dist_calle(red, t[b])[0] > EN_CALLE_M:
            b += 1
        if tramos and a <= tramos[-1][1]:
            tramos[-1] = (tramos[-1][0], b)
        else:
            tramos.append((a, b))
        i = max(j, b)
    res, ult = [], 0
    for a, b in tramos:
        res += t[ult:a]
        largo = acumulado(t[a:b + 1])[-1]
        cam = camino_calles(red, t[a], t[b], largo * MAS_LARGO + 150)
        if cam:
            res += [t[a]] + cam[1:-1]
            log(f"    tramo fuera de la calle en {t[a][0]:.5f},{t[a][1]:.5f}: {largo:.0f} m dibujados → "
                f"{acumulado(cam)[-1]:.0f} m por calles")
        else:
            res += t[a:b]
            log(f"    AVISO sin arreglo: tramo fuera de la calle en {t[a][0]:.5f},{t[a][1]:.5f} ({largo:.0f} m)")
        ult = b
    return res + t[ult:]


def paradas_cerca(paradas, p):
    """Paradas (de cualquier línea o marquesinas de OpenStreetMap) en la celda de p y sus vecinas."""
    i, j = int(p[0] / 0.002), int(p[1] / 0.002)
    return [s for di in (-1, 0, 1) for dj in (-1, 0, 1) for s in paradas.get((i + di, j + dj), ())]


def ida_y_vuelta_original(orig, inicio, punta, largo):
    """¿El trazado original también llega a la punta y vuelve al inicio? (si sigue de largo, el ramal es del ajuste)"""
    for i, p in enumerate(orig):
        if m(p, punta) >= ORIGINAL_M:
            continue
        L = 0.0
        for j in range(i + 1, len(orig)):
            L += m(orig[j - 1], orig[j])
            if L > 2 * largo + 200:
                break
            if m(orig[j], inicio) < 60:
                return True
    return False


def quitar_ramales(t, paradas, nucleos, orig, log):
    """Quita idas y vueltas por la misma calle sin parada en la punta."""
    i = 0
    while i < len(t) - 2:
        L, j_ok = 0.0, None
        for j in range(i + 2, len(t)):
            L += m(t[j - 1], t[j])
            if L > RAMAL_MAX_M:
                break
            if L >= RAMAL_MIN_M and m(t[i], t[j]) < 15:
                j_ok = j
        if j_ok is not None:
            tramo = t[i:j_ok + 1]
            acc = acumulado(tramo)
            mitad = acc[-1] / 2
            k = max(range(len(tramo)), key=lambda x: m(tramo[0], tramo[x]))
            ida, vuelta = tramo[:k + 1], tramo[k:]
            # ida y vuelta por la misma calle: cada punto de la vuelta cerca de la ida
            misma = all(min(m(p, q) for q in ida) < 20 for p in vuelta[1:-1]) if len(vuelta) > 2 else True
            alejado = m(tramo[0], tramo[k]) > 30 and acc[k] > mitad * 0.6
            punta = min((m(tramo[k], s) for s in paradas_cerca(paradas, tramo[k])), default=1e9)
            lejos = [p for p in tramo if m(p, tramo[0]) > 30 and m(p, tramo[-1]) > 30]
            con_parada = any(m(p, s) < PARADA_PUNTA_M for p in lejos for s in paradas_cerca(paradas, p))
            con_nucleo = any(m(tramo[k], s) < NUCLEO_M for s in nucleos)
            dibujado = ida_y_vuelta_original(orig, tramo[0], tramo[k], acc[-1])   # Tiadhe lo dibujó así: se queda
            if misma and alejado and not con_parada and not con_nucleo and not dibujado:
                log(f"    ramal de ida y vuelta en {tramo[0][0]:.5f},{tramo[0][1]:.5f}: {acc[-1]:.0f} m sin parada "
                    f"(la más cercana a la punta, a {punta:.0f} m) → quitado")
                t = t[:i + 1] + t[j_ok + 1:]
                continue
        i += 1
    return t


def angulo(a, b, c):
    """Giro (grados) en b: 0 = sigue recto, 180 = media vuelta."""
    c0 = math.cos(math.radians(b[0]))
    v1 = ((b[1] - a[1]) * c0, b[0] - a[0])
    v2 = ((c[1] - b[1]) * c0, c[0] - b[0])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if not n1 or not n2:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))))


def quitar_picos(t, paradas, log):
    """Quita los vértices en los que el trazado da media vuelta en seco (picos y zigzags de un solo punto).
    Junto a una parada no: ahí la media vuelta es la guagua que entra hasta la parada y vuelve (La Pared, Tindaya…)."""
    n0, cambio = len(t), True
    while cambio:
        cambio, res = False, [t[0]]
        for i in range(1, len(t) - 1):
            if angulo(res[-1], t[i], t[i + 1]) > PICO_GRADOS and \
                    not any(m(t[i], s) < PARADA_PUNTA_M for s in paradas_cerca(paradas, t[i])):
                cambio = True
                continue
            res.append(t[i])
        res.append(t[-1])
        t = res
    if len(t) < n0:
        log(f"    {n0 - len(t)} picos quitados")
    return t


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("lineas", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    f = DATOS / "paradas_reales.json"
    datos = json.loads(f.read_text(encoding="utf-8"))
    originales = json.loads((DATOS / "trazados_originales.json").read_text(encoding="utf-8"))
    red = Red(teselas_necesarias(originales))   # las mismas teselas que ajustar_rutas.py (ya en caché)
    base = json.loads((DATOS / "lineas_base.json").read_text(encoding="utf-8"))["lineas"]
    nucleos = {n: [(p["lat"], p["lon"]) for p in b["paradas"] if "lat" in p] for n, b in base.items()}
    # paradas de todas las líneas y marquesinas de OpenStreetMap: un ramal con parada en la punta es de verdad
    todas = {}
    osm = json.loads((DATOS / "paradas_osm.json").read_text(encoding="utf-8"))
    for s_ in [(p["lat"], p["lon"]) for l in datos.values() for p in l.get("paradas", [])] + [(p["lat"], p["lon"]) for p in osm]:
        todas.setdefault((int(s_[0] / 0.002), int(s_[1] / 0.002)), []).append(s_)
    orig = {}
    for n, o in originales.items():
        tr = o.get("trazado") or []
        orig[n] = [tuple(p) for parte in (tr if tr and isinstance(tr[0][0], list) else [tr]) for p in parte]
    for num, l in datos.items():
        if (a.lineas and num not in a.lineas) or not l.get("trazado"):
            continue
        partes = l["trazado"] if isinstance(l["trazado"][0][0], list) else [l["trazado"]]
        print(f"Línea {num}")
        nuevas = []
        for t in partes:
            t = [tuple(p) for p in t]
            t = arreglar_fuera(red, t, print)
            while True:   # hasta que no cambie nada (quitar un pico puede dejar a la vista un ramal)
                n = len(t)
                t = quitar_ramales(t, todas, nucleos.get(num, []), orig.get(num, []), print)
                t = quitar_picos(t, todas, print)
                if len(t) == n:
                    break
            nuevas.append([[round(x, 6), round(y, 6)] for x, y in t])
        l["trazado"] = nuevas
    if not a.dry_run:
        f.write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"Guardado {f}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
