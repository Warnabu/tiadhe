#!/usr/bin/env python3
"""Reconstruye los trazados de datos/paradas_reales.json siguiendo la CARRETERA REAL (OSRM /route).

    python reconstruir_trazados.py               # todas las líneas
    python reconstruir_trazados.py 05 12         # solo esas
    python reconstruir_trazados.py --dry-run     # informa sin escribir
    python reconstruir_trazados.py --servidor http://localhost:5000   # OSRM propio

Por qué /route y no /match: los trazados de los mapas de Google tienen tramos rectos que cortan curvas y
manzanas; el "map matching" se pierde con ellos. En cambio, enrutar de parada en parada por la red viaria
da siempre un recorrido por carretera. El trazado original solo se usa como GUÍA:
  1. Cada parada se engancha a la vía en el SENTIDO de la marcha (parámetro bearings) para no caer en el
     carril contrario de una autovía (eso provocaba vueltas en rotondas y "lazos").
  2. Cada tramo entre dos paradas se compara con el trazado original (longitud y distancia media). Si se
     desvía, se repite con puntos de paso tomados del original y se queda el candidato que mejor encaja.
  3. Las ROTONDAS se sustituyen por una esquina en su centro (nada de polígonos ni vueltas completas).
  4. Se quitan "pinchos" (ir y volver por la misma calle) que no lleven a una parada.
  5. Cada parada se coloca en la marquesina de OpenStreetMap más cercana (≤40 m) o, si no hay, sobre la vía.
     Las que están a >120 m del recorrido y sin marquesina (desvíos o datos dudosos) se dejan donde están y se
     avisa. A todas se les recalcula 'k' (km desde el origen) sobre el trazado nuevo: el servidor lo usa para los tiempos.
  6. Si la ruta se aleja >100 m del original con las dos paradas sobre él (o una parada queda a >60 m de toda vía),
     falta esa calle en OpenStreetMap: ese tramo conserva el dibujo original.
     Por defecto 'k' se reescala a la longitud del trazado original (km_escala) para no cambiar la duración total
     de cada línea; solo cambia el reparto entre paradas. Con --km-reales se usan los km reales por carretera.
     El script es repetible: guarda km_escala y vuelve a partir de los datos ya reconstruidos sin perder la escala.

Respuestas de OSRM cacheadas en datos/.cache_osrm/ (repetir no vuelve a pedir nada).
"""
import argparse, hashlib, json, math, sys, time
from pathlib import Path

DATOS = Path(__file__).parent / "datos"
CACHE = DATOS / ".cache_osrm"
SERVIDOR = "https://router.project-osrm.org"
MAX_WP = 60               # puntos por petición /route
RADIO_SNAP = 120          # m: distancia máxima a la que una parada se engancha a la vía
OSM_MARQUESINA = 0.040    # km: si hay una parada de OSM así de cerca, se usa su posición
LEJOS_GUIA = 0.120        # km: parada más lejos del trazado original (y sin marquesina OSM) = mal situada
ACEPTA = 120              # puntuación máxima de un tramo (m de separación media + 400 por km de rodeo)
_ultima = [0.0]


# ------------------------------------------------------------------ geometría
def km(a, b):
    p = math.pi / 180
    h = math.sin((b[0] - a[0]) * p / 2) ** 2 + math.cos(a[0] * p) * math.cos(b[0] * p) * math.sin((b[1] - a[1]) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(min(1.0, h)))


def longitud(t):
    return sum(km(a, b) for a, b in zip(t, t[1:]))


def acumulado(t):
    acc = [0.0]
    for a, b in zip(t, t[1:]):
        acc.append(acc[-1] + km(a, b))
    return acc


def proyectar(t, acc, p, i0=0, i1=None):
    """(distancia_km, k_km, índice_segmento, punto) del punto p sobre t[i0:i1]."""
    i1 = len(t) - 1 if i1 is None else min(i1, len(t) - 1)
    c = math.cos(math.radians(p[0]))
    mejor = (1e9, 0.0, i0, t[i0])
    for i in range(i0, i1):
        a, b = t[i], t[i + 1]
        ax, ay, bx, by, px, py = a[1] * c, a[0], b[1] * c, b[0], p[1] * c, p[0]
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        u = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
        q = (ay + u * dy, (ax + u * dx) / c)
        d = km(q, p)
        if d < mejor[0]:
            mejor = (d, acc[i] + u * (acc[i + 1] - acc[i]), i, q)
    return mejor


def rumbo(a, b):
    """Rumbo de a→b en grados (0 = norte, sentido horario)."""
    la1, la2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    x = math.sin(dl) * math.cos(la2)
    y = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def punto_en(t, acc, k):
    """Punto de t a k km del inicio."""
    if k <= 0:
        return t[0]
    for i in range(1, len(t)):
        if acc[i] >= k:
            s = acc[i] - acc[i - 1]
            u = 0 if s == 0 else (k - acc[i - 1]) / s
            return (t[i - 1][0] + u * (t[i][0] - t[i - 1][0]), t[i - 1][1] + u * (t[i][1] - t[i - 1][1]))
    return t[-1]


def rumbo_en(t, acc, k, tramo=0.04):
    a, b = punto_en(t, acc, max(0.0, k - tramo)), punto_en(t, acc, min(acc[-1], k + tramo))
    return None if km(a, b) < 0.005 else rumbo(a, b)


def douglas_peucker(t, tol_m=2.0):
    if len(t) < 3:
        return list(t)
    c = math.cos(math.radians(t[0][0]))
    xy = [(p[1] * c * 111320, p[0] * 110540) for p in t]
    keep = [False] * len(t)
    keep[0] = keep[-1] = True
    pila = [(0, len(t) - 1)]
    while pila:
        i, j = pila.pop()
        (ax, ay), (bx, by) = xy[i], xy[j]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        mejor, im = -1.0, -1
        for m in range(i + 1, j):
            px, py = xy[m]
            d = math.hypot(px - ax, py - ay) if L == 0 else abs(dy * px - dx * py + bx * ay - by * ax) / L
            if d > mejor:
                mejor, im = d, m
        if mejor > tol_m:
            keep[im] = True
            pila += [(i, im), (im, j)]
    return [p for p, k in zip(t, keep) if k]


def centro_circulo(pts):
    """Ajuste de circunferencia (Kasa) → ((lat, lon), radio_m) o None."""
    if len(pts) < 3:
        return None
    lat0, lon0 = pts[0]
    c = math.cos(math.radians(lat0))
    xy = [((p[1] - lon0) * c * 111320, (p[0] - lat0) * 110540) for p in pts]
    n = len(xy)
    sx = sum(x for x, _ in xy); sy = sum(y for _, y in xy)
    sxx = sum(x * x for x, _ in xy); syy = sum(y * y for _, y in xy); sxy = sum(x * y for x, y in xy)
    sz = [x * x + y * y for x, y in xy]
    szx = sum(z * x for z, (x, _) in zip(sz, xy)); szy = sum(z * y for z, (_, y) in zip(sz, xy)); szz = sum(sz)
    # resolver [sxx sxy sx; sxy syy sy; sx sy n] [A B C]^T = [szx szy szz]
    M = [[sxx, sxy, sx, szx], [sxy, syy, sy, szy], [sx, sy, n, szz]]
    for i in range(3):
        piv = max(range(i, 3), key=lambda r: abs(M[r][i]))
        if abs(M[piv][i]) < 1e-9:
            return None
        M[i], M[piv] = M[piv], M[i]
        for r in range(3):
            if r != i:
                f = M[r][i] / M[i][i]
                M[r] = [a - f * b for a, b in zip(M[r], M[i])]
    A, B, C = (M[i][3] / M[i][i] for i in range(3))
    cx, cy = A / 2, B / 2
    r2 = C + cx * cx + cy * cy
    if r2 <= 0:
        return None
    return (lat0 + cy / 110540, lon0 + cx / (c * 111320)), math.sqrt(r2)


# ------------------------------------------------------------------ OSRM
def osrm(servidor, wps, bearings=None, continue_straight=True, radio=RADIO_SNAP):
    import requests
    cs = ";".join(f"{lo:.6f},{la:.6f}" for la, lo in wps)
    params = dict(overview="full", geometries="geojson", steps="true", continue_straight=str(continue_straight).lower(),
                  radiuses=";".join(str(radio) for _ in wps))
    if bearings:
        params["bearings"] = ";".join("" if b is None else f"{round(b) % 360},60" for b in bearings)
    clave = hashlib.sha1((servidor + cs + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    f = CACHE / f"r_{clave}.json"
    if f.exists():
        return json.loads(f.read_text())
    for intento in range(5):
        espera = 1.05 - (time.time() - _ultima[0])
        if espera > 0:
            time.sleep(espera)
        _ultima[0] = time.time()
        try:
            r = requests.get(f"{servidor}/route/v1/driving/{cs}", params=params, timeout=90,
                             headers={"User-Agent": "guaguas-fuerteventura"})
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(4 * (intento + 1)); continue
            j = r.json()
            if j.get("code") != "Ok":
                j = None
            else:  # se guarda solo lo necesario
                j = dict(waypoints=[dict(location=w["location"], distance=w["distance"]) for w in j["waypoints"]],
                         legs=[dict(distance=l["distance"], duration=l["duration"],
                                    steps=[dict(type=s["maneuver"]["type"], coords=s["geometry"]["coordinates"]) for s in l["steps"]])
                               for l in j["routes"][0]["legs"]])
            CACHE.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(j))
            return j
        except Exception as e:
            print(f"      (red: {e}; reintento {intento + 1}/5)")
            time.sleep(3 * (intento + 1))
    return None


def geometria_tramo(leg):
    """Geometría de un tramo [(lat, lon)] con las rotondas sustituidas por su centro.
    Devuelve (puntos, centros) donde centros es el conjunto de índices que son esquinas de rotonda."""
    pasos = leg["steps"]
    out, rot = [], set()
    i = 0
    while i < len(pasos):
        s = pasos[i]
        pts = [(la, lo) for lo, la in s["coords"]]
        if s["type"] in ("roundabout", "rotary") and len(pts) >= 2:
            # el anillo puede venir partido en varios pasos seguidos
            anillo = list(pts)
            while i + 1 < len(pasos) and pasos[i + 1]["type"] in ("roundabout", "rotary"):
                i += 1
                anillo += [(la, lo) for lo, la in pasos[i]["coords"]][1:]
            ajuste = centro_circulo(anillo)
            arco = longitud(anillo) * 1000
            if ajuste and 4 <= ajuste[1] <= 90 and arco >= 0.4 * ajuste[1]:
                centro, r = ajuste
            else:
                centro, r = ((anillo[0][0] + anillo[-1][0]) / 2, (anillo[0][1] + anillo[-1][1]) / 2), 12.0
            # quitar lo que ya está dentro de la rotonda por el lado de entrada
            while len(out) > 1 and km(out[-1], centro) * 1000 < r * 1.25:
                out.pop()
            out.append(centro)
            rot.add(len(out) - 1)
            # y por el lado de salida (el siguiente paso empieza en el anillo)
            if i + 1 < len(pasos):
                sig = [(la, lo) for lo, la in pasos[i + 1]["coords"]]
                j = 0
                while j < len(sig) - 1 and km(sig[j], centro) * 1000 < r * 1.25:
                    j += 1
                out += sig[j:]
                i += 2
                continue
            i += 1
            continue
        if out and pts and km(out[-1], pts[0]) < 0.0005:
            pts = pts[1:]
        out += pts
        i += 1
    return out, rot


def quitar_pinchos(t, protegidos, max_km=0.35):
    """Quita bucles cortos (el recorrido vuelve a pasar a <12 m del mismo punto tras ≤max_km): vueltas a una
    manzana, idas y vueltas, rotondas sin etiquetar… Si dentro del bucle hay puntos protegidos (paradas, esquinas
    de rotonda) se cambia por una ida y vuelta recta a ellos, sin formas; si no, se elimina."""
    protegidos = list(protegidos)
    cambiado = True
    while cambiado:
        cambiado = False
        acc = acumulado(t)
        for i in range(len(t)):
            for j in range(i + 2, len(t)):
                if acc[j] - acc[i] > max_km:
                    break
                if km(t[i], t[j]) >= 0.012:
                    continue
                dentro = sorted({p for p in protegidos if i < p < j})
                if j - i - 1 <= len(dentro):
                    continue  # ya es una ida y vuelta mínima
                nuevo = t[:i + 1] + [t[p] for p in dentro] + t[j:]
                pos = {p: i + 1 + n for n, p in enumerate(dentro)}
                quitados = (j - i - 1) - len(dentro)
                protegidos = [pos[p] if p in pos else (p if p <= i else p - quitados) for p in protegidos]
                t = nuevo
                cambiado = True
                break
            if cambiado:
                break
    return t, protegidos


# ------------------------------------------------------------------ línea
def distancia_media(guia, cand, muestras=25):
    """Distancia media (m) de puntos de la guía al candidato."""
    if len(guia) < 2 or len(cand) < 2:
        return 0.0
    acc_g, acc_c = acumulado(guia), acumulado(cand)
    L = acc_g[-1]
    if L == 0:
        return 0.0
    ds = [proyectar(cand, acc_c, punto_en(guia, acc_g, L * m / (muestras - 1)))[0] * 1000 for m in range(muestras)]
    return sum(ds) / len(ds)


def subtrazo(t, acc, k0, k1):
    pts = [punto_en(t, acc, k0)] + [p for p, a in zip(t, acc) if k0 < a < k1] + [punto_en(t, acc, k1)]
    return pts


def puntuar(guia, cand_pts, cand_len):
    Lg = longitud(guia)
    dm = distancia_media(guia, cand_pts)
    exceso = max(0.0, cand_len - 1.25 * Lg - 0.15)  # km de más respecto al original
    return dm + 400 * exceso, dm, exceso


def ruta_tramo(servidor, a, b, guia_ab, rumbos_ab):
    """Candidatos de ruta para un tramo a→b (con puntos de paso de la guía) → lista de (nombre, tramo)."""
    cands = []
    Lg = longitud(guia_ab)
    ac = acumulado(guia_ab)
    # de más barato a más caro; se para en cuanto uno encaja con la guía
    configs = [(None, cont, rum) for rum in (True, False) for cont in (True, False)]
    configs += [(0.4, True, True), (0.4, False, True), (0.15, False, True), (0.15, False, False)]
    for paso, cont, rum in configs:
        n = int(Lg / paso) if paso else 0
        ks = [Lg * m / (n + 1) for m in range(1, n + 1)] if n else []
        pts = [a] + [punto_en(guia_ab, ac, k) for k in ks] + [b]
        if len(pts) > MAX_WP or (paso and not n):
            continue
        bs = [rumbos_ab[0]] + [rumbo_en(guia_ab, ac, k) for k in ks] + [rumbos_ab[1]] if rum else None
        r = osrm(servidor, pts, bs, continue_straight=cont)
        if not r:
            continue
        geom, rot = [], set()
        for leg in r["legs"]:
            g, rr = geometria_tramo(leg)
            if geom and g and km(geom[-1], g[0]) < 0.0005:
                g = g[1:]
                rr = {x - 1 for x in rr if x > 0}
            rot |= {x + len(geom) for x in rr}
            geom += g
        nombre = (f"vías cada {paso} km" if paso else "directo") + ("" if rum else ", sin rumbo") + ("" if cont else ", con giros")
        cands.append((nombre, dict(geom=geom, rot=rot, dist=sum(l["distance"] for l in r["legs"]) / 1000)))
        if puntuar(guia_ab, geom, cands[-1][1]["dist"])[0] < ACEPTA:
            break
    return cands


def simplificar_con_fijos(geom, fijos, tol_m=2.0):
    """Douglas-Peucker por trozos sin tocar los índices fijos. Devuelve (geom, mapa índice_viejo→nuevo)."""
    fijos = sorted(set(f for f in fijos if 0 <= f < len(geom)) | {0, len(geom) - 1})
    out, mapa = [geom[0]], {0: 0}
    for a, b in zip(fijos, fijos[1:]):
        seg = douglas_peucker(geom[a:b + 1], tol_m)
        out += seg[1:]
        mapa[b] = len(out) - 1
    return out, mapa


def reconstruir(num, linea, osm, servidor, log):
    tr = linea["trazado"]
    partes = [tr] if tr and isinstance(tr[0][0], (int, float)) else tr
    guia = [tuple(p) for p in partes[0]]
    acc = acumulado(guia)
    paradas = sorted(linea["paradas"], key=lambda p: p["k"])
    # k puede estar en otra escala (km_total) que la longitud de la guía (p. ej. al repetir sobre datos ya reconstruidos)
    escala = acc[-1] / linea["km_total"] if linea.get("km_total") else 1.0

    # 1) ¿dónde está cada parada y cuánto nos fiamos de ella?
    #    fiable = hay una marquesina de OSM a ≤40 m (se usa esa posición) o está a ≤120 m del trazado original.
    for p in paradas:
        s = (p["lat"], p["lon"])
        o = min(osm, key=lambda q: km(s, q), default=None)
        dg = proyectar(guia, acc, s)[0]
        if o and km(s, o) <= OSM_MARQUESINA:
            p["_pos"], p["_fiable"] = o, True
        else:
            p["_pos"], p["_fiable"] = s, dg <= LEJOS_GUIA
        # posición sobre la guía: proyección, pero solo cerca de su k (las líneas que pasan dos veces por el mismo sitio)
        k0 = min(max(p["k"] * escala, 0.0), acc[-1])
        i0 = next((i for i, x in enumerate(acc) if x >= k0 - 3.0), 0)
        i1 = next((i for i, x in enumerate(acc) if x > k0 + 3.0), len(acc) - 1)
        p["_kg"] = proyectar(guia, acc, s, max(0, i0 - 1), i1)[1]
        if not p["_fiable"]:
            log(f"   · {p['n']}: a {dg * 1000:.0f} m del recorrido y sin marquesina en OSM → se deja donde está (¿desvío?), revisar")

    for a_, b_ in zip(paradas, paradas[1:]):   # orden monótono
        b_["_kg"] = max(b_["_kg"], a_["_kg"])

    # puntos de ruta: inicio del trazado, paradas fiables (sin duplicados), final del trazado
    wps = []  # dict(pt, kg, paradas=[índices])
    fiables = [i for i, p in enumerate(paradas) if p["_fiable"]]
    if not fiables or paradas[fiables[0]]["_kg"] > 0.15:
        wps.append(dict(pt=guia[0], kg=0.0, paradas=[]))
    for i in fiables:
        p = paradas[i]
        if wps and km(wps[-1]["pt"], p["_pos"]) < 0.005:
            wps[-1]["paradas"].append(i)
            continue
        wps.append(dict(pt=p["_pos"], kg=p["_kg"], paradas=[i]))
    if not fiables or acc[-1] - paradas[fiables[-1]]["_kg"] > 0.15:
        wps.append(dict(pt=guia[-1], kg=acc[-1], paradas=[]))
    rumbos = [rumbo_en(guia, acc, w["kg"]) for w in wps]

    # 2) ruta completa por bloques
    tramos = []
    i = 0
    while i < len(wps) - 1:
        bloque = list(range(i, min(i + MAX_WP, len(wps))))
        pts = [wps[j]["pt"] for j in bloque]
        r = osrm(servidor, pts, [rumbos[j] for j in bloque]) or osrm(servidor, pts)
        if r is None:
            tramos += [None] * (len(bloque) - 1)
        else:
            for leg in r["legs"]:
                g, rot = geometria_tramo(leg)
                tramos.append(dict(geom=g, rot=rot, dist=leg["distance"] / 1000))
        i = bloque[-1]

    # 3) validar cada tramo contra la guía; los malos se repiten con puntos de paso
    dudosos = 0
    for t_i in range(len(wps) - 1):
        a, b = wps[t_i], wps[t_i + 1]
        g_ab = subtrazo(guia, acc, a["kg"], b["kg"]) if b["kg"] > a["kg"] else []
        g_ab = [a["pt"]] + g_ab[1:-1] + [b["pt"]]
        actual = tramos[t_i]
        cands = [("directo", actual)] if actual else []
        if not actual or puntuar(g_ab, actual["geom"], actual["dist"])[0] >= ACEPTA:
            cands += ruta_tramo(servidor, a["pt"], b["pt"], g_ab, (rumbos[t_i], rumbos[t_i + 1]))
            if not cands:
                cands.append(("recta", dict(geom=[a["pt"], b["pt"]], rot=set(), dist=km(a["pt"], b["pt"]))))
            nombre, mejor = min(cands, key=lambda c: puntuar(g_ab, c[1]["geom"], c[1]["dist"])[0])
            sc, dm, ex = puntuar(g_ab, mejor["geom"], mejor["dist"])
            if sc >= ACEPTA and dm > 100 and all(proyectar(guia, acc, w["pt"])[0] < 0.06 for w in (a, b)):
                # las dos paradas están sobre el trazado original y aun así la ruta se va lejos: falta una vía en
                # OpenStreetMap (calle nueva, pista…). Se conserva el dibujo original de ese tramo.
                log(f"   ~ tramo sin vía en OSM: se conserva el trazado original ({dm:.0f} m de separación con la ruta)")
                nombre, mejor = "trazado original", dict(geom=g_ab, rot=set(), dist=longitud(g_ab))
                sc, dm, ex = puntuar(g_ab, mejor["geom"], mejor["dist"])
            if sc >= ACEPTA:
                dudosos += 1
                nom = lambda w, d: paradas[w["paradas"][0]]["n"] if w["paradas"] else d
                log(f"   ? {nom(a, 'inicio')} → {nom(b, 'final')}: a {dm:.0f} m de media del original"
                    f"{f', {ex:.2f} km más largo' if ex > 0 else ''} (usado: {nombre})")
            tramos[t_i] = mejor

    # 3b) paradas sobre el trazado original que OSRM solo alcanza a >60 m: no hay vía en OSM hasta ellas.
    #     Los dos tramos que las tocan conservan el dibujo original y la parada se queda donde está.
    def guia_tramo(t_i):
        a, b = wps[t_i], wps[t_i + 1]
        g = subtrazo(guia, acc, a["kg"], b["kg"]) if b["kg"] > a["kg"] else []
        g = [a["pt"]] + g[1:-1] + [b["pt"]]
        return dict(geom=g, rot=set(), dist=longitud(g))
    for w_i, w in enumerate(wps):
        llega = tramos[w_i - 1]["geom"][-1] if w_i > 0 and tramos[w_i - 1]["geom"] else w["pt"]
        sale = tramos[w_i]["geom"][0] if w_i < len(tramos) and tramos[w_i]["geom"] else w["pt"]
        if max(km(llega, w["pt"]), km(sale, w["pt"])) > 0.06 and proyectar(guia, acc, w["pt"])[0] < 0.03:
            for t_i in (w_i - 1, w_i):
                if 0 <= t_i < len(tramos):
                    tramos[t_i] = guia_tramo(t_i)
            nom = paradas[w["paradas"][0]]["n"] if w["paradas"] else "extremo"
            log(f"   ~ {nom}: sin vía en OSM a menos de 60 m → se conserva el trazado original a su alrededor")

    # 4) unir tramos; cada punto de ruta queda en la unión de dos tramos
    geom, rot, idx_wp = [], set(), [0]
    for t_i, tm in enumerate(tramos):
        g = list(tm["geom"]) if tm["geom"] else [wps[t_i]["pt"], wps[t_i + 1]["pt"]]
        rr = set(tm["rot"])
        if geom:
            g, rr = g[1:], {x - 1 for x in rr if x > 0}
        rot |= {x + len(geom) for x in rr}
        geom += g
        idx_wp.append(len(geom) - 1)
    rot = sorted(rot)
    geom, prot = quitar_pinchos(geom, idx_wp + rot)
    idx_wp, rot = prot[:len(idx_wp)], prot[len(idx_wp):]
    geom, mapa = simplificar_con_fijos(geom, idx_wp + rot)
    idx_wp = [mapa[i] for i in idx_wp]
    geom = [(round(la, 6), round(lo, 6)) for la, lo in geom]
    acc_n = acumulado(geom)

    # 5) paradas sobre el trazado nuevo y k (monótono por construcción)
    nuevas, avisos = [None] * len(paradas), []
    for w_i, w in enumerate(wps):
        for i in w["paradas"]:
            p, q = paradas[i], geom[idx_wp[w_i]]
            pos = p["_pos"] if km(p["_pos"], q) <= 0.025 else q   # a >25 m de la vía → sobre la vía
            nuevas[i] = (pos, acc_n[idx_wp[w_i]])
    for i, p in enumerate(paradas):
        if nuevas[i]:
            continue
        prev = max([idx_wp[w] for w in range(len(wps)) if any(j < i for j in wps[w]["paradas"])] + [0])
        sig = min([idx_wp[w] for w in range(len(wps)) if any(j > i for j in wps[w]["paradas"])] + [len(geom) - 1])
        d, k, _, q = proyectar(geom, acc_n, p["_pos"], prev, max(sig, prev + 1))
        # lejos del recorrido y sin marquesina: suele ser una parada de desvío (muelle, hotel…). No se inventa
        # otra posición: se deja donde está y solo se calcula su k sobre el recorrido.
        nuevas[i] = (p["_pos"], k)
    res = []
    for p, (pos, k) in zip(paradas, nuevas):
        movida = km((p["lat"], p["lon"]), pos) * 1000
        if movida > 60:
            avisos.append(f"{p['n']}: movida {movida:.0f} m")
        n = {k2: v for k2, v in p.items() if not k2.startswith("_")}
        n.update(lat=round(pos[0], 6), lon=round(pos[1], 6), k=round(k, 3))
        res.append(n)

    # partes extra (ramales cortos): de extremo a extremo con puntos de paso
    extra = []
    for parte in partes[1:]:
        g = [tuple(p) for p in parte]
        ac = acumulado(g)
        cands = ruta_tramo(servidor, g[0], g[-1], g, (rumbo_en(g, ac, 0.0), rumbo_en(g, ac, ac[-1])))
        if cands:
            _, mejor = min(cands, key=lambda c: puntuar(g, c[1]["geom"], c[1]["dist"])[0])
            if puntuar(g, mejor["geom"], mejor["dist"])[0] < ACEPTA:
                gg, _ = quitar_pinchos(mejor["geom"], [0, len(mejor["geom"]) - 1] + sorted(mejor["rot"]))
                g = douglas_peucker(gg, 2.0)
        extra.append([[round(la, 6), round(lo, 6)] for la, lo in g])
    return res, geom, extra, dudosos, avisos


def cargar_osm():
    f = DATOS / "paradas_osm.json"
    return [(p["lat"], p["lon"]) for p in json.loads(f.read_text(encoding="utf-8"))] if f.exists() else []


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("lineas", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--servidor", default=SERVIDOR)
    ap.add_argument("--km-reales", action="store_true", help="k en km reales por carretera (cambia los tiempos estimados)")
    ap.add_argument("--salida", help="escribir el resultado en este archivo en vez de datos/paradas_reales.json")
    a = ap.parse_args(argv)
    f = DATOS / "paradas_reales.json"
    datos = json.loads(f.read_text(encoding="utf-8"))
    osm = cargar_osm()
    total_malos = 0
    for num, linea in datos.items():
        if a.lineas and num not in a.lineas:
            continue
        if not linea.get("valida", True) or not linea.get("trazado") or not linea.get("paradas"):
            print(f"Línea {num}: sin datos válidos, se omite")
            continue
        tr = linea["trazado"]
        L_guia = longitud(tr if isinstance(tr[0][0], (int, float)) else tr[0])
        avisos = []
        nuevas, geom, extra, malos, av = reconstruir(num, linea, osm, a.servidor, avisos.append)
        total_malos += malos
        L = longitud(geom)
        # Escala de tiempos: el servidor convierte k en minutos (y algunas líneas están calibradas con Moovit sobre
        # la escala antigua). Se conserva la longitud total antigua y solo se corrige la posición RELATIVA de cada
        # parada; la longitud real por carretera queda en km_carretera.
        if a.km_reales:
            fac = 1.0
        else:
            fac = (linea.get("km_escala") or linea.get("km_total") or L_guia) / L if L else 1.0
        for p in nuevas:
            p["k"] = round(p["k"] * fac, 3)
        print(f"Línea {num}: trazado {L_guia:.1f} km → carretera {L:.1f} km · {len(geom)} puntos · {malos} tramos dudosos")
        for x in avisos + av:
            print("  ", x)
        linea["paradas"] = nuevas
        linea["trazado"] = [[list(p) for p in geom]] + extra   # siempre lista de tramos, como importar_mapas.py
        linea["km_escala"] = round(linea.get("km_escala") or linea.get("km_total") or L_guia, 3)
        linea["km_total"] = round(L * fac, 1)
        linea["km_carretera"] = round(L, 1)
        linea["reconstruido"] = "osrm-route"
    if a.salida:
        f = Path(a.salida)
    if not a.dry_run:
        f.write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"Guardado {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
