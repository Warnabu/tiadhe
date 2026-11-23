#!/usr/bin/env python3
"""Pega los trazados de datos/paradas_reales.json a la carretera real (map matching con OSRM).

    python ajustar_trazados.py --dry-run          # prueba sin tocar nada, solo informa y crea la vista previa
    python ajustar_trazados.py                    # ajusta todas las líneas
    python ajustar_trazados.py 01 10              # solo esas líneas
    python ajustar_trazados.py --servidor http://localhost:5000   # tu propio OSRM (recomendado si hay muchos 429)

Qué hace, línea a línea:
  1. Corta el trazado en ventanas de ~60 puntos (solapadas) y pide a OSRM /match que las pegue a la vía.
  2. VALIDA cada ventana: si el resultado no se parece al original (puntos lejos, longitud absurda)
     o OSRM falla, esa ventana conserva el trazado original y se avisa. Nunca empeora una zona a ciegas.
  3. Une las ventanas, simplifica (3 m) y RECALCULA el 'k' (km) de cada parada sobre el trazado nuevo,
     porque el servidor usa ese k para los tiempos de llegada.
  4. Si menos del 60 % de las ventanas se ajustan, la línea se deja como estaba.

Seguridad: la primera vez guarda datos/paradas_reales.original.json (y git guarda el resto).
Las respuestas de OSRM se cachean en datos/.cache_osrm/, así que repetir no vuelve a pedir nada.
Genera datos/preview_ajuste.geojson (gris = original, color = ajustado): ábrelo en https://geojson.io
Servidor demo público: ~1 petición/s, uso ligero. Ver https://github.com/Project-OSRM/osrm-backend
"""
import hashlib, json, math, shutil, sys, time
from pathlib import Path

from importar_mapas import km, proyectar, simplificar

DATOS = Path(__file__).parent / "datos"
CACHE = DATOS / ".cache_osrm"
SERVIDOR = "https://router.project-osrm.org"
VENTANA, SOLAPE = 60, 2          # puntos por petición y puntos compartidos entre ventanas
RADIO_M = 50                     # distancia máxima a la que un punto puede "engancharse" a una vía
MIN_COBERTURA = 0.80             # fracción de puntos originales a <60 m del resultado
RATIO_LONG = (0.70, 1.60)        # longitud ajustada / original (las curvas cortadas hacen que crezca)
MIN_AJUSTADAS = 0.60             # fracción mínima de ventanas buenas para aceptar la línea
_ultima = [0.0]


def pedir_match(servidor, coords, radio=RADIO_M):
    """coords [(lat, lon)] → [(lat, lon)] pegado a la carretera, o None si OSRM no puede."""
    import requests
    cs = ";".join(f"{lo:.6f},{la:.6f}" for la, lo in coords)
    params = dict(geometries="geojson", overview="full", gaps="ignore", tidy="true",
                  radiuses=";".join([str(radio)] * len(coords)))
    clave = hashlib.sha1((servidor + cs + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    f = CACHE / f"{clave}.json"
    if f.exists():
        return [tuple(p) for p in json.loads(f.read_text())]
    for intento in range(4):
        espera = 1.1 - (time.time() - _ultima[0])
        if espera > 0:
            time.sleep(espera)
        _ultima[0] = time.time()
        try:
            r = requests.get(f"{servidor}/match/v1/driving/{cs}", params=params, timeout=60,
                             headers={"User-Agent": "guaguas-fuerteventura"})
            if r.status_code == 429:
                time.sleep(5 * (intento + 1)); continue
            j = r.json()
            if j.get("code") != "Ok":
                return None                               # NoMatch / NoSegment: no se reintenta
            geom = [(la, lo) for m in j["matchings"] for lo, la in m["geometry"]["coordinates"]]
            CACHE.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(geom))
            return geom
        except Exception as e:
            print(f"      (red: {e}; reintento {intento + 1}/4)")
            time.sleep(2 * (intento + 1))
    return None


def longitud(t):
    return sum(km(a, b) for a, b in zip(t, t[1:]))


def dist_a_traza(t, p):
    k, mejor = math.cos(math.radians(p[0])), 1e9
    for (ay, ax), (by, bx) in zip(t, t[1:]):
        dx, dy = (bx - ax) * k, by - ay
        L = dx * dx + dy * dy
        u = 0 if L == 0 else max(0, min(1, (((p[1] - ax) * k) * dx + (p[0] - ay) * dy) / L))
        mejor = min(mejor, km((ay + u * dy, ax + u * (bx - ax)), p))
    return mejor


def validar(geom, orig):
    """(ok, motivo)"""
    if not geom or len(geom) < 2:
        return False, "sin resultado"
    cerca = sum(dist_a_traza(geom, p) < 0.06 for p in orig) / len(orig)
    if cerca < MIN_COBERTURA:
        return False, f"solo {cerca:.0%} de puntos cerca"
    lo, lg = longitud(orig), longitud(geom)
    if lo > 0.05 and not RATIO_LONG[0] <= lg / lo <= RATIO_LONG[1]:
        return False, f"longitud {lg / lo:.2f}x el original"
    return True, ""


def ajustar_traza(traza, servidor):
    """→ (traza_nueva, ventanas_ok, ventanas_total, motivos_fallo)"""
    pts = [tuple(traza[0])]
    for p in traza[1:]:
        if km(pts[-1], p) > 0.001:
            pts.append(tuple(p))
    res, ok, total, fallos = [], 0, 0, []
    i = 0
    while i < len(pts) - 1:
        orig = pts[i:i + VENTANA]
        total += 1
        geom = pedir_match(servidor, orig)
        bien, motivo = validar(geom, orig)
        if bien:
            ok += 1
            res.extend(geom)
        else:
            fallos.append((i, motivo))
            res.extend(orig)
        i += VENTANA - SOLAPE
    limpio = [res[0]]
    for p in res[1:]:
        if km(limpio[-1], p) > 0.0005:
            limpio.append(p)
    return limpio, ok, total, fallos


def reajustar_paradas(paradas, traza):
    acum = [0.0]
    for a, b in zip(traza, traza[1:]):
        acum.append(acum[-1] + km(a, b))
    lejos, nuevas = [], []
    for p in paradas:
        d, k = proyectar(traza, acum, p["lat"], p["lon"])
        if d > 0.3:
            lejos.append(p["n"])
        nuevas.append({**p, "k": round(k, 3)})
    orden_antes = [p["n"] for p in paradas]
    nuevas.sort(key=lambda p: p["k"])
    return nuevas, round(acum[-1], 1), lejos, [p["n"] for p in nuevas] != orden_antes


def main(args):
    dry, servidor, lineas, i = False, SERVIDOR, [], 0
    while i < len(args):
        if args[i] == "--dry-run":
            dry = True; i += 1
        elif args[i] == "--servidor":
            servidor = args[i + 1].rstrip("/"); i += 2
        else:
            lineas.append(args[i]); i += 1
    ruta = DATOS / "paradas_reales.json"
    copia = DATOS / "paradas_reales.original.json"
    if not copia.exists():
        shutil.copyfile(ruta, copia)
        print("Copia de seguridad:", copia.name)
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    feats = []
    for num in lineas or list(datos):
        v = datos.get(num)
        if not v or not v.get("valida") or not v.get("trazado"):
            print(f"L{num}: sin trazado válido, la salto"); continue
        orig = v["trazado"][0]
        nueva, ok, total, fallos = ajustar_traza(orig, servidor)
        aceptada = ok / total >= MIN_AJUSTADAS
        print(f"L{num}: {ok}/{total} ventanas ajustadas  {longitud(orig):.1f} km → {longitud(nueva):.1f} km  "
              f"{'ACEPTADA' if aceptada else 'RECHAZADA (se deja como estaba)'}")
        for ini, motivo in fallos[:4]:
            print(f"     · ventana desde el punto {ini}: {motivo} → se conserva el original")
        if len(fallos) > 4:
            print(f"     · … y {len(fallos) - 4} ventanas más conservadas")
        feats.append({"type": "Feature", "properties": {"linea": num, "tipo": "original", "stroke": "#888888"},
                      "geometry": {"type": "LineString", "coordinates": [[lo, la] for la, lo in orig]}})
        if aceptada:
            nueva = simplificar(nueva, 3.0)
            paradas, kmtot, lejos, reorden = reajustar_paradas(v["paradas"], nueva)
            feats.append({"type": "Feature", "properties": {"linea": num, "tipo": "ajustado", "stroke": "#7b1fa2"},
                          "geometry": {"type": "LineString", "coordinates": [[lo, la] for la, lo in nueva]}})
            if lejos:
                print(f"     · {len(lejos)} paradas a >300 m del trazado nuevo (p. ej. {lejos[0]!r})")
            if reorden:
                print("     · OJO: cambió el orden de alguna parada al recalcular su posición")
            if not dry:
                v["trazado"] = [nueva] + v["trazado"][1:]
                v["paradas"], v["km_total"], v["ajustado"] = paradas, kmtot, True
    (DATOS / "preview_ajuste.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    print("Vista previa: datos/preview_ajuste.geojson (https://geojson.io)")
    if dry:
        print("--dry-run: paradas_reales.json NO se ha modificado")
    else:
        ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
        print("Guardado", ruta)


if __name__ == "__main__":
    main(sys.argv[1:])
