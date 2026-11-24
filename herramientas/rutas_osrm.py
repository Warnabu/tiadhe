#!/usr/bin/env python3
"""Calcula el recorrido de cada línea como UN trazo continuo por las calles, pasando por sus paradas en orden.

    python herramientas/rutas_osrm.py              # todas las líneas → datos/paradas_reales.json
    python herramientas/rutas_osrm.py 05 12        # solo esas
    python herramientas/rutas_osrm.py --dry-run    # solo el informe

Cómo:
  1. Las paradas de la línea, en su orden, se sitúan sobre el recorrido que dibujó Tiadhe (trazados_originales.json)
     para saber en qué sentido pasa la guagua por cada una.
  2. Se pide la ruta a OSRM (router.project-osrm.org, calles de OpenStreetMap con su geometría completa: las rotondas
     son redondas, los enlaces están enteros) pasando por todas las paradas y por puntos guía del dibujo de Tiadhe
     entre paradas lejanas. A cada punto se le da el sentido de la marcha (así toma la calzada correcta de una
     autovía y no da medias vueltas) y se prohíbe dar la vuelta en ellos.
  3. Se compara con el dibujo de Tiadhe: donde la ruta se aparta más de DESVIO_M, se añaden más puntos guía ahí y se
     repite. Lo que siga apartándose sale en el informe.
  4. 'k' de cada parada (km desde el origen) se recalcula sobre el trazo y se reescala a la longitud oficial, para no
     cambiar los tiempos.
  6. Consenso: si una línea se separa sola de las demás en un tramo corto y vuelve a juntarse, va con ellas.
  5. Se quitan los muñones (ida y vuelta a una calle sin parada) y se respetan las vueltas que dibujó Tiadhe por un
     pueblo hasta una parada aunque OSRM no pueda circular por ellas (reglas de limpiar_trazados.py).

Las respuestas de OSRM se guardan en datos/.cache_osrm/rutas/ (se puede repetir sin volver a pedirlas).
"""
import argparse, hashlib, json, math, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ajustar_rutas import DATOS, acumulado, m, proyectar_seg  # noqa: E402
import limpiar_trazados  # noqa: E402
from limpiar_trazados import RedLineas, quitar_ramales, unificar  # noqa: E402
from ajustar_rutas import Red, teselas_necesarias  # noqa: E402

SERVIDOR = "https://router.project-osrm.org"
CACHE = DATOS / ".cache_osrm" / "rutas"
GUIA_M = 700          # punto guía del dibujo original cada tanto entre paradas lejanas
DESVIO_M = 120        # la ruta no debe apartarse del dibujo original más que esto…
DESVIO_LARGO_M = 250  # …durante más de esto
MAX_PUNTOS = 90       # puntos por consulta (el servidor admite 100)
VUELTAS = 4
CONSENSO_DIBUJO_MAX_M = 3500   # … o esto, en las líneas que tuvieron que seguir el dibujo de Tiadhe en algún tramo
CONSENSO_MAX_M = 1500 # tramo máximo en el que una línea, sola, se separa de las demás y vuelve a juntarse
MUNON_MAX_M = 400     # un ida y vuelta sin parada más corto que esto es un muñón de un punto guía mal enganchado
RODEO_MAX_M = 600     # sin_rodeos: diferencia máxima que se corrige (más es un desvío de verdad)
RODEO_PARADA_M = 80   # entrar hasta una parada pegada a la ruta no puede costar más que esto de rodeo
MAS_LARGO = 1.4       # un tramo por calles más largo que esto × el dibujo (+150 m) es un rodeo: se usa el dibujo


# ------------------------------------------------------------------ geometría
def rumbo(a, b):
    """Rumbo (grados, 0 = norte) de a hacia b."""
    y = math.sin(math.radians(b[1] - a[1])) * math.cos(math.radians(b[0]))
    x = math.cos(math.radians(a[0])) * math.sin(math.radians(b[0])) - \
        math.sin(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.cos(math.radians(b[1] - a[1]))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def punto_en(t, acc, d):
    d = max(0.0, min(acc[-1], d))
    for i in range(1, len(t)):
        if acc[i] >= d:
            s = acc[i] - acc[i - 1]
            u = 0 if s == 0 else (d - acc[i - 1]) / s
            return (t[i - 1][0] + u * (t[i][0] - t[i - 1][0]), t[i - 1][1] + u * (t[i][1] - t[i - 1][1])), i
    return t[-1], len(t) - 1


def proyectar(t, acc, p, cerca_de=None, ventana=1500):
    """(distancia, posición en m) de p sobre t; si cerca_de, solo a ±ventana de esa posición."""
    mejor = (1e18, 0.0)
    for i in range(len(t) - 1):
        if cerca_de is not None and (acc[i + 1] < cerca_de - ventana or acc[i] > cerca_de + ventana):
            continue
        d, u, _ = proyectar_seg(p, t[i], t[i + 1])
        if d < mejor[0]:
            mejor = (d, acc[i] + u * (acc[i + 1] - acc[i]))
    return mejor


def dist_linea(p, t, rejilla):
    ci, cj = int(p[0] / 0.002), int(p[1] / 0.002)
    segs = {i for di in (-1, 0, 1) for dj in (-1, 0, 1) for i in rejilla.get((ci + di, cj + dj), ())}
    return min((proyectar_seg(p, t[i], t[i + 1])[0] for i in segs), default=1e9)


def rejilla_de(t):
    r = {}
    for i in range(len(t) - 1):
        for p in (t[i], t[i + 1], ((t[i][0] + t[i + 1][0]) / 2, (t[i][1] + t[i + 1][1]) / 2)):
            r.setdefault((int(p[0] / 0.002), int(p[1] / 0.002)), set()).add(i)
    return r


# ------------------------------------------------------------------ OSRM
def osrm(puntos):
    """puntos: [(lat, lon, rumbo o None, radio)] → [(lat, lon)] de la ruta, o None."""
    coords = ";".join(f"{lo:.6f},{la:.6f}" for la, lo, _, _ in puntos)
    bearings = ";".join(f"{round(b) % 360},60" if b is not None else "" for _, _, b, _ in puntos)
    radios = ";".join(str(r) for _, _, _, r in puntos)
    url = (f"{SERVIDOR}/route/v1/driving/{coords}?overview=full&geometries=geojson&continue_straight=true"
           f"&bearings={bearings}&radiuses={radios}")
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".json")
    if f.exists():
        r = json.loads(f.read_text())
    else:
        for intento in range(4):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "guaguas-ftv-herramientas/1.0"})
                r = json.load(urllib.request.urlopen(req, timeout=90))
                break
            except urllib.error.HTTPError as e:
                r = json.loads(e.read() or b"{}")
                if r.get("code"):
                    break
                time.sleep(3 * (intento + 1))
            except Exception:
                time.sleep(3 * (intento + 1))
        else:
            return None
        f.write_text(json.dumps(r))
        time.sleep(1.1)   # servidor de demostración: con calma
    if r.get("code") != "Ok":
        return None
    return [(la, lo) for lo, la in r["routes"][0]["geometry"]["coordinates"]]


def ruta_por(puntos):
    """Ruta por todos los puntos, en trozos de MAX_PUNTOS que se solapan en un punto."""
    res, i = [], 0
    while i < len(puntos) - 1:
        trozo = puntos[i:i + MAX_PUNTOS]
        r = osrm(trozo)
        if r is None:
            return None, i
        res += r if not res else r[1:]
        i += MAX_PUNTOS - 1
    return res, None


# ------------------------------------------------------------------ una línea
def lado(t, acc, x, p):
    """Distancia (m) con signo de p al recorrido en la posición x: > 0 a la izquierda, < 0 a la derecha."""
    a, _ = punto_en(t, acc, x - 15)
    b, _ = punto_en(t, acc, x + 15)
    c = math.cos(math.radians(a[0]))
    dx, dy = (b[1] - a[1]) * c * 111320, (b[0] - a[0]) * 110540
    q, _ = punto_en(t, acc, x)
    vx, vy = (p[1] - q[1]) * c * 111320, (p[0] - q[0]) * 110540
    n = math.hypot(dx, dy) or 1
    return (dx * vy - dy * vx) / n


def paradas_de_ida(paradas, pos, t, acc, log):
    """De cada pareja de paradas enfrentadas (una por sentido) se queda la de la derecha (aquí se circula por la
    derecha): el trazo de la línea es el de ida y no debe cruzar la calle de una a otra."""
    quitar = set()
    for i in range(len(paradas)):
        for j in range(i + 1, len(paradas)):
            a, b = paradas[i], paradas[j]
            if abs(pos[i] - pos[j]) < 150 and m((a["lat"], a["lon"]), (b["lat"], b["lon"])) < 150:
                # la que está más a la izquierda de la otra es la del otro sentido
                x = (pos[i] + pos[j]) / 2
                li, lj = lado(t, acc, x, (a["lat"], a["lon"])), lado(t, acc, x, (b["lat"], b["lon"]))
                if abs(li - lj) > 4:
                    quitar.add(i if li > lj else j)
    if quitar:
        log(f"  {len(quitar)} paradas son del sentido contrario (enfrente de otra): no se usan para el trazo")
    return quitar


con_dibujo = {}   # línea → si algún tramo tuvo que seguir el dibujo de Tiadhe


def trazar(pts, t, acc, sentido):
    """Une los puntos (en orden) con tramos de OSRM. Si un tramo falla y sale de un punto guía, ese punto se quita y se
    vuelve a empezar; si sale de una parada, ese tramo sigue el dibujo de Tiadhe (desde donde acabó la ruta, por los
    puntos del dibujo entre ambos, en orden). Nunca une con una recta dos tramos que no se tocan."""
    pts = list(pts)
    por_dibujo = set()   # índices (en pts) de paradas desde las que el tramo siguiente va por el dibujo
    while True:
        ruta, saltados, dibujo, i, fallo = [], [], [], 0, None
        while i < len(pts) - 1:
            a_ = pts[i]
            if i in por_dibujo:
                b_ = pts[i + 1]
                trozo = [q for q, d in zip(t, acc) if a_["x"] < d < b_["x"]] + [b_["p"]]
                ruta += trozo if ruta else [a_["p"]] + trozo
                dibujo.append((round(a_["p"][0], 5), round(a_["p"][1], 5), round(b_["x"] - a_["x"])))
                i += 1
                continue
            hecho = False
            def pt(q, sin_sentido=False):
                return (q["p"][0], q["p"][1], None if (sin_sentido or q.get("libre")) else sentido(q["x"]), 60 if q["parada"] else 80)
            if i + 2 < len(pts) and pts[i + 1]["parada"] and not pts[i + 1].get("libre"):
                # parada a la que solo se llega dando un rodeo (p. ej. una vía de servicio en La Ampuyenta) y que queda
                # junto a la ruta directa: la línea sigue recta (la parada no cambia, solo no se dibuja la entrada)
                rd = osrm([pt(a_), pt(pts[i + 2])])
                r1, r2 = osrm([pt(a_), pt(pts[i + 1])]), osrm([pt(pts[i + 1]), pt(pts[i + 2])])
                if rd and r1 and r2 and acumulado(r1)[-1] + acumulado(r2)[-1] > acumulado(rd)[-1] + RODEO_PARADA_M \
                        and dist_linea(pts[i + 1]["p"], rd, rejilla_de(rd)) < 40 \
                        and acumulado(rd)[-1] <= MAS_LARGO * (pts[i + 2]["x"] - a_["x"]) + 150 \
                        and not (ruta and m(ruta[-1], rd[0]) > 20):
                    ruta += rd if not ruta else rd[1:]
                    saltados.append(pts[i + 1])
                    i += 2
                    continue
            for j in range(i + 1, min(i + 4, len(pts))):
                b_ = pts[j]
                ini = (a_["p"][0], a_["p"][1], None if a_.get("libre") else sentido(a_["x"]), 60 if a_["parada"] else 80)
                r = osrm([ini, (b_["p"][0], b_["p"][1], None if b_.get("libre") else sentido(b_["x"]), 60 if b_["parada"] else 80)])
                if r is None and j == len(pts) - 1:   # sin sentido en el destino: solo la última parada (fondo de saco)
                    r = osrm([ini, (b_["p"][0], b_["p"][1], None, 60)])
                if r is None and a_["parada"]:
                    # saliendo de una parada en un fondo de saco la guagua da la vuelta (p. ej. el Hotel Gorriones)
                    r = osrm([(a_["p"][0], a_["p"][1], None, 60),
                              (b_["p"][0], b_["p"][1], None if b_.get("libre") else sentido(b_["x"]), 60 if b_["parada"] else 80)])
                if r and acumulado(r)[-1] > MAS_LARGO * (b_["x"] - a_["x"]) + 150:
                    r = None   # rodeo: la calle que usa la guagua no está (bien) en el mapa de calles
                if r and ruta and m(ruta[-1], r[0]) > 20:
                    # el tramo anterior acabó enganchado en otra calle: se une por calles, nunca con una recta
                    puente = osrm([(ruta[-1][0], ruta[-1][1], None, 30), (r[0][0], r[0][1], None, 30)])
                    r = puente[:-1] + r if puente and acumulado(puente)[-1] <= 1.5 * m(ruta[-1], r[0]) + 50 else None
                if r:
                    ruta += r if not ruta else r[1:]
                    saltados += pts[i + 1:j]
                    i, hecho = j, True
                    break
            if not hecho:
                fallo = i
                break
        if fallo is None:
            return ruta, saltados, dibujo
        if not pts[fallo]["parada"] and fallo > 0:
            pts.pop(fallo)   # punto guía sin salida (p. ej. en la calzada que no es): fuera, y otra vez
            por_dibujo = {k - (k > fallo) for k in por_dibujo}
        else:
            por_dibujo.add(fallo)


def sin_rodeos(ruta, paradas, log, otras=()):
    """Entre dos paradas seguidas, si el trazo es mucho más largo que el camino directo por calles (con el sentido de la
    marcha), se usa el directo: los puntos guía del dibujo de Tiadhe a veces obligan a dar vueltas que la guagua no da
    (la «rotonda» dibujada junto al Hotel Gorriones en la 05)."""
    acc = acumulado(ruta)
    pos = []
    for q in paradas:
        d, x = min(((proyectar_seg(q, ruta[i], ruta[i + 1])[0], acc[i] + proyectar_seg(q, ruta[i], ruta[i + 1])[1] * (acc[i + 1] - acc[i]))
                    for i in range(len(ruta) - 1) if abs(ruta[i][0] - q[0]) < 0.01 and abs(ruta[i][1] - q[1]) < 0.01),
                   default=(1e9, 0))
        if d < 40 and (not pos or x > pos[-1][0]):
            pos.append((x, q))
    cortes = []
    for (x0, a), (x1, b) in zip(pos, pos[1:]):
        if x1 - x0 < 300:
            continue
        p0, i0 = punto_en(ruta, acc, x0)
        p1, i1 = punto_en(ruta, acc, x1)
        rumbo0 = rumbo(punto_en(ruta, acc, x0 - 20)[0], punto_en(ruta, acc, x0 + 20)[0])
        rumbo1 = rumbo(punto_en(ruta, acc, x1 - 20)[0], punto_en(ruta, acc, x1 + 20)[0])
        r = osrm([(p0[0], p0[1], rumbo0, 30), (p1[0], p1[1], rumbo1, 30)])
        if r and acumulado(r)[-1] * 1.3 + 200 < x1 - x0 and m(r[0], p0) < 20 and m(r[-1], p1) < 20:
            cortes.append((i0, i1, p0, p1, r, x1 - x0))
            continue
        # sin forzar el sentido de llegada: si es más corto y es el camino que hacen otras líneas, ese es
        r = osrm([(p0[0], p0[1], rumbo0, 30), (p1[0], p1[1], None, 30)])
        if r and acumulado(r)[-1] + 50 < x1 - x0 and m(r[0], p0) < 20 and m(r[-1], p1) < 20 and otras:
            muestras = [r[k] for k in range(0, len(r), max(1, len(r) // 40))]
            compartidas = sum(any(dist_linea(q, t, rj) < 15 for t, rj in otras) for q in muestras)
            if compartidas >= 0.9 * len(muestras):
                cortes.append((i0, i1, p0, p1, r, x1 - x0))
    # solo rodeos pequeños (un lazo, una vuelta a una rotonda que no existe): los grandes son desvíos de verdad a un
    # pueblo o a una urbanización sin parada en el tramo (línea 11)
    cortes = [c for c in cortes if c[5] - acumulado(c[4])[-1] <= RODEO_MAX_M and c[5] <= 1.35 * acumulado(c[4])[-1]]
    for i0, i1, p0, p1, r, L in sorted(cortes, reverse=True):
        log(f"  rodeo entre paradas en {p0[0]:.5f},{p0[1]:.5f}: {L:.0f} m → {acumulado(r)[-1]:.0f} m por el camino directo")
        ruta = ruta[:i0] + [p0] + r + [p1] + ruta[i1:]
    return ruta


def calcular(num, linea, orig, log, antes=None):
    t = [tuple(p) for p in orig]
    acc = acumulado(t)
    escala = acc[-1] / (linea.get("km_total") or acc[-1] / 1000) / 1000
    paradas = [p for p in linea["paradas"] if "d" not in p]
    # 1) cada parada en el dibujo original (cerca de donde dice su k): posición y sentido de la marcha
    pos = []
    for p in paradas:
        d, x = proyectar(t, acc, (p["lat"], p["lon"]), cerca_de=p["k"] * 1000 * escala)
        if d > 300:
            d, x = proyectar(t, acc, (p["lat"], p["lon"]))
        pos.append(x)
    # las paradas de vuelta (según Tiadhe, herramientas/colocar_paradas.py) no marcan el trazo de ida; para las que no
    # tienen sentido se deduce por el lado de la calle
    quitar = {i for i, p in enumerate(paradas) if p.get("solo") == "vuelta"}
    sin_sentido = [i for i, p in enumerate(paradas) if not p.get("solo")]
    quitar |= {sin_sentido[k] for k in paradas_de_ida([paradas[i] for i in sin_sentido], [pos[i] for i in sin_sentido], t, acc, log)}
    def sentido(x):
        a, _ = punto_en(t, acc, x - 25)
        b, _ = punto_en(t, acc, x + 25)
        return rumbo(a, b)
    base = [dict(p=(paradas[i]["lat"], paradas[i]["lon"]), x=pos[i], parada=True)
            for i in sorted(range(len(paradas)), key=lambda i: (pos[i], i)) if i not in quitar]
    extra = set()
    forzadas = set()   # paradas a las que la ruta no llegaba: van como punto de paso obligado (sin sentido)
    rj_antes = rejilla_de(antes) if antes else None
    ruta, saltados = None, []
    for vuelta in range(VUELTAS + 3):
        # puntos: paradas de ida + guías del dibujo de Tiadhe entre paradas lejanas + guías añadidas por desvío
        pts = list(base) + [dict(p=(paradas[i]["lat"], paradas[i]["lon"]), x=pos[i], parada=True, libre=True)
                            for i in sorted(forzadas)]
        for a_, b_ in zip(base, base[1:]):
            g = a_["x"] + GUIA_M
            while g < b_["x"] - GUIA_M / 2:
                pts.append(dict(p=punto_en(t, acc, g)[0], x=g, parada=False)); g += GUIA_M
        for g in extra:
            pts.append(dict(p=punto_en(t, acc, g)[0], x=g, parada=False))
        pts.sort(key=lambda q: q["x"])
        # 2) ruta tramo a tramo (cada uno con el sentido de la marcha en sus dos extremos)
        ruta, saltados, dibujo = trazar(pts, t, acc, sentido)
        con_dibujo[num] = bool(dibujo)
        if dibujo:
            log(f"  {len(dibujo)} tramos sin camino razonable por calles: siguen el dibujo de Tiadhe {dibujo[:6]}")
        if saltados:
            log(f"  {len(saltados)} puntos saltados (OSRM no llega con ese sentido): "
                f"{[(round(q['p'][0], 5), round(q['p'][1], 5), 'parada' if q['parada'] else 'guía') for q in saltados[:5]]}")
        # 3) comparar con el original: tramos que se apartan
        rj = rejilla_de(t)
        nuevos, racha, ini = [], 0.0, None
        for a_, b_ in zip(ruta, ruta[1:]):
            if dist_linea(b_, t, rj) > DESVIO_M:
                racha += m(a_, b_); ini = ini or b_
            else:
                if racha > DESVIO_LARGO_M:
                    nuevos.append((ini, racha))
                racha, ini = 0.0, None
        if racha > DESVIO_LARGO_M:
            nuevos.append((ini, racha))
        # paradas que el trazo de antes tocaba y este no: obligatorias (salvo si su pareja de enfrente ya está)
        rjr = rejilla_de(ruta)
        faltan = set()
        for i, p in enumerate(paradas):
            q = (p["lat"], p["lon"])
            if i in forzadas or i in quitar or dist_linea(q, ruta, rjr) <= 30:
                continue   # (las de vuelta están en la otra acera o en otra calle: no se fuerzan)
            if rj_antes is not None and dist_linea(q, antes, rj_antes) > 30:
                continue   # tampoco estaba en la línea antes: dato dudoso, no se fuerza
            pareja = [j for j in range(len(paradas)) if j != i and abs(pos[i] - pos[j]) < 150
                      and m(q, (paradas[j]["lat"], paradas[j]["lon"])) < 150]
            if any(dist_linea((paradas[j]["lat"], paradas[j]["lon"]), ruta, rjr) <= 30 for j in pareja):
                continue
            faltan.add(i)
        if faltan:
            forzadas |= faltan
            log(f"  {len(faltan)} paradas se quedaban fuera: van como paso obligado {[paradas[i]['n'] for i in faltan][:5]}")
            continue
        if not nuevos:
            break
        anadidos = 0
        for p0, L in nuevos:
            _, x0 = proyectar(t, acc, p0)
            for g in range(int(x0 - 200), int(x0 + L + 400), 250):
                if 0 < g < acc[-1] and all(abs(g - e) > 120 for e in extra):
                    extra.add(float(g)); anadidos += 1
        log(f"  vuelta {vuelta + 1}: {len(nuevos)} tramos se apartan del dibujo de Tiadhe → {anadidos} puntos guía más")
        if not anadidos:
            break
    return ruta


_bucle_original = limpiar_trazados.bucle_original


def bucle_validado(orig, inicio, punta, fin, largo):
    """La vuelta del dibujo de Tiadhe solo se copia si OSRM NO puede recorrerla por calles (Giniginamar: Calle El Carmen
    no está para coches). Si puede (Hotel Gorriones: la C. Crisálida existe), el dibujo solo era impreciso."""
    b = _bucle_original(orig, inicio, punta, fin, largo)
    if not b:
        return None
    mid = b[len(b) // 2]
    r = osrm([(b[0][0], b[0][1], None, 80), (mid[0], mid[1], None, 80), (b[-1][0], b[-1][1], None, 80)])
    if r and acumulado(r)[-1] <= 1.3 * acumulado(b)[-1] + 100:
        rj = rejilla_de(r)
        muestras = [(x[0] + (y[0] - x[0]) * k / 4, x[1] + (y[1] - x[1]) * k / 4) for x, y in zip(b, b[1:]) for k in range(4)]
        if sum(dist_linea(q, r, rj) < 40 for q in muestras) >= 0.8 * len(muestras):
            return None
    return b


limpiar_trazados.bucle_original = bucle_validado


def informe(num, linea, ruta, orig, log, resto=()):
    paradas = [(p["lat"], p["lon"], p["n"]) for p in linea["paradas"] if "d" not in p]
    partes = [ruta] + list(resto)   # los ramales (2ª parte de la 05 y la 14) también cuentan para las paradas
    rjs = [rejilla_de(x) for x in partes]
    def d_(q):
        return min(dist_linea(q, x, r) for x, r in zip(partes, rjs))
    lejos = [(n, round(d_((la, lo)))) for la, lo, n in paradas if d_((la, lo)) > 30]
    t = [tuple(p) for p in orig]
    rjo = rejilla_de(t)
    desv, racha, peor = [], 0.0, 0.0
    for a, b in zip(ruta, ruta[1:]):
        d = dist_linea(b, t, rjo)
        if d > DESVIO_M:
            racha += m(a, b); peor = max(peor, d)
        else:
            if racha > DESVIO_LARGO_M:
                desv.append((round(a[0], 5), round(a[1], 5), round(racha), round(peor)))
            racha, peor = 0.0, 0.0
    vueltas = 0
    for a, b, c in zip(ruta, ruta[1:], ruta[2:]):
        if m(a, b) > 5 and m(b, c) > 5:
            v1 = (b[0] - a[0], (b[1] - a[1]) * .88); v2 = (c[0] - b[0], (c[1] - b[1]) * .88)
            cosang = (v1[0] * v2[0] + v1[1] * v2[1]) / (math.hypot(*v1) * math.hypot(*v2))
            if cosang < -0.95:
                vueltas += 1
    L = acumulado(ruta)[-1] / 1000
    log(f"  {L:.1f} km (oficial {linea.get('km_total')}), {len(ruta)} puntos · paradas a >30 m: {len(lejos)} {lejos[:4]} · "
        f"se aparta del dibujo de Tiadhe: {len(desv)} {desv[:4]} · medias vueltas: {vueltas}")
    return dict(km=round(L, 1), paradas_lejos=lejos, desvios=desv, medias_vueltas=vueltas)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("lineas", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    f = DATOS / "paradas_reales.json"
    datos = json.loads(f.read_text(encoding="utf-8"))
    originales = json.loads((DATOS / "trazados_originales.json").read_text(encoding="utf-8"))
    resumen = {}
    base = json.loads((DATOS / "lineas_base.json").read_text(encoding="utf-8"))["lineas"]
    nucleos = {n: [(p["lat"], p["lon"]) for p in b["paradas"] if "lat" in p] for n, b in base.items()}
    todas = {}
    osm = json.loads((DATOS / "paradas_osm.json").read_text(encoding="utf-8"))
    for q in [(p["lat"], p["lon"]) for x in datos.values() for p in x.get("paradas", [])] + [(p["lat"], p["lon"]) for p in osm]:
        todas.setdefault((int(q[0] / 0.002), int(q[1] / 0.002)), []).append(q)
    for num, l in datos.items():
        if a.lineas and num not in a.lineas:
            continue
        ot = originales[num]["trazado"]
        partes = ot if isinstance(ot[0][0], list) else [ot]
        print(f"Línea {num}")
        tr_antes = l["trazado"] if isinstance(l["trazado"][0][0], list) else [l["trazado"]]
        ruta = calcular(num, l, partes[0], print, [tuple(p) for p in tr_antes[0]])
        if not ruta:
            print("  SIN RUTA: se deja como estaba")
            continue
        # muñones (ida y vuelta a una calle sin parada, por un punto guía enganchado a una calle de al lado) y
        # vueltas que OSRM no puede hacer (Giniginamar): las reglas de limpiar_trazados.py
        fijos = set()
        limpiar_trazados.RAMAL_MAX_M = MUNON_MAX_M   # solo muñones cortos: los desvíos largos de verdad (Tindaya) se quedan
        while True:
            n0 = len(ruta)
            ruta = quitar_ramales(ruta, todas, nucleos.get(num, []), [tuple(p) for p in partes[0]], print, fijos)
            # (sin quitar «picos»: en un trazado de OSRM las curvas cerradas son calles de verdad; quitarlas en
            # cadena convertía las curvas de la C. Crisálida en una recta por el campo)
            if len(ruta) == n0:
                break
        otras = [(t, rejilla_de(t)) for n2, x in datos.items() if n2 != num and x.get("trazado")
                 for t in [[tuple(q) for q in x["trazado"][0]]]]
        ruta = sin_rodeos(ruta, [(p["lat"], p["lon"]) for p in sorted(l["paradas"], key=lambda p: p["k"])
                                 if "d" not in p and p.get("solo") != "vuelta"], print, otras)
        resto = [[tuple(p) for p in x] for x in tr_antes[1:]]
        resumen[num] = informe(num, l, ruta, partes[0], print, resto)
        # k de cada parada sobre el trazo, en la escala de tiempos oficial
        acc = acumulado(ruta)
        L = acc[-1]
        km_total = l.get("km_total") or L / 1000
        ant = 0.0
        for p in sorted(l["paradas"], key=lambda p: p["k"]):
            if "d" in p:
                continue
            _, x = proyectar(ruta, acc, (p["lat"], p["lon"]), cerca_de=p["k"] / km_total * L, ventana=2500)
            p["k"] = round(max(x, ant) / L * km_total, 3)
            ant = max(x, ant)
        resto = l["trazado"][1:] if isinstance(l["trazado"][0][0], list) else []
        l["trazado"] = [[[round(la, 6), round(lo, 6)] for la, lo in ruta]] + resto
        l["ajuste"] = "osrm-paradas"
        l["km_calles"] = round(L / 1000, 1)
    # consenso: si una línea se separa sola de las demás en un tramo corto y vuelve a juntarse (p. ej. la 05 por una
    # pista recta junto al Hotel Gorriones mientras las demás van por la C. Crisálida), va por donde van ellas
    print("Consenso entre líneas")
    red = Red(teselas_necesarias(originales))
    trazos = {n: [[tuple(p) for p in t] for t in x["trazado"]] for n, x in datos.items() if x.get("trazado")}
    lineas = RedLineas(trazos, red)
    limpiar_trazados.UNIFICAR_MAX_M = CONSENSO_MAX_M
    for num, x in datos.items():
        if (a.lineas and num not in a.lineas) or num not in resumen:
            continue
        # donde una línea tuvo que seguir el dibujo de Tiadhe, el camino de las demás vale también en tramos largos
        limpiar_trazados.UNIFICAR_MAX_M = CONSENSO_DIBUJO_MAX_M if con_dibujo.get(num) else CONSENSO_MAX_M
        # puntos cada 20 m en los segmentos largos: así un tramo recto que se separa de las demás se detecta entero
        # aunque sus dos extremos coincidan con otras líneas
        denso = [trazos[num][0][0]]
        for x0, x1 in zip(trazos[num][0], trazos[num][0][1:]):
            k = max(1, int(m(x0, x1) / 20))
            denso += [(x0[0] + (x1[0] - x0[0]) * i / k, x0[1] + (x1[1] - x0[1]) * i / k) for i in range(1, k + 1)]
        t = unificar(denso, lineas, num, todas, lambda txt, n=num: print(f"  {n}:{txt}"))
        t = [q for i, q in enumerate(t) if i in (0, len(t) - 1) or abs(rumbo(t[i - 1], q) - rumbo(q, t[i + 1])) > 0.5]
        x["trazado"][0] = [[round(la, 6), round(lo, 6)] for la, lo in t]
    (DATOS / ".cache_osrm" / "informe_rutas.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=1))
    if not a.dry_run:
        f.write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"Guardado {f}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
