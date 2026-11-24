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

Las respuestas de OSRM se guardan en datos/.cache_osrm/rutas/ (se puede repetir sin volver a pedirlas).
"""
import argparse, hashlib, json, math, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ajustar_rutas import DATOS, acumulado, m, proyectar_seg  # noqa: E402

SERVIDOR = "https://router.project-osrm.org"
CACHE = DATOS / ".cache_osrm" / "rutas"
GUIA_M = 700          # punto guía del dibujo original cada tanto entre paradas lejanas
DESVIO_M = 120        # la ruta no debe apartarse del dibujo original más que esto…
DESVIO_LARGO_M = 250  # …durante más de esto
MAX_PUNTOS = 90       # puntos por consulta (el servidor admite 100)
VUELTAS = 4
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


def calcular(num, linea, orig, log):
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
    quitar = paradas_de_ida(paradas, pos, t, acc, log)
    def sentido(x):
        a, _ = punto_en(t, acc, x - 25)
        b, _ = punto_en(t, acc, x + 25)
        return rumbo(a, b)
    base = [dict(p=(paradas[i]["lat"], paradas[i]["lon"]), x=pos[i], parada=True)
            for i in sorted(range(len(paradas)), key=lambda i: (pos[i], i)) if i not in quitar]
    extra = set()
    ruta, saltados = None, []
    for vuelta in range(VUELTAS):
        # puntos: paradas de ida + guías del dibujo de Tiadhe entre paradas lejanas + guías añadidas por desvío
        pts = list(base)
        for a_, b_ in zip(base, base[1:]):
            g = a_["x"] + GUIA_M
            while g < b_["x"] - GUIA_M / 2:
                pts.append(dict(p=punto_en(t, acc, g)[0], x=g, parada=False)); g += GUIA_M
        for g in extra:
            pts.append(dict(p=punto_en(t, acc, g)[0], x=g, parada=False))
        pts.sort(key=lambda q: q["x"])
        # 2) ruta tramo a tramo (cada uno con el sentido de la marcha en sus dos extremos)
        ruta, saltados, dibujo, i = [], [], [], 0
        while i < len(pts) - 1:
            hecho = False
            for j in range(i + 1, min(i + 4, len(pts))):
                a_, b_ = pts[i], pts[j]
                r = osrm([(a_["p"][0], a_["p"][1], sentido(a_["x"]), 60 if a_["parada"] else 80),
                          (b_["p"][0], b_["p"][1], sentido(b_["x"]), 60 if b_["parada"] else 80)])
                if r is None:   # sin sentido en el destino (p. ej. una parada al final de un fondo de saco)
                    r = osrm([(a_["p"][0], a_["p"][1], sentido(a_["x"]), 60 if a_["parada"] else 80),
                              (b_["p"][0], b_["p"][1], None, 60 if b_["parada"] else 80)])
                tramo_orig = b_["x"] - a_["x"]
                if r and acumulado(r)[-1] > MAS_LARGO * tramo_orig + 150:
                    r = None   # rodeo: la calle que usa la guagua no está (bien) en el mapa de calles
                if r:
                    ruta += r if not ruta else r[1:]
                    saltados += pts[i + 1:j]
                    i, hecho = j, True
                    break
            if not hecho:
                # sin camino razonable por calles: este tramo sigue el dibujo de Tiadhe hasta el siguiente punto
                a_, b_ = pts[i], pts[i + 1]
                trozo = [a_["p"]] + [q for q, d in zip(t, acc) if a_["x"] < d < b_["x"]] + [b_["p"]]
                ruta += trozo if not ruta else trozo[1:]
                dibujo.append((round(a_["p"][0], 5), round(a_["p"][1], 5), round(b_["x"] - a_["x"])))
                i += 1
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


def informe(num, linea, ruta, orig, log):
    paradas = [(p["lat"], p["lon"], p["n"]) for p in linea["paradas"] if "d" not in p]
    rj = rejilla_de(ruta)
    lejos = [(n, round(dist_linea((la, lo), ruta, rj))) for la, lo, n in paradas if dist_linea((la, lo), ruta, rj) > 30]
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
    for num, l in datos.items():
        if a.lineas and num not in a.lineas:
            continue
        ot = originales[num]["trazado"]
        partes = ot if isinstance(ot[0][0], list) else [ot]
        print(f"Línea {num}")
        ruta = calcular(num, l, partes[0], print)
        if not ruta:
            print("  SIN RUTA: se deja como estaba")
            continue
        resumen[num] = informe(num, l, ruta, partes[0], print)
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
    (DATOS / ".cache_osrm" / "informe_rutas.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=1))
    if not a.dry_run:
        f.write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"Guardado {f}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
