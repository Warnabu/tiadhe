#!/usr/bin/env python3
"""Pinta las rutas sobre las calles reales: ajusta ("map matching") el trazado original de cada línea a la red viaria.

    python herramientas/ajustar_rutas.py              # todas las líneas → datos/paradas_reales.json
    python herramientas/ajustar_rutas.py 05 12        # solo esas
    python herramientas/ajustar_rutas.py --dry-run    # informa sin escribir

Entrada fija: datos/trazados_originales.json (los trazados y paradas tal como vienen de los mapas de Google de
Tiadhe, ver importar_mapas.py). El script siempre parte de ahí, así que se puede repetir sin acumular cambios.

Cómo funciona (modelo oculto de Markov, el método estándar para pegar un GPS a un mapa):
  1. La red de calles sale de las teselas vectoriales del servidor OSRM (datos de OpenStreetMap): cada segmento
     con su sentido de circulación. Se cachean en datos/.cache_osrm/red/.
  2. El trazado original se muestrea cada 25 m. Cada muestra tiene como candidatas las calles a menos de 50 m.
  3. Se elige la sucesión de calles más probable: cerca del dibujo (probabilidad de "emisión") y con una distancia
     por la red parecida a la del dibujo entre muestras (probabilidad de "transición"). Así la ruta va por las mismas
     calles que el original, sin inventar rodeos; las diagonales y los octógonos dibujados a mano se resuelven por
     la calle real más cercana.
  4. Consenso entre líneas: primero se ajusta cada línea por su cuenta; luego otra vez dando preferencia a las calles
     que usan las demás (un trazado dibujado con poco cuidado a 60 m de la avenida por la que van otras seis líneas
     va por esa avenida, no por la calle de detrás). Ir a contramano de un sentido único cuesta un poco más.
  5. Paradas: si la oficial está a ≤25 m de la ruta se queda donde está; si no, se pone en su marquesina de
     OpenStreetMap (≤40 m) o sobre la calle; las que están a >120 m (desvíos o datos dudosos) se dejan y se avisa.
     'k' (km desde el origen, el servidor lo usa para los tiempos) se recalcula sobre la ruta y se reescala a la
     longitud original (km_escala) para no cambiar la duración total de las líneas.
"""
import argparse, heapq, json, math, os, sys, time
from pathlib import Path

DATOS = Path(__file__).resolve().parent.parent / "datos"
RED = DATOS / ".cache_osrm" / "red"
ORIGINALES = DATOS / "trazados_originales.json"
SERVIDOR = "https://router.project-osrm.org"
Z = 15
PASO_M = 25            # muestreo del trazado original
RADIO_M = 90           # calles candidatas para cada muestra
SIGMA_M = 25           # error típico del dibujo original (está hecho a mano: hasta 40 m)
BETA_M = 30            # tolerancia entre distancia por la red y distancia en el dibujo
MAX_CAND = 24
CONSENSO = 2.0         # premio (por muestra) a las calles que usan otras líneas: atrae los trazados imprecisos
CONTRAMANO = 1.3       # ir en contra de un sentido único: leve preferencia por el sentido legal (para dibujar da igual,
                       # y un valor alto hace que el ajuste invente vueltas para respetar sentidos)
CAMBIO_SENTIDO_M = 150 # coste extra de dar media vuelta en una calle (solo si no hay otra, p. ej. un fondo de saco)


# ------------------------------------------------------------------ geometría
def km(a, b):
    p = math.pi / 180
    h = math.sin((b[0] - a[0]) * p / 2) ** 2 + math.cos(a[0] * p) * math.cos(b[0] * p) * math.sin((b[1] - a[1]) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(min(1.0, h)))


def m(a, b):
    return km(a, b) * 1000


def acumulado(t):
    acc = [0.0]
    for a, b in zip(t, t[1:]):
        acc.append(acc[-1] + m(a, b))
    return acc


def punto_en(t, acc, d):
    if d <= 0:
        return t[0]
    for i in range(1, len(t)):
        if acc[i] >= d:
            s = acc[i] - acc[i - 1]
            u = 0 if s == 0 else (d - acc[i - 1]) / s
            return (t[i - 1][0] + u * (t[i][0] - t[i - 1][0]), t[i - 1][1] + u * (t[i][1] - t[i - 1][1]))
    return t[-1]


def proyectar_seg(p, a, b):
    """(distancia m, fracción u, punto) de p sobre el segmento a-b."""
    c = math.cos(math.radians(p[0]))
    ax, ay, bx, by = (a[1] - p[1]) * c * 111320, (a[0] - p[0]) * 110540, (b[1] - p[1]) * c * 111320, (b[0] - p[0]) * 110540
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    u = 0.0 if L == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L))
    return math.hypot(ax + u * dx, ay + u * dy), u, (a[0] + u * (b[0] - a[0]), a[1] + u * (b[1] - a[1]))


def proyectar(t, acc, p, i0=0, i1=None):
    """(distancia m, posición m, índice, punto) de p sobre la polilínea t[i0:i1]."""
    i1 = len(t) - 1 if i1 is None else min(i1, len(t) - 1)
    mejor = (1e18, 0.0, i0, t[i0])
    for i in range(i0, i1):
        d, u, q = proyectar_seg(p, t[i], t[i + 1])
        if d < mejor[0]:
            mejor = (d, acc[i] + u * (acc[i + 1] - acc[i]), i, q)
    return mejor


def douglas_peucker(t, tol_m=1.5):
    if len(t) < 3:
        return list(t)
    keep = [False] * len(t)
    keep[0] = keep[-1] = True
    pila = [(0, len(t) - 1)]
    while pila:
        i, j = pila.pop()
        d, k = max(((proyectar_seg(t[x], t[i], t[j])[0], x) for x in range(i + 1, j)), default=(0, -1))
        if d > tol_m:
            keep[k] = True
            pila += [(i, k), (k, j)]
    return [p for p, k in zip(t, keep) if k]


# ------------------------------------------------------------------ red de calles
def tesela(la, lo):
    n = 2 ** Z
    return int((lo + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(la))) / math.pi) / 2 * n)


def bajar_tesela(x, y, servidor):
    import requests
    import mapbox_vector_tile as mvt
    r = None
    for i in range(6):
        try:
            r = requests.get(f"{servidor}/tile/v1/driving/tile({x},{y},{Z}).mvt", timeout=60,
                             headers={"User-Agent": "guaguas-fuerteventura"})
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(3 + 3 * i)
    segs = []
    if r is not None and r.status_code == 200 and r.content:
        n = 2 ** Z
        d = mvt.decode(r.content, default_options={"y_coord_down": True})
        for ft in d.get("speeds", {}).get("features", []):
            g = ft["geometry"]
            if g["type"] != "LineString":
                continue
            pts = [(round(math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + py / 4096) / n)))), 7),
                    round((x + px / 4096) / n * 360 - 180, 7)) for px, py in g["coordinates"]]
            segs.append([pts[0], pts[-1], ft["properties"].get("name", "")])
    time.sleep(1.05)
    return segs


class Red:
    """Grafo dirigido de calles con índice espacial."""

    def __init__(self, teselas, servidor=SERVIDOR):
        RED.mkdir(parents=True, exist_ok=True)
        dirigidos = {}
        faltan = [t for t in teselas if not (RED / f"{Z}_{t[0]}_{t[1]}.json").exists()]
        if faltan:
            print(f"Descargando {len(faltan)} teselas de calles (≈{len(faltan) * 1.1 / 60:.0f} min)…")
        for x, y in teselas:
            f = RED / f"{Z}_{x}_{y}.json"
            if not f.exists():
                f.write_text(json.dumps(bajar_tesela(x, y, servidor)))
            for a, b, nombre in json.loads(f.read_text()):
                a, b = tuple(a), tuple(b)
                if a != b:
                    dirigidos[(a, b)] = nombre
        self.nodos, self.pos = {}, []
        rejilla = {}   # celdas de ~1 m: los extremos que una calle tiene en dos teselas vecinas no coinciden exactos
        def nodo(p):
            if p in self.nodos:
                return self.nodos[p]
            ci, cj = round(p[0] * 1e5), round(p[1] * 1e5)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for q in rejilla.get((ci + di, cj + dj), ()):
                        if m(p, self.pos[q]) <= 1.5:
                            self.nodos[p] = q
                            return q
            self.nodos[p] = len(self.pos)
            self.pos.append(p)
            rejilla.setdefault((ci, cj), []).append(self.nodos[p])
            return self.nodos[p]
        self.aristas = []   # (u, v, longitud_m, coste)
        self.sal = {}       # u → [índice de arista]
        def arista(u, v, L, coste):
            self.aristas.append((u, v, L, coste))
            self.sal.setdefault(u, []).append(len(self.aristas) - 1)
        for (a, b) in dirigidos:
            u, v = nodo(a), nodo(b)
            L = m(a, b)
            arista(u, v, L, L)
            if (b, a) not in dirigidos:     # sentido único: a contramano solo si compensa mucho
                arista(v, u, L, L * CONTRAMANO)
        self._coser()
        # trozos sueltos (aparcamientos sin conexión, restos de teselas): no sirven como candidatos
        vec = {}
        for u, v, L, c in self.aristas:
            vec.setdefault(u, []).append(v)
            vec.setdefault(v, []).append(u)
        comp, tam = {}, {}
        for s0 in vec:
            if s0 in comp:
                continue
            pila, comp[s0], n = [s0], s0, 0
            while pila:
                x = pila.pop(); n += 1
                for y in vec[x]:
                    if y not in comp:
                        comp[y] = s0; pila.append(y)
            tam[s0] = n
        self.util = {u for u in vec if tam[comp[u]] >= 200}
        # índice espacial (celdas de ~110 m)
        self.celdas = {}
        for e, (u, v, L, c) in enumerate(self.aristas):
            if u not in self.util:
                continue
            a, b = self.pos[u], self.pos[v]
            for i in range(int(min(a[0], b[0]) / 0.001) - 1, int(max(a[0], b[0]) / 0.001) + 2):
                for j in range(int(min(a[1], b[1]) / 0.001) - 1, int(max(a[1], b[1]) / 0.001) + 2):
                    self.celdas.setdefault((i, j), []).append(e)

    def _coser(self):
        """OSRM recorta cada calle en el borde (con margen) de su tesela: el trozo de una tesela y el de la vecina se
        solapan pero no comparten ningún punto. Cada extremo suelto que cae sobre una calle de otra tesela se une a ella
        partiendo esa calle por ese punto (X→D y D→Y), así la conexión queda recta, sin picos."""
        vecinos = {}
        for u, v, L, c in self.aristas:
            vecinos.setdefault(u, set()).add(v)
            vecinos.setdefault(v, set()).add(u)
        sueltos = [n for n, vs in vecinos.items() if len(vs) == 1]
        celdas = {}
        for e, (u, v, L, c) in enumerate(self.aristas):
            a, b = self.pos[u], self.pos[v]
            for i in range(int(min(a[0], b[0]) / 0.001), int(max(a[0], b[0]) / 0.001) + 1):
                for j in range(int(min(a[1], b[1]) / 0.001), int(max(a[1], b[1]) / 0.001) + 1):
                    celdas.setdefault((i, j), []).append(e)
        nuevas = []
        for d in sueltos:
            p = self.pos[d]
            for e in celdas.get((int(p[0] / 0.001), int(p[1] / 0.001)), ()):
                u, v, L, c = self.aristas[e]
                if d in (u, v):
                    continue
                dist, t, q = proyectar_seg(p, self.pos[u], self.pos[v])
                if dist <= 1.0 and 0.0 < t < 1.0:
                    nuevas.append((u, d, L * t, c * t))
                    nuevas.append((d, v, L * (1 - t), c * (1 - t)))
        for u, v, L, c in nuevas:
            self.aristas.append((u, v, L, c))
            self.sal.setdefault(u, []).append(len(self.aristas) - 1)
        self.cosidos = len(nuevas) // 2

    compartidas = frozenset()

    def clave(self, e):
        u, v = self.aristas[e][:2]
        return (u, v) if u < v else (v, u)

    def candidatos(self, p, radio=RADIO_M):
        vistos, res = set(), []
        for e in self.celdas.get((int(p[0] / 0.001), int(p[1] / 0.001)), ()):
            if e in vistos:
                continue
            vistos.add(e)
            u, v, L, c = self.aristas[e]
            d, t, q = proyectar_seg(p, self.pos[u], self.pos[v])
            if d <= radio:
                res.append((d, e, t, q))
        # las calles que usan otras líneas entran antes en la lista (si no, en zonas con muchas calles
        # quedan fuera de las MAX_CAND más cercanas y el consenso no puede actuar)
        res.sort(key=lambda r: r[0] - (45 if self.clave(r[1]) in self.compartidas else 0))
        # sin duplicados: varios segmentos seguidos de la misma calle dan el mismo punto proyectado
        unicos, vistos_q = [], []
        for d, e, t, q in res:
            sentido = (self.aristas[e][0] < self.aristas[e][1])
            if any(sv == sentido and m(q, qv) < 3 for qv, sv in vistos_q):
                continue
            vistos_q.append((q, sentido))
            unicos.append((d, e, t, q))
            if len(unicos) >= MAX_CAND:
                break
        return unicos

    def dijkstra(self, origen, limite, vengo_de=None):
        """Costes mínimos desde el nodo origen hasta todos los nodos a menos de 'limite' (y su predecesor).
        vengo_de: nodo del que se llega; volver a él nada más salir es media vuelta y se penaliza."""
        dist, prev = {origen: 0.0}, {}
        pq = [(0.0, origen)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, 1e18) or d > limite:
                continue
            for e in self.sal.get(u, ()):
                _, v, L, c = self.aristas[e]
                nd = d + c + (CAMBIO_SENTIDO_M if u == origen and v == vengo_de else 0)
                if nd < dist.get(v, 1e18) and nd <= limite:
                    dist[v], prev[v] = nd, e
                    heapq.heappush(pq, (nd, v))
        return dist, prev


# ------------------------------------------------------------------ ajuste
def ajustar(red, guia):
    """Ajusta la polilínea guia [(lat, lon)] a la red. Devuelve (ruta [(lat, lon)], posición en la ruta de cada muestra
    de la guía en metros, distancias de las muestras en la guía)."""
    acc = acumulado(guia)
    n = max(1, int(acc[-1] // PASO_M))
    ks = [acc[-1] * i / n for i in range(n + 1)]
    obs = [punto_en(guia, acc, k) for k in ks]
    cands = [red.candidatos(p) for p in obs]

    # Viterbi por tramos (si dos muestras seguidas no se pueden unir por la red, se empieza un tramo nuevo)
    tramos = []
    actual = None  # lista de (índice_obs, candidatos, puntuaciones, backptr)
    for i, cs in enumerate(cands):
        if not cs:
            continue
        emis = [-0.5 * (d / SIGMA_M) ** 2 + (CONSENSO if red.clave(e) in red.compartidas else 0) for d, e, t, q in cs]
        if actual is None:
            actual = [(i, cs, emis, [None] * len(cs))]
            continue
        j, cs0, sc0, _ = actual[-1]
        gc = m(obs[j], obs[i])
        limite = max(3 * gc, gc + 400)
        nuevas, back = [-1e18] * len(cs), [None] * len(cs)
        for a, (d0, e0, t0, q0) in enumerate(cs0):
            if sc0[a] <= -1e17:
                continue
            u0, v0, L0, c0 = red.aristas[e0]
            dist, _ = red.dijkstra(v0, limite, u0)
            for b, (d1, e1, t1, q1) in enumerate(cs):
                u1, v1, L1, c1 = red.aristas[e1]
                if e1 == e0 and t1 >= t0:
                    coste = (t1 - t0) * c0
                elif e1 == e0 and (t0 - t1) * L0 < 15:
                    coste = 0.0   # unos metros "hacia atrás" en la misma calle: ruido del dibujo, se queda quieto
                else:
                    if u1 not in dist:
                        continue
                    coste = (1 - t0) * c0 + dist[u1] + t1 * c1
                    if u1 == v0 and v1 == u0:   # la misma calle en sentido contrario: media vuelta
                        coste += CAMBIO_SENTIDO_M
                s = sc0[a] - abs(coste - gc) / BETA_M + emis[b]
                if s > nuevas[b]:
                    nuevas[b], back[b] = s, a
        if max(nuevas) <= -1e17:
            tramos.append(actual)
            actual = [(i, cs, emis, [None] * len(cs))]
        else:
            actual.append((i, cs, nuevas, back))
    if actual:
        tramos.append(actual)

    # reconstrucción
    ruta, pos_obs, ultimo_obs = [], {}, 0
    for tramo in tramos:
        b = max(range(len(tramo[-1][2])), key=lambda x: tramo[-1][2][x])
        elegidos = []
        for paso in reversed(tramo):
            elegidos.append((paso[0], paso[1][b]))
            b = paso[3][b] if paso[3][b] is not None else b
        elegidos.reverse()
        trozo, local = [elegidos[0][1][3]], {elegidos[0][0]: 0}
        for (i0, c0), (i1, c1) in zip(elegidos, elegidos[1:]):
            d0, e0, t0, q0 = c0
            d1, e1, t1, q1 = c1
            if e1 == e0 and t1 < t0 and (t0 - t1) * red.aristas[e0][2] < 15:
                local[i1] = len(trozo) - 1   # no retrocede: se queda donde estaba
                continue
            if i1 - i0 > 2:
                # el original pasa lejos de toda calle conocida: es una calle que falta en OpenStreetMap
                # (o un acceso privado a una parada). Se conserva el dibujo original de ese trozo.
                trozo += _relleno(obs[i0 + 1:i1], trozo[-1], q1)
            elif not (e1 == e0 and t1 >= t0):
                u0, v0, L0, cc0 = red.aristas[e0]
                u1, v1, L1, cc1 = red.aristas[e1]
                gc = m(obs[i0], obs[i1])
                dist, prev = red.dijkstra(v0, max(3 * gc, gc + 400), u0)
                camino, x = [], u1
                while x != v0 and x in prev:
                    camino.append(x)
                    x = red.aristas[prev[x]][0]
                camino.append(v0)
                trozo += [red.pos[x] for x in reversed(camino)]
            trozo.append(q1)
            local[i1] = len(trozo) - 1
        base = len(ruta)
        if ruta:   # entre tramos que la red no une: el dibujo original (o una recta si el hueco es corto)
            ruta += _relleno(obs[ultimo_obs + 1:elegidos[0][0]], ruta[-1], trozo[0])
            base = len(ruta)
        ruta += trozo
        ultimo_obs = elegidos[-1][0]
        for k_, v_ in local.items():
            pos_obs[k_] = v_ + base
    # limpiar puntos repetidos
    limpia, mapa = [], {}
    for idx, p in enumerate(ruta):
        if not limpia or m(limpia[-1], p) > 0.3:
            limpia.append(p)
        mapa[idx] = len(limpia) - 1
    acc_r = acumulado(limpia)
    pos_m = {i: acc_r[mapa[j]] for i, j in pos_obs.items()}
    red.usadas = {(min(red.nodos[a], red.nodos[b]), max(red.nodos[a], red.nodos[b]))
                  for a, b in zip(ruta, ruta[1:]) if a in red.nodos and b in red.nodos}
    return limpia, pos_m, ks, len(tramos)


def _relleno(puntos, a, b):
    """Puntos con que unir a y b a través de un hueco de la red. Si el hueco es corto (<150 m) y el dibujo original
    da vueltas (octógonos de rotondas que no están en OSM, saltos entre calzadas), una recta queda más limpia."""
    recta = m(a, b)
    dibujo = sum(m(x, y) for x, y in zip([a] + list(puntos), list(puntos) + [b]))
    if recta < 150 and 1.3 * recta + 10 < dibujo < 400:   # un desvío largo de ida y vuelta NO se recorta
        return []
    return douglas_peucker([a] + list(puntos) + [b], 3.0)[1:-1]


def quitar_jitter(t, max_m=12):
    """Picos de pocos metros (A→B→A con B a <max_m), en una sola pasada y sin volver sobre lo ya limpiado:
    repetirlo "royería" un desvío de ida y vuelta real desde la punta hasta borrarlo entero."""
    out, i = [t[0]], 1
    while i < len(t):
        hecho = False
        for j in range(i + 1, min(len(t), i + 6)):
            if m(out[-1], t[j]) < 2 and max(m(out[-1], q) for q in t[i:j]) < max_m:
                i, hecho = j + 1, True   # se salta el pico; t[j] ≈ out[-1]
                break
        if not hecho:
            out.append(t[i])
            i += 1
    return out


# ------------------------------------------------------------------ línea
def procesar(num, orig, red, osm, log):
    partes = [[tuple(p) for p in t] for t in orig["trazado"]]
    guia = partes[0]
    acc_g = acumulado(guia)
    cruda, pos_m, ks, ntramos = ajustar(red, guia)
    if ntramos > 1:
        log(f"   ~ {ntramos - 1} hueco(s) sin calle en OSM: se unen en línea recta")
    ruta = douglas_peucker(quitar_jitter(cruda), 1.0)
    acc_r = acumulado(ruta)
    # posición de cada muestra en la ruta limpia (proyección hacia delante, en orden)
    acc_c, nuevo, i_seg = acumulado(cruda), {}, 0
    for i in sorted(pos_m):
        p = punto_en(cruda, acc_c, pos_m[i])
        d, k, i_seg2, _ = proyectar(ruta, acc_r, p, i_seg, i_seg + 40)
        if d > 30:
            d, k, i_seg2, _ = proyectar(ruta, acc_r, p, i_seg, None)
        nuevo[i], i_seg = k, i_seg2
    pos_m = nuevo
    # correspondencia guía (m) → ruta (m) a partir de las muestras ajustadas
    pares = sorted((ks[i], pos_m[i]) for i in pos_m)
    def en_ruta(kg):
        if not pares:
            return 0.0
        j = min(range(len(pares)), key=lambda x: abs(pares[x][0] - kg))
        return pares[j][1]

    escala_k = acc_g[-1] / 1000 / orig["km_total"] if orig.get("km_total") else 1.0
    paradas = sorted(orig["paradas"], key=lambda p: p["k"])
    nuevas, ultimo, avisos = [], 0.0, []
    for p in paradas:
        s = (p["lat"], p["lon"])
        # posición aproximada en la ruta según su k en el original; se busca la proyección en ±600 m
        kg = min(max(p["k"] * 1000 * escala_k, 0), acc_g[-1])
        dg, kg2, _, _ = proyectar(guia, acc_g, s, *_ventana(acc_g, kg - 3000, kg + 3000))
        centro = en_ruta(kg2)
        i0, i1 = _ventana(acc_r, max(centro - 600, ultimo), centro + 600)
        d, k, _, q = proyectar(ruta, acc_r, s, i0, i1)
        if k < ultimo:
            d, k, _, q = proyectar(ruta, acc_r, s, *_ventana(acc_r, ultimo, ultimo + 1500))
        k = max(k, ultimo)
        o = min(osm, key=lambda x: m(s, x), default=None)
        if d <= 25:
            pos = s
        elif o and m(s, o) <= 40 and proyectar(ruta, acc_r, o, i0, i1)[0] <= 25:
            pos = o
        elif d <= 120:
            pos = q
        else:
            pos = s
            avisos.append(f"   · {p['n']}: a {d:.0f} m de la ruta y sin marquesina en OSM → se deja donde está (¿desvío?)")
        if m(s, pos) > 60:
            avisos.append(f"   {p['n']}: movida {m(s, pos):.0f} m")
        ultimo = k
        nuevas.append({**p, "lat": round(pos[0], 6), "lon": round(pos[1], 6), "k": k})
    extra = []
    for parte in partes[1:]:
        r2, _, _, _ = ajustar(red, parte)
        extra.append(douglas_peucker(quitar_jitter(r2), 1.0))
    for x in avisos:
        log(x)
    return nuevas, ruta, extra


def _ventana(acc, a, b):
    i0 = next((i for i, x in enumerate(acc) if x >= a), len(acc) - 1)
    i1 = next((i for i, x in enumerate(acc) if x > b), len(acc) - 1)
    return max(0, i0 - 1), max(i1, i0 + 1)


def teselas_necesarias(datos):
    ts = set()
    for v in datos.values():
        pts = [p for t in v["trazado"] for p in t] + [[p["lat"], p["lon"]] for p in v["paradas"]]
        for a, b in zip(pts, pts[1:]):
            L = max(1, int(math.hypot(a[0] - b[0], a[1] - b[1]) / 0.0008))
            for i in range(L + 1):
                la, lo = a[0] + (b[0] - a[0]) * i / L, a[1] + (b[1] - a[1]) * i / L
                for dla in (-0.003, 0, 0.003):
                    for dlo in (-0.003, 0, 0.003):
                        ts.add(tesela(la + dla, lo + dlo))
    return sorted(ts)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("lineas", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--salida", default=str(DATOS / "paradas_reales.json"))
    ap.add_argument("--servidor", default=SERVIDOR)
    a = ap.parse_args(argv)
    originales = json.loads(ORIGINALES.read_text(encoding="utf-8"))
    salida = Path(a.salida)
    datos = json.loads(salida.read_text(encoding="utf-8")) if salida.exists() else {}
    osm_f = DATOS / "paradas_osm.json"
    osm = [(p["lat"], p["lon"]) for p in json.loads(osm_f.read_text(encoding="utf-8"))] if osm_f.exists() else []
    t0 = time.time()
    red = Red(teselas_necesarias(originales), a.servidor)
    print(f"Red: {len(red.pos)} nodos, {len(red.aristas)} aristas ({time.time() - t0:.0f} s)")
    validas = {n: o for n, o in originales.items() if o.get("valida", True) and o.get("trazado") and o.get("paradas")}
    # 1ª pasada: cada línea por su cuenta, para saber qué calles usa cada una
    usadas = {}
    for num, orig in validas.items():
        ajustar(red, [tuple(p) for p in orig["trazado"][0]])
        usadas[num] = red.usadas
    print("1ª pasada hecha (calles de cada línea)")
    # 2ª pasada: consenso, las calles que usan las demás líneas tienen preferencia
    for num, orig in validas.items():
        if a.lineas and num not in a.lineas:
            continue
        cuenta = {}
        for n, u in usadas.items():
            if n != num:
                for k in u:
                    cuenta[k] = cuenta.get(k, 0) + 1
        red.compartidas = frozenset(k for k, c in cuenta.items() if c >= 2)   # al menos dos líneas más
        t0 = time.time()
        nuevas, ruta, extra = procesar(num, orig, red, osm, print)
        L = acumulado(ruta)[-1] / 1000
        escala = orig.get("km_total") or acumulado([tuple(p) for p in orig["trazado"][0]])[-1] / 1000
        for p in nuevas:   # k en la escala de tiempos original (no cambia la duración de la línea)
            p["k"] = round(p["k"] / 1000 * escala / L, 3) if L else p["k"]
        print(f"Línea {num}: original {acumulado([tuple(p) for p in orig['trazado'][0]])[-1] / 1000:.1f} km → calles {L:.1f} km "
              f"· {len(ruta)} puntos ({time.time() - t0:.0f} s)")
        datos[num] = {**orig, "paradas": nuevas,
                      "trazado": [[[round(la, 6), round(lo, 6)] for la, lo in ruta]] + [[[round(la, 6), round(lo, 6)] for la, lo in t] for t in extra],
                      "km_total": round(escala, 1), "km_calles": round(L, 1), "ajuste": "map-matching"}
    if not a.dry_run:
        salida.write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"Guardado {salida}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
