#!/usr/bin/env python3
"""Análisis exhaustivo de rutas: detecta paradas faltantes, k incorrectos, desviaciones no mapeadas, esquinas."""
import json, math
from pathlib import Path

DATOS = Path(__file__).resolve().parent.parent / "datos"

def km(a, b):
    p = math.pi / 180
    h = math.sin((b["lat"] - a["lat"]) * p / 2) ** 2 + math.cos(a["lat"] * p) * math.cos(b["lat"] * p) * math.sin((b["lon"] - a["lon"]) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))

def angulo(a, b, c):
    """Ángulo en b entre segmentos a-b y b-c (grados). <30 = esquina aguda."""
    v1 = (a["lat"] - b["lat"], a["lon"] - b["lon"])
    v2 = (c["lat"] - b["lat"], c["lon"] - b["lon"])
    d1 = math.hypot(*v1); d2 = math.hypot(*v2)
    if d1 == 0 or d2 == 0: return 180
    cos = (v1[0]*v2[0] + v1[1]*v2[1]) / (d1 * d2)
    return math.degrees(math.acos(max(-1, min(1, cos))))

def cargar_todo():
    reales = json.loads((DATOS / "paradas_reales.json").read_text(encoding="utf-8"))
    base = json.loads((DATOS / "lineas_base.json").read_text(encoding="utf-8"))["lineas"]
    horarios = json.loads((DATOS / "horarios.json").read_text(encoding="utf-8"))
    return reales, base, horarios

def analizar_linea(num, v_reales, v_base, h):
    print(f"\n{'='*60}")
    print(f"LÍNEA {num}")
    print(f"{'='*60}")
    
    if not v_reales.get("valida") or not v_reales.get("trazado"):
        print("  SIN TRAZADO VÁLIDO")
        return
    
    trazado = v_reales["trazado"][0]
    paradas = v_reales["paradas"]
    
    # 1. Verificar k monotónico
    print(f"\n1. K MONOTÓNICO ({len(paradas)} paradas):")
    k_anterior = -1
    problemas_k = []
    for i, p in enumerate(paradas):
        k = p.get("k", 0)
        if k <= k_anterior:
            problemas_k.append(f"  [{i}] {p['n']}: k={k:.3f} <= anterior {k_anterior:.3f}")
        k_anterior = k
    if problemas_k:
        for prob in problemas_k:
            print(prob)
    else:
        print("  OK: k estrictamente creciente")
    
    # 2. Esquinas agudas en el trazado (<30°)
    print(f"\n2. ESQUINAS AGUDAS EN TRAZADO ({len(trazado)} puntos):")
    esquinas = []
    for i in range(1, len(trazado)-1):
        a = {"lat": trazado[i-1][0], "lon": trazado[i-1][1]}
        b = {"lat": trazado[i][0], "lon": trazado[i][1]}
        c = {"lat": trazado[i+1][0], "lon": trazado[i+1][1]}
        ang = angulo(a, b, c)
        if ang < 30:
            dist_ant = km(a, b)
            dist_sig = km(b, c)
            esquinas.append((i, ang, dist_ant, dist_sig, b))
    print(f"  Encontradas {len(esquinas)} esquinas <30°")
    for i, ang, d1, d2, b in esquinas[:10]:
        print(f"  [{i}] {ang:.1f}°  segmentos: {d1:.0f}m -> {d2:.0f}m  @({b['lat']:.5f},{b['lon']:.5f})")
    if len(esquinas) > 10:
        print(f"  ... y {len(esquinas)-10} más")
    
    # 3. Paradas del esqueleto vs reales
    print(f"\n3. ESQUELETO vs REALES:")
    nombres_base = [p["n"] for p in v_base["paradas"] if "opt" not in p]
    nombres_opt = [p for p in v_base["paradas"] if "opt" in p]
    print(f"  Base (sin opt): {nombres_base}")
    print(f"  Desvíos (opt): {[p['opt'] for p in nombres_opt]}")
    
    # Buscar cada parada base en reales
    for nb in nombres_base:
        coinc = [p for p in paradas if p["n"] == nb or p.get("orig") == nb]
        if not coinc:
            print(f"  FALTA en reales: '{nb}'")
        elif len(coinc) > 1:
            print(f"  DUPLICADA: '{nb}' -> {[c['n'] for c in coinc]}")
    
    # 4. Desvíos horarios (via, inicio, fin, muelle) vs opt
    print(f"\n4. DESVÍOS HORARIOS:")
    if num in h:
        variantes_vistas = set()
        for s in h[num]["salidas"]:
            if "via" in s:
                for v in s["via"]:
                    variantes_vistas.add(v)
            if "inicio" in s:
                variantes_vistas.add(f"inicio:{s['inicio']}")
            if "fin" in s:
                variantes_vistas.add(f"fin:{s['fin']}")
            if s.get("muelle"):
                variantes_vistas.add("muelle")
        print(f"  Variantes en horarios: {variantes_vistas if variantes_vistas else 'ninguna'}")
        for opt in nombres_opt:
            if opt["opt"] not in variantes_vistas and opt["opt"] not in [v for v in variantes_vistas if not v.startswith(("inicio:","fin:"))]:
                print(f"  OPT en base PERO NO en horarios: {opt['opt']}")
    
    # 5. Saltos grandes en trazado (posible corte de carretera)
    print(f"\n5. SALTOS EN TRAZADO:")
    saltos = []
    for i in range(len(trazado)-1):
        d = km({"lat": trazado[i][0], "lon": trazado[i][1]}, {"lat": trazado[i+1][0], "lon": trazado[i+1][1]})
        if d > 200:  # >200m entre puntos consecutivos
            saltos.append((i, d, trazado[i], trazado[i+1]))
    if saltos:
        for i, d, a, b in saltos[:10]:
            print(f"  [{i}] {d:.0f}m  ({a[0]:.5f},{a[1]:.5f}) -> ({b[0]:.5f},{b[1]:.5f})")
    else:
        print("  OK: sin saltos >200m")
    
    # 6. Distancia total trazado vs km_total
    print(f"\n6. LONGITUD TRAZADO vs km_total:")
    long_trazado = sum(km({"lat": trazado[i][0], "lon": trazado[i][1]}, {"lat": trazado[i+1][0], "lon": trazado[i+1][1]}) for i in range(len(trazado)-1))
    km_total = v_reales.get("km_total", 0)
    print(f"  Trazado: {long_trazado:.1f} km  |  km_total: {km_total:.1f} km  |  diff: {abs(long_trazado - km_total):.1f} km")
    
    # 7. Paradas a >100m del trazado (posible parada mal colocada)
    print(f"\n7. PARADAS LEJOS DEL TRAZADO:")
    for p in paradas:
        dist_min = min(km({"lat": p["lat"], "lon": p["lon"]}, {"lat": t[0], "lon": t[1]}) for t in trazado)
        if dist_min > 0.1:  # >100m
            print(f"  {p['n']}: {dist_min*1000:.0f}m del trazado  k={p.get('k',0):.3f}")

def main():
    reales, base, horarios = cargar_todo()
    
    # Analizar todas las líneas con datos reales
    for num, v_reales in reales.items():
        if v_reales.get("valida") and v_reales.get("trazado"):
            v_base = base.get(num, {})
            h = horarios.get(num, {})
            analizar_linea(num, v_reales, v_base, h)

if __name__ == "__main__":
    main()