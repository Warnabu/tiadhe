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
     Si donde el ajuste va y vuelve por la misma calle el original da una vuelta (baja por una y sube por otra que no
     está en la red de calles, como en Giniginamar), se usa la vuelta del original.
  3. Picos: un vértice en el que el trazado da media vuelta en seco se quita.
"""
import argparse, heapq, json, math, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ajustar_rutas import DATOS, Red, acumulado, m, proyectar_seg, teselas_necesarias  # noqa: E402

FUERA_M = 25         # una muestra a más de esto de cualquier calle está "fuera de la calle"
MUESTRA_M = 10
EN_CALLE_M = 12      # los extremos de un tramo arreglado tienen que estar en la calle
MAS_LARGO = 1.1       # el camino por calles no puede ser más largo que el dibujo (+10 % y 30 m): si lo es, se inventa un rodeo
RAMAL_MAX_M = 1200   # longitud máxima de un ramal de ida y vuelta
RAMAL_MIN_M = 40
PARADA_PUNTA_M = 60  # con una parada de la línea en el ramal, el ramal se queda (la guagua entra a por ella)
FALTA_M = 80         # una vuelta del original se usa si al ajuste le falta un tramo seguido así de largo…
VUELTA_MAX_M = 600   # …y si es corta (un pueblo como Giniginamar); las largas se avisan
LEJOS_M = 35         # …a más de esto (la imprecisión normal del dibujo a mano no cuenta)
RECTA_LARGA_M = 250  # un segmento del original así de largo es un atajo dibujado a mano, no una calle
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


def arreglar_fuera(red, t, orig, log):
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
        cam = camino_calles(red, t[a], t[b], largo * MAS_LARGO + 30)
        if cam and not fiel_al_original(cam, orig):
            log(f"    AVISO el camino por calles en {t[a][0]:.5f},{t[a][1]:.5f} se aleja del original de Tiadhe: se deja el dibujo")
            cam = None
        if cam:
            res += [t[a]] + cam[1:-1]
            log(f"    tramo fuera de la calle en {t[a][0]:.5f},{t[a][1]:.5f}: {largo:.0f} m dibujados → "
                f"{acumulado(cam)[-1]:.0f} m por calles")
        else:
            res += t[a:b]
            log(f"    AVISO sin arreglo: tramo fuera de la calle en {t[a][0]:.5f},{t[a][1]:.5f} ({largo:.0f} m)")
        ult = b
    return res + t[ult:]


def segmento_original(orig, p):
    """(distancia de p al trazado original, longitud del segmento original más cercano)."""
    mejor = (1e9, 0.0)
    for a, b in zip(orig, orig[1:]):
        if min(a[0], b[0]) - 0.01 < p[0] < max(a[0], b[0]) + 0.01 and min(a[1], b[1]) - 0.01 < p[1] < max(a[1], b[1]) + 0.01:
            d = proyectar_seg(p, a, b)[0]
            if d < mejor[0]:
                mejor = (d, m(a, b))
    return mejor


def fiel_al_original(camino, orig):
    """El camino por calles va por donde dibujó Tiadhe (a ≤ ORIGINAL_M), salvo donde el original es una recta larga
    y poco fiable (un atajo dibujado a mano de punta a punta, como el de la FV-2 en Costa Calma)."""
    for q in camino:
        d, largo = segmento_original(orig, q)
        if d > ORIGINAL_M and largo < RECTA_LARGA_M:
            return False
    return True


def bucle_original(orig, inicio, punta, fin, largo):
    """Si el original sale de 'inicio', pasa por la punta y vuelve a 'fin' por otro sitio (un bucle), sus puntos."""
    for i, p in enumerate(orig):
        if m(p, inicio) >= ORIGINAL_M:
            continue
        L, k = 0.0, None
        for j in range(i + 1, len(orig)):
            L += m(orig[j - 1], orig[j])
            if L > 3 * largo + 300:
                break
            if k is None and m(orig[j], punta) < ORIGINAL_M:
                k = j
            if k is not None and j > k and m(orig[j], fin) < ORIGINAL_M:
                return orig[i:j + 1]
    return None


def calle_que_falta(bucle, tramo):
    """Metros seguidos del bucle original que quedan lejos (> LEJOS_M) del trazado ajustado: la calle que se salta."""
    def lejos(q):
        return min(proyectar_seg(q, x, y)[0] for x, y in zip(tramo, tramo[1:])) > LEJOS_M
    mayor = racha = 0.0
    for a, b in zip(bucle, bucle[1:]):
        n = max(1, int(m(a, b) / 10))
        for k in range(n):
            q = (a[0] + (b[0] - a[0]) * (k + .5) / n, a[1] + (b[1] - a[1]) * (k + .5) / n)
            racha = racha + m(a, b) / n if lejos(q) else 0.0
            mayor = max(mayor, racha)
    return mayor


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


def quitar_ramales(t, paradas, nucleos, orig, log, fijos):
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
        if j_ok is not None and any(p in fijos for p in t[i + 1:j_ok]):
            j_ok = None   # ahí ya se puso la vuelta del original: no se toca más
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
            bucle = bucle_original(orig, tramo[0], tramo[k], tramo[-1], acc[-1]) if alejado else None
            falta = calle_que_falta(bucle, tramo) if bucle else 0
            if falta >= FALTA_M and acumulado(bucle)[-1] > VUELTA_MAX_M:
                log(f"    AVISO vuelta en {tramo[0][0]:.5f},{tramo[0][1]:.5f}: al ajuste le faltan {falta:.0f} m de la que dibujó "
                    f"Tiadhe, pero es larga ({acumulado(bucle)[-1]:.0f} m) y no se cambia por el dibujo a mano")
            elif falta >= FALTA_M:
                # el original da una vuelta (p. ej. Giniginamar: baja por una calle y sube por otra que no está en la
                # red de calles): se usa la vuelta del original, si las paradas siguen quedando en la línea
                nuevo = [tramo[0]] + list(bucle[1:-1]) + [tramo[-1]]
                def dist(q, linea):
                    return min(proyectar_seg(q, x, y)[0] for x, y in zip(linea, linea[1:]))
                suyas = {s_ for p in tramo for s_ in paradas_cerca(paradas, p) if dist(s_, tramo) < 60}
                if all(dist(s_, nuevo) <= max(25, dist(s_, tramo) + 10) for s_ in suyas):   # ninguna parada se aleja
                    log(f"    vuelta en {tramo[0][0]:.5f},{tramo[0][1]:.5f}: el ajuste se salta parte de la que dibujó "
                        f"Tiadhe ({falta:.0f} m de calle sin recorrer) → se usa la del original ({acumulado(nuevo)[-1]:.0f} m)")
                    fijos.update(nuevo)
                    t = t[:i] + nuevo + t[j_ok + 1:]
                    i += len(nuevo)
                    continue
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


def quitar_picos(t, paradas, log, fijos=frozenset()):
    """Quita los vértices en los que el trazado da media vuelta en seco (picos y zigzags de un solo punto).
    Junto a una parada no: ahí la media vuelta es la guagua que entra hasta la parada y vuelve (La Pared, Tindaya…)."""
    n0, cambio = len(t), True
    while cambio:
        cambio, res = False, [t[0]]
        for i in range(1, len(t) - 1):
            if t[i] not in fijos and angulo(res[-1], t[i], t[i + 1]) > PICO_GRADOS and \
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
            t = arreglar_fuera(red, t, orig.get(num, []), print)
            fijos = set()   # puntos puestos desde el original (vueltas): no se vuelven a tocar
            while True:   # hasta que no cambie nada (quitar un pico puede dejar a la vista un ramal)
                n = len(t)
                t = quitar_ramales(t, todas, nucleos.get(num, []), orig.get(num, []), print, fijos)
                t = quitar_picos(t, todas, print, fijos)
                if len(t) == n:
                    break
            nuevas.append([[round(x, 6), round(y, 6)] for x, y in t])
        l["trazado"] = nuevas
    if not a.dry_run:
        f.write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"Guardado {f}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
