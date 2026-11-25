"""Paradas del mapa: une las paradas de todas las líneas en los puntos que ve el usuario.

Cada línea tiene sus paradas (con id «línea:índice»); varias líneas paran en el mismo poste y una parada física puede
tener una por acera. agrupar() decide qué paradas son el mismo punto del mapa y cómo se llama cada punto.
"""
import hashlib
from collections import Counter

from . import guaguas as g

MISMO_SITIO_KM = 0.012   # mismo poste: a menos de esto no pueden ser dos aceras


def agrupar(BASE, HORARIOS, radio_km=0.015):
    """{id del punto: {lat, lon, nombre, ids, lineas, solos, …}}.

    Van al mismo punto del mapa las paradas (de cualquier línea):
      - a ≤ 12 m: el mismo poste, aunque sean de sentidos distintos (fondo de saco del Hotel Gorriones);
      - a ≤ 15 m si no son de sentidos contrarios; a ≤ 60 m si no se sabe su sentido;
      - con el mismo nombre a ≤ 200 m (marquesinas de una estación), si no son de sentidos contrarios.
    Las paradas de una acera y de la de enfrente quedan separadas: cada una muestra su sentido ('solos'), y si todas
    sus guaguas van en el mismo sentido el nombre lo dice («→ Morro Jable»). Se conservan los ids de cada línea."""
    pts = sorted((p["lat"], p["lon"], p["n"], p["id"], num, p.get("solo")) for num, l in BASE.items() for p in l["paradas"])
    grupos = []
    for lat, lon, n, pid, num, solo in pts:
        for gr in grupos:
            d = g.km({"lat": lat, "lon": lon}, gr)
            contrarios = solo and gr["solos"] and solo not in gr["solos"]
            sin_sentido = not solo and not gr["solos"]   # sin sentido conocido: como antes, una sola parada a ≤ 60 m
            mismo_nombre = (d <= 0.2 and not contrarios and (not solo) == (not gr["solos"])
                            and g.norm(n) in {g.norm(x) for x in gr["nombres"]})
            if d <= MISMO_SITIO_KM or (d <= radio_km and not contrarios) or (sin_sentido and d <= 0.06) or mismo_nombre:
                gr["c"] += 1
                gr["lat"] += (lat - gr["lat"]) / gr["c"]
                gr["lon"] += (lon - gr["lon"]) / gr["c"]
                gr["ids"].add(pid); gr["nombres"].append(n); gr["lineas"].add(num)
                gr["hacia"].append(HORARIOS.get(num, {}).get("destino" if solo == "ida" else "origen") if solo else None)
                if solo:
                    gr["solos"].add(solo)
                else:
                    gr["ambos"] = True
                break
        else:
            grupos.append(dict(lat=lat, lon=lon, c=1, ids={pid}, nombres=[n], lineas={num}, solos={solo} if solo else set(), ambos=not solo,
                               hacia=[HORARIOS.get(num, {}).get("destino" if solo == "ida" else "origen") if solo else None]))
    res = {}
    for gr in grupos:
        gr["nombre"] = Counter(gr["nombres"]).most_common(1)[0][0]
        hacia = [h for h in gr.pop("hacia") if h]
        if gr.pop("ambos") or len(gr["solos"]) > 1:
            gr["solos"] = set()   # alguna guagua para aquí en los dos sentidos (o hay de los dos): sin flecha
        if hacia and len(gr["solos"]) == 1:   # todas sus guaguas en un sentido: hacia dónde van
            dest = Counter(hacia).most_common(1)[0][0]
            if not set(g.norm(dest).split()) & set(g.norm(gr["nombre"]).split()):   # «Ayto. Tuineje → Tuineje», no
                gr["nombre"] += " → " + dest
        gr["solos"] = sorted(gr["solos"])
    rep = Counter(gr["nombre"] for gr in grupos)
    for gr in grupos:  # nombres repetidos: se distinguen por las líneas que pasan (y por nº si aún coinciden)
        if rep[gr["nombre"]] > 1:
            gr["nombre"] += " · L" + ", L".join(sorted(gr["lineas"]))
    vistos = Counter()
    for gr in grupos:
        vistos[gr["nombre"]] += 1
        if vistos[gr["nombre"]] > 1:
            gr["nombre"] += f" ({vistos[gr['nombre']]})"
        pid = "p" + hashlib.md5(f"{gr['lat']:.4f},{gr['lon']:.4f}".encode()).hexdigest()[:8]  # estable entre arranques
        while pid in res:
            pid += "x"
        gr["id"] = pid
        res[pid] = gr
    return res
