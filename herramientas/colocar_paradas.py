#!/usr/bin/env python3
"""Pone cada parada de las líneas donde el mapa pinta la parada, con el sentido que le da Tiadhe.

    python herramientas/colocar_paradas.py --dry-run   # informe
    python herramientas/colocar_paradas.py             # aplica en datos/paradas_reales.json
    (después: python herramientas/rutas_osrm.py, para que las rutas pasen por las paradas nuevas)

Entradas:
  - datos/paradas_mapa.json: los iconos de parada del mapa de OpenStreetMap (herramientas/paradas_mapa.py).
  - datos/.cache_osrm/kml/<línea>.kml: el mapa de Google de Tiadhe de cada línea, con sus capas «Paradas Ida» y
    «Paradas Vuelta» (herramientas/importar_mapas.py las descarga; aquí solo se leen).

Reglas (aquí se circula por la derecha: la parada de ida queda a la derecha del recorrido de ida):
  - Parada que Tiadhe pone en los dos sentidos (mismo punto en las dos capas): si el mapa tiene dos iconos, uno a cada
    lado de la calle, se separa en dos paradas, cada una con su sentido ('solo'); si tiene uno, va ahí (dos sentidos).
  - Parada de un solo sentido: al icono más cercano de su lado de la calle ('solo': su sentido).
  - Sin icono a ≤ MAX_M: se queda donde estaba (con su sentido, si Tiadhe lo da).
La posición de antes se guarda en 'lat0'/'lon0' (el script siempre parte de ella: se puede repetir).
"""
import argparse, copy, json, math, sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ajustar_rutas import DATOS, m, proyectar_seg  # noqa: E402

MAX_M = 75
LADO_M = 3      # una parada a menos de esto del eje de la calle vale para cualquier lado


def lado(t, p):
    """Distancia con signo de p al recorrido t: > 0 a la izquierda (en el sentido del dibujo), < 0 a la derecha."""
    mejor = (1e9, 0)
    for i in range(len(t) - 1):
        if abs(t[i][0] - p[0]) > 0.01 or abs(t[i][1] - p[1]) > 0.01:
            continue
        d, _, _ = proyectar_seg(p, t[i], t[i + 1])
        if d < mejor[0]:
            mejor = (d, i)
    d, i = mejor
    if d > 400:
        return None
    a, b = t[i], t[i + 1]
    c = math.cos(math.radians(a[0]))
    return d if (b[1] - a[1]) * c * (p[0] - a[0]) - (b[0] - a[0]) * (p[1] - a[1]) * c > 0 else -d


def sentidos_tiadhe(num, origen=None, destino=None):
    """{nombre: [(lat, lon, {'ida', 'vuelta'})]} según las capas del mapa de Tiadhe (None si no está descargado).
    Si la capa «Paradas Ida» de Tiadhe empieza en nuestro destino (línea 11: su ida es nuestra vuelta), se invierte."""
    f = DATOS / ".cache_osrm" / "kml" / f"{num}.kml"
    if not f.exists():
        return None
    puntos, primera = {}, {}
    for carpeta in ET.parse(f).getroot().iter():
        if not carpeta.tag.endswith("Folder"):
            continue
        nom = (next((c.text for c in carpeta if c.tag.endswith("name")), "") or "").lower()
        s = "ida" if "ida" in nom else "vuelta" if "vuelta" in nom else None
        if not s:
            continue
        for pm in carpeta.iter():
            if not pm.tag.endswith("Placemark"):
                continue
            n = (next((c.text for c in pm if c.tag.endswith("name")), "") or "").strip()
            co = next((c.text for c in pm.iter() if c.tag.endswith("coordinates")), None)
            if co and len(co.strip().split()) == 1:
                lo, la = map(float, co.strip().split(",")[:2])
                puntos.setdefault((n, round(la, 5), round(lo, 5)), set()).add(s)
                primera.setdefault(s, (la, lo))
    invertir = bool(origen and destino and "ida" in primera and m(primera["ida"], destino) < m(primera["ida"], origen))
    if invertir:
        print(f"  línea {num}: la «ida» de Tiadhe empieza en nuestro destino → se invierten sus sentidos")
        puntos = {k: {"vuelta" if x == "ida" else "ida" for x in v} for k, v in puntos.items()}
    res = {}
    for (n, la, lo), ss in puntos.items():
        res.setdefault(n, []).append((la, lo, ss))
    return res


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    f = DATOS / "paradas_reales.json"
    datos = json.loads(f.read_text(encoding="utf-8"))
    mapa = [(p["lat"], p["lon"]) for p in json.loads((DATOS / "paradas_mapa.json").read_text())]
    total = dict(paradas=0, movidas=0, separadas=0, con_sentido=0, sin_icono=0, sin_tiadhe=0)
    for num, l in datos.items():
        # deshacer una pasada anterior
        l["paradas"] = [p for p in l["paradas"] if not p.get("copia")]
        for p in l["paradas"]:
            if "lat0" in p:
                p["lat"], p["lon"] = p.pop("lat0"), p.pop("lon0")
            p.pop("solo", None)
        t = [tuple(x) for x in l["trazado"][0]]
        ps0 = sorted((p for p in l["paradas"] if "d" not in p), key=lambda p: p["k"])
        tiadhe = sentidos_tiadhe(num, (ps0[0]["lat"], ps0[0]["lon"]), (ps0[-1]["lat"], ps0[-1]["lon"]))
        if tiadhe is None:
            print(f"  línea {num}: falta su mapa de Tiadhe (sin sentidos)")
        usados, nuevas, est = set(), [], dict(movidas=0, separadas=0, solo=0, sin=0)
        for p in sorted(l["paradas"], key=lambda p: p["k"]):
            if "d" in p:
                continue
            total["paradas"] += 1
            q = (p["lat"], p["lon"])
            ss = None
            if tiadhe is not None:
                cand = [(m(q, (la, lo)), s) for la, lo, s in tiadhe.get(p["n"], [])]
                ss = min(cand, key=lambda x: x[0])[1] if cand and min(cand)[0] < 400 else None
                if ss is None:
                    total["sin_tiadhe"] += 1
            iconos = []
            for ic in mapa:
                d = m(q, ic)
                if d <= MAX_M and ic not in usados:
                    iconos.append((d, ic, lado(t, ic)))
            iconos.sort()
            der = [x for x in iconos if x[2] is not None and x[2] < LADO_M]
            izq = [x for x in iconos if x[2] is not None and x[2] > -LADO_M]

            def mover(parada, ic):
                parada["lat0"], parada["lon0"] = parada["lat"], parada["lon"]
                parada["lat"], parada["lon"] = ic
                usados.add(ic)

            if ss == {"ida", "vuelta"} or ss is None:
                d_ = [x for x in der if x[2] < -LADO_M]
                i_ = [x for x in izq if x[2] > LADO_M]
                if ss and d_ and i_ and m(d_[0][1], i_[0][1]) > 8:
                    mover(p, d_[0][1]); p["solo"] = "ida"
                    otra = copy.deepcopy(p)
                    otra["lat"], otra["lon"] = i_[0][1]; otra["solo"] = "vuelta"; otra["copia"] = True
                    usados.add(i_[0][1]); nuevas.append(otra)
                    est["separadas"] += 1
                elif iconos:
                    mover(p, iconos[0][1]); est["movidas"] += 1
                else:
                    est["sin"] += 1
            else:
                s = next(iter(ss))
                p["solo"] = s
                est["solo"] += 1
                buenos = der if s == "ida" else izq
                if buenos:
                    mover(p, buenos[0][1]); est["movidas"] += 1
                else:
                    est["sin"] += 1
        l["paradas"] = sorted(l["paradas"] + nuevas, key=lambda p: (p["k"], "d" in p))
        print(f"  línea {num}: {est['movidas']} al icono del mapa, {est['separadas']} separadas en sus dos sentidos, "
              f"{est['solo']} de un solo sentido, {est['sin']} sin icono cerca")
        total["movidas"] += est["movidas"]; total["separadas"] += est["separadas"]
        total["con_sentido"] += est["solo"] + 2 * est["separadas"]; total["sin_icono"] += est["sin"]
    print(f"Total: {total}")
    if not a.dry_run:
        f.write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"Guardado {f}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
