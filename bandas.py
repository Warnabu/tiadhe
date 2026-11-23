"""Bandas paralelas para las líneas que comparten carretera (estilo plano de metro).

Si varias líneas pasan por la misma calle, se dibujarían una encima de otra y solo se vería la última. Aquí se
parte el recorrido de cada línea en tramos según qué otras líneas lo comparten, y a cada tramo se le asigna un
desplazamiento lateral `o` (en "anchos de línea"): con 3 líneas compartiendo, -1, 0 y +1. El cliente lo convierte
en píxeles, así que se ve bien a cualquier zoom.

El signo de `o` se da respecto al sentido en que está dibujado el tramo, normalizado para que dos líneas que
recorren la misma calle en sentidos opuestos queden igualmente a un lado y no se crucen.
"""
import math

PASO_M = 20          # densidad del muestreo
CERCA_M = 12         # dos líneas "comparten" la calle si pasan a menos de esto
MIN_PUNTOS = 4       # un cambio de grupo más corto que esto (~80 m: un cruce) se ignora
CELDA = 0.0004       # rejilla espacial (grados, ~40 m)


def _m(a, b):
    c = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 110540, (a[1] - b[1]) * 111320 * c)


def _densificar(t):
    out = [tuple(t[0])]
    for a, b in zip(t, t[1:]):
        n = max(1, int(_m(a, b) // PASO_M))
        for i in range(1, n + 1):
            out.append((a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n))
    return out


def _dist_seg(p, a, b):
    c = math.cos(math.radians(p[0]))
    ax, ay, bx, by, px, py = a[1] * c * 111320, a[0] * 110540, b[1] * c * 111320, b[0] * 110540, p[1] * c * 111320, p[0] * 110540
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    u = 0 if L == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - ax - u * dx, py - ay - u * dy)


def _simplificar(t, tol=2.0):
    if len(t) < 3:
        return t
    keep = [False] * len(t)
    keep[0] = keep[-1] = True
    pila = [(0, len(t) - 1)]
    while pila:
        i, j = pila.pop()
        d, k = max(((_dist_seg(t[m], t[i], t[j]), m) for m in range(i + 1, j)), default=(0, -1))
        if d > tol:
            keep[k] = True
            pila += [(i, k), (k, j)]
    return [p for p, k in zip(t, keep) if k]


def calcular(trazados):
    """trazados: {num: [tramo, …]} con tramo = [[lat, lon], …].
    Devuelve {num: [{"p": [[lat, lon], …], "o": desplazamiento}, …]}."""
    orden = {n: i for i, n in enumerate(trazados)}
    rejilla = {}  # celda → [(num, a, b)]
    for num, partes in trazados.items():
        for t in partes:
            for a, b in zip(t, t[1:]):
                for la in range(int(min(a[0], b[0]) / CELDA) - 1, int(max(a[0], b[0]) / CELDA) + 2):
                    for lo in range(int(min(a[1], b[1]) / CELDA) - 1, int(max(a[1], b[1]) / CELDA) + 2):
                        rejilla.setdefault((la, lo), []).append((num, a, b))

    res = {}
    for num, partes in trazados.items():
        tramos = []
        for t in partes:
            if len(t) < 2:
                continue
            pts = _densificar(t)
            grupos = []
            for p in pts:
                cerca = {num}
                for n2, a, b in rejilla.get((int(p[0] / CELDA), int(p[1] / CELDA)), ()):
                    if n2 not in cerca and _dist_seg(p, a, b) < CERCA_M:
                        cerca.add(n2)
                grupos.append(frozenset(cerca))
            # suavizar: los cambios de grupo muy cortos (cruces, pasos por encima) no cuentan
            i = 0
            while i < len(grupos):
                j = i
                while j < len(grupos) and grupos[j] == grupos[i]:
                    j += 1
                if j - i < MIN_PUNTOS and i > 0:
                    for k in range(i, j):
                        grupos[k] = grupos[i - 1]
                i = j
            # trocear por grupo
            i = 0
            while i < len(pts):
                j = i
                while j + 1 < len(pts) and grupos[j + 1] == grupos[i]:
                    j += 1
                trozo = pts[i:j + 2]  # incluye el primer punto del siguiente para que no haya huecos
                miembros = sorted(grupos[i], key=lambda n: orden[n])
                o = miembros.index(num) - (len(miembros) - 1) / 2
                # sentido canónico: hacia el este (o hacia el norte si es casi vertical)
                dla, dlo = trozo[-1][0] - trozo[0][0], trozo[-1][1] - trozo[0][1]
                if dlo < 0 or (abs(dlo) < 1e-9 and dla < 0):
                    o = -o
                tramos.append({"p": [[round(a, 6), round(b, 6)] for a, b in _simplificar(trozo)], "o": o})
                i = j + 1
        res[num] = tramos
    return res
