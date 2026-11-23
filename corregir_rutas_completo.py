#!/usr/bin/env python3
"""Corrección completa de rutas:
- k estrictamente monotónico
- Trazados suaves (Catmull-Rom + elimina duplicados)
- Añade paradas base faltantes con coords correctas
- Snap de paradas al trazado (<50m)
- Añade desvíos: Triquivijate, Las Playitas, Ajuy, Muelle Morro Jable
- Recalcula k sobre el trazado final
"""
import json, math, shutil
from pathlib import Path
from copy import deepcopy

DATOS = Path(__file__).parent / "datos"

def km(a, b):
    p = math.pi / 180
    h = math.sin((b["lat"] - a["lat"]) * p / 2) ** 2 + math.cos(a["lat"] * p) * math.cos(b["lat"] * p) * math.sin((b["lon"] - a["lon"]) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))

def km_pt(a, b):
    return km({"lat": a[0], "lon": a[1]}, {"lat": b[0], "lon": b[1]})

def catmull_rom(points, tension=0.5, samples_per_segment=5):
    """Suaviza con spline Catmull-Rom. points = [(lat, lon), ...]"""
    if len(points) < 4:
        return points
    res = [points[0]]
    for i in range(len(points) - 3):
        p0, p1, p2, p3 = points[i], points[i+1], points[i+2], points[i+3]
        for t in [j/samples_per_segment for j in range(1, samples_per_segment)]:
            t2 = t*t; t3 = t2*t
            lat = 0.5 * ((2*p1[0]) + (-p0[0]+p2[0])*t + (2*p0[0]-5*p1[0]+4*p2[0]-p3[0])*t2 + (-p0[0]+3*p1[0]-3*p2[0]+p3[0])*t3)
            lon = 0.5 * ((2*p1[1]) + (-p0[1]+p2[1])*t + (2*p0[1]-5*p1[1]+4*p2[1]-p3[1])*t2 + (-p0[1]+3*p1[1]-3*p2[1]+p3[1])*t3)
            res.append((lat, lon))
        res.append(p2)
    res.append(points[-2])
    res.append(points[-1])
    return res

def eliminar_duplicados(puntos, eps_m=1):
    """Elimina puntos consecutivos a < eps_m metros"""
    if not puntos: return []
    res = [puntos[0]]
    for p in puntos[1:]:
        if km_pt(res[-1], p) >= eps_m/1000:
            res.append(p)
    return res

def acumulado(puntos):
    """Distancia acumulada desde el inicio para cada punto"""
    acum = [0.0]
    for i in range(1, len(puntos)):
        acum.append(acum[-1] + km_pt(puntos[i-1], puntos[i]))
    return acum

def proyectar(trazado, acum, lat, lon):
    """Proyecta (lat,lon) sobre el trazado -> (dist_km, k_km)"""
    k = math.cos(math.radians(lat))
    mejor = 1e9; mejor_k = 0
    for i in range(len(trazado)-1):
        ay, ax = trazado[i]; by, bx = trazado[i+1]
        dx, dy = (bx - ax) * k, by - ay
        L = dx*dx + dy*dy
        if L == 0: continue
        u = max(0, min(1, (((lon - ax)*k)*dx + (lat - ay)*dy) / L))
        py = ay + u*dy; px = ax + u*dx/k
        d = km({"lat": lat, "lon": lon}, {"lat": py, "lon": px})
        if d < mejor:
            mejor = d
            mejor_k = acum[i] + u * (acum[i+1] - acum[i])
    return mejor, mejor_k

def snap_parada(parada, trazado, acum, max_dist=0.05):
    """Si la parada está a >max_dist km del trazado, la mueve al punto más cercano"""
    d, k = proyectar(trazado, acum, parada["lat"], parada["lon"])
    # Clamp k al rango válido
    k = max(acum[0], min(acum[-1], k))
    if d > max_dist:
        # Encontrar punto exacto en trazado
        k_idx = 0
        for i in range(len(acum)-1):
            if acum[i] <= k < acum[i+1]:
                k_idx = i
                break
        else:
            k_idx = len(acum) - 2
        u = (k - acum[k_idx]) / (acum[k_idx+1] - acum[k_idx]) if acum[k_idx+1] != acum[k_idx] else 0
        ay, ax = trazado[k_idx]; by, bx = trazado[k_idx+1]
        return {"lat": ay + u*(by-ay), "lon": ax + u*(bx-ax), "k": round(k, 3), "movida": True, "dist_original": d}
    return {"lat": parada["lat"], "lon": parada["lon"], "k": round(k, 3), "movida": False, "dist_original": d}

def cargar_todo():
    reales = json.loads((DATOS / "paradas_reales.json").read_text(encoding="utf-8"))
    base = json.loads((DATOS / "lineas_base.json").read_text(encoding="utf-8"))["lineas"]
    horarios = json.loads((DATOS / "horarios.json").read_text(encoding="utf-8"))
    return reales, base, horarios

# Coordenadas exactas de paradas base faltantes (de Google Maps / Tiadhe)
PARADAS_BASE_COORDS = {
    "Puerto del Rosario (Estación)": (28.5000, -13.8620),
    "Antigua": (28.4170, -14.0125),
    "Tuineje": (28.3230, -14.0490),
    "Gran Tarajal": (28.2117, -14.0165),
    "Tarajalejo": (28.1885, -14.1035),
    "La Lajita": (28.1795, -14.1760),
    "Costa Calma": (28.1620, -14.2250),
    "Esquinzo": (28.0960, -14.3130),
    "Morro Jable": (28.0505, -14.3545),
    "Pájara": (28.3495, -14.1068),
    "La Pared": (28.2235, -14.2340),
    "Playa Blanca": (28.4626, -13.8607),
    "Aeropuerto": (28.4527, -13.8638),
    "Caleta de Fuste": (28.3985, -13.8615),
    "Las Salinas": (28.3830, -13.8625),
    "Parque Holandés": (28.7180, -13.8690),
    "Corralejo": (28.7345, -13.8665),
    "La Oliva": (28.6105, -13.9285),
    "El Cotillo": (28.6870, -14.0105),
    "Lajares": (28.6980, -13.9450),
    "Hospital General": (28.4790, -13.8770),
    "Triquivijate": (28.3860, -14.0245),
    "Las Playitas": (28.2274, -13.9905),
    "Ajuy": (28.4200, -14.1450),
    "Salinas del Carmen": (28.3990, -13.9100),
    "El Matorral": (28.4380, -13.8620),
    "El Time": (28.5085, -13.9650),
    "Vega de Río Palmas": (28.3283, -14.0836),
    "Cofete": (28.1040, -14.4530),
    "Faro de Punta Jandía": (28.0575, -14.5135),
}

# Desvíos: secuencia de waypoints (lat, lon) que el trazado debe seguir
DESVIOS = {
    "01": {
        "Triquivijate": [(28.3860, -14.0245)],  # punto de paso
        "Las Playitas": [(28.2274, -13.9905)],
        "Muelle Morro Jable": [(28.0505, -14.3545), (28.0495, -14.3560)],  # entra al muelle
    },
    "18": {
        "Ajuy": [(28.4200, -14.1450)],
        "Las Playitas": [(28.2274, -13.9905)],
    },
    "03": {
        "Aeropuerto": [(28.4527, -13.8638)],  # ya en base
    },
    "04": {
        # La Pared es un desvío según horarios (sale/termina en La Pared)
    },
}

def insertar_desvio_en_trazado(trazado, desde_idx, hasta_idx, waypoints):
    """Inserta waypoints entre dos índices del trazado"""
    if not waypoints: return trazado
    nuevo = trazado[:desde_idx+1]
    for wp in waypoints:
        nuevo.append(list(wp))
    nuevo.extend(trazado[hasta_idx:])
    return nuevo

def encontrar_idx_mas_cercano(trazado, lat, lon):
    return min(range(len(trazado)), key=lambda i: km_pt(trazado[i], (lat, lon)))

def procesar_linea(num, v_reales, v_base, horarios):
    if not v_reales.get("valida") or not v_reales.get("trazado"):
        return v_reales
    
    print(f"\nProcesando línea {num}...")
    trazado_orig = v_reales["trazado"][0]
    paradas_orig = v_reales["paradas"]
    
    # 1. Limpiar duplicados en trazado
    trazado = eliminar_duplicados(trazado_orig)
    print(f"  Trazado: {len(trazado_orig)} -> {len(trazado)} pts")
    
    # 2. Suavizar con Catmull-Rom
    trazado = catmull_rom(trazado, tension=0.5, samples_per_segment=3)
    print(f"  Suavizado: {len(trazado)} pts")
    
    # 3. Insertar desvíos si los hay
    if num in DESVIOS:
        for nombre_desvio, waypoints in DESVIOS[num].items():
            # Encontrar dónde insertar (punto más cercano al waypoint central)
            wp_central = waypoints[len(waypoints)//2]
            idx = encontrar_idx_mas_cercano(trazado, wp_central[0], wp_central[1])
            # Insertar alrededor de ese punto (±5 puntos)
            ini = max(0, idx - 5)
            fin = min(len(trazado), idx + 5)
            trazado = insertar_desvio_en_trazado(trazado, ini, fin, waypoints)
            print(f"  Desvío '{nombre_desvio}' insertado en idx {idx}")
    
    # 4. Recalcular acumulado
    acum = acumulado(trazado)
    km_total = round(acum[-1], 1)
    
    # 4. Preparar paradas: base + reales
    # Paradas base (esqueleto sin opt)
    paradas_base = [p for p in v_base["paradas"] if "opt" not in p]
    paradas_opt = [p for p in v_base["paradas"] if "opt" in p]
    
    # Diccionario de reales por nombre para lookup rápido
    reales_dict = {}
    for p in paradas_orig:
        reales_dict.setdefault(p["n"], []).append(p)
    
    nuevas_paradas = []
    k_usados = []
    
    # Añadir paradas base en orden
    for pb in paradas_base:
        nombre = pb["n"]
        if nombre in PARADAS_BASE_COORDS:
            lat, lon = PARADAS_BASE_COORDS[nombre]
        else:
            # Buscar en reales
            coinc = reales_dict.get(nombre, [])
            if coinc:
                lat, lon = coinc[0]["lat"], coinc[0]["lon"]
            else:
                print(f"  ADVERTENCIA: '{nombre}' no tiene coordenadas conocidas")
                continue
        
        snap = snap_parada({"lat": lat, "lon": lon, "n": nombre}, trazado, acum, max_dist=0.05)
        p_new = {"n": nombre, "lat": snap["lat"], "lon": snap["lon"], "k": snap["k"]}
        if snap["movida"]:
            print(f"  Parada base '{nombre}' movida {snap['dist_original']*1000:.0f}m al trazado")
        nuevas_paradas.append(p_new)
        k_usados.append(snap["k"])
    
    # Añadir desvíos (opt) en su posición correcta según k
    for po in paradas_opt:
        nombre_opt = po["opt"]
        if nombre_opt in PARADAS_BASE_COORDS:
            lat, lon = PARADAS_BASE_COORDS[nombre_opt]
        else:
            coinc = reales_dict.get(po["n"], [])
            if coinc:
                lat, lon = coinc[0]["lat"], coinc[0]["lon"]
            else:
                continue
        snap = snap_parada({"lat": lat, "lon": lon, "n": po["n"], "opt": nombre_opt}, trazado, acum, max_dist=0.05)
        p_new = {"n": po["n"], "lat": snap["lat"], "lon": snap["lon"], "k": snap["k"], "opt": nombre_opt}
        if snap["movida"]:
            print(f"  Desvío '{po['n']}' ({nombre_opt}) movido {snap['dist_original']*1000:.0f}m")
        nuevas_paradas.append(p_new)
        k_usados.append(snap["k"])
    
    # Añadir resto de paradas reales que no sean base/opt
    nombres_base_opt = set(p["n"] for p in paradas_base + paradas_opt)
    for p in paradas_orig:
        if p["n"] in nombres_base_opt:
            continue
        snap = snap_parada(p, trazado, acum, max_dist=0.05)
        p_new = {**p, "lat": snap["lat"], "lon": snap["lon"], "k": snap["k"]}
        if snap["movida"]:
            print(f"  Parada '{p['n']}' movida {snap['dist_original']*1000:.0f}m al trazado")
        nuevas_paradas.append(p_new)
        k_usados.append(snap["k"])
    
    # 5. Ordenar por k y asegurar monotónico estricto
    nuevas_paradas.sort(key=lambda p: p["k"])
    for i, p in enumerate(nuevas_paradas):
        if i > 0 and p["k"] <= nuevas_paradas[i-1]["k"]:
            p["k"] = round(nuevas_paradas[i-1]["k"] + 0.001, 3)
    
    # Asignar IDs secuenciales
    for i, p in enumerate(nuevas_paradas):
        p["id"] = f"{num}:{i}"
    
    print(f"  Paradas finales: {len(nuevas_paradas)} (k: {nuevas_paradas[0]['k']:.3f} -> {nuevas_paradas[-1]['k']:.3f})")
    
    # 6. Guardar resultado
    v_reales["trazado"] = [trazado] + v_reales["trazado"][1:]
    v_reales["paradas"] = nuevas_paradas
    v_reales["km_total"] = km_total
    v_reales["ajustado"] = True
    return v_reales

def main():
    shutil.copyfile(DATOS / "paradas_reales.json", DATOS / "paradas_reales.backup_antes_correccion.json")
    print("Backup guardado: paradas_reales.backup_antes_correccion.json")
    
    reales, base, horarios = cargar_todo()
    
    for num, v_reales in reales.items():
        if v_reales.get("valida") and v_reales.get("trazado"):
            v_base = base.get(num, {})
            h = horarios.get(num, {})
            reales[num] = procesar_linea(num, v_reales, v_base, h)
    
(DATOS / "paradas_reales.json").write_text(json.dumps(reales, ensure_ascii=False), encoding="utf-8")
    print("\nOK Guardado en paradas_reales.json")
    print("Ejecuta 'python revisar_trazados.py' para verificar")

if __name__ == "__main__":
    main()