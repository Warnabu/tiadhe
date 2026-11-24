"""Cómo llegar de una parada a otra, con transbordos (algoritmo Connection Scan).

Se "despliegan" todas las salidas de hoy y mañana en conexiones parada→parada con su hora. Recorriendo las conexiones
por orden de salida se obtiene la llegada más temprana a cada parada; con eso se reconstruye el itinerario.

Criterio prudente (es mejor esperar que perder la guagua):
  - Para coger una guagua se cuenta con lo más PRONTO que puede pasar por la parada.
  - Al bajarse se cuenta con lo más TARDE que puede llegar.
  - Un transbordo solo vale si entre lo uno y lo otro hay al menos MIN_TRANSBORDO minutos.
Máximo MAX_TRAMOS guaguas por viaje y caminatas cortas (≤ CAMINAR_M) entre paradas cercanas.
"""
import bisect
import math
from datetime import datetime, timedelta

from . import guaguas as g

MIN_TRANSBORDO = 5      # minutos
MAX_TRAMOS = 3          # guaguas por itinerario (2 transbordos)
CAMINAR_M = 300         # caminata máxima entre dos paradas para un transbordo
VEL_PIE = 70            # metros por minuto (≈4 km/h)


class Planificador:
    def __init__(self, horarios, base, parada_de, paradas):
        """parada_de: id de parada de línea → id de parada del mapa. paradas: id del mapa → {lat, lon, nombre…}."""
        self.horarios, self.base, self.parada_de, self.paradas = horarios, base, parada_de, paradas
        self._dias = {}
        # caminatas cortas entre paradas cercanas
        self.vecinas = {p: [] for p in paradas}
        ids = list(paradas)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                d = g.km(paradas[a], paradas[b]) * 1000
                if d <= CAMINAR_M:
                    t = math.ceil(d / VEL_PIE) + 1
                    self.vecinas[a].append((b, t))
                    self.vecinas[b].append((a, t))

    # ------------------------------------------------------------------ conexiones
    def _conexiones_dia(self, fecha):
        """[(sale_pronto, llega_tarde, desde, hasta, viaje, linea, destino, sale_est, llega_est, nota)] de un día."""
        if fecha in self._dias:
            return self._dias[fecha]
        res, dia = [], g.tipo_dia(fecha)
        for num, lin in self.horarios.items():
            for s in lin["salidas"]:
                if dia not in s["d"].split(","):
                    continue
                seq = g.recorrido(num, s["s"], s, self.base)
                if len(seq) < 2:
                    continue
                h, m = map(int, s["h"].split(":"))
                inicio = datetime(fecha.year, fecha.month, fecha.day, h, m)
                viaje = g.clave_viaje(fecha, num, s)
                destino = g.nombre_destino(seq[-1], self.base[num])
                puntos = []
                for p in seq:
                    pid = self.parada_de.get(p.get("id"))
                    if not pid or (puntos and puntos[-1][0] == pid):
                        continue
                    off = g.minutos(p, self.base[num])
                    est = inicio + timedelta(minutes=off)
                    antes, despues = g.margenes(dict(prec="oficial" if off == 0 else "estimado", cal="t" in p, off=off))
                    puntos.append((pid, est, est - timedelta(minutes=antes), est + timedelta(minutes=despues)))
                for (a, ea, pa, _), (b, eb, _, tb) in zip(puntos, puntos[1:]):
                    res.append((pa, tb, a, b, viaje, num, destino, ea, eb, s.get("nota", "")))
        res.sort(key=lambda c: c[0])
        self._dias[fecha] = res
        return res

    def _conexiones(self, desde):
        hoy = desde.date()
        todas = self._conexiones_dia(hoy) + self._conexiones_dia(hoy + timedelta(days=1))
        todas.sort(key=lambda c: c[0])
        if len(self._dias) > 6:   # no acumular días viejos en memoria
            for f in sorted(self._dias)[:-4]:
                self._dias.pop(f, None)
        return todas

    # ------------------------------------------------------------------ búsqueda
    def _buscar(self, conns, origen, destino, salida, max_tramos=MAX_TRAMOS):
        """Llegada más temprana (prudente) a 'destino' saliendo de 'origen' a partir de 'salida'. Itinerario o None."""
        INF = datetime.max
        llega = {origen: salida}            # hora a la que seguro estás en cada parada
        tramos = {origen: 0}                # guaguas usadas hasta cada parada
        previo = {}                         # parada → ("bus", conexión de subida, conexión de bajada) | ("pie", desde, min)
        subida = {}                         # viaje → conexión en la que se subió
        for v, t in self.vecinas.get(origen, []):
            if salida + timedelta(minutes=t) < llega.get(v, INF):
                llega[v], tramos[v], previo[v] = salida + timedelta(minutes=t), 0, ("pie", origen, t)
        i0 = bisect.bisect_left(conns, salida, key=lambda c: c[0])
        for c in conns[i0:]:
            pronto, tarde, a, b, viaje = c[0], c[1], c[2], c[3], c[4]
            if pronto > llega.get(destino, INF):
                break   # ya nada puede llegar antes
            if viaje not in subida:
                if a not in llega:
                    continue
                # primera guagua: basta con estar en la parada; transbordo: margen extra
                margen = timedelta(0) if tramos[a] == 0 else timedelta(minutes=MIN_TRANSBORDO)
                if llega[a] + margen > pronto or tramos[a] >= max_tramos:
                    continue
                subida[viaje] = c
            if tarde < llega.get(b, INF):
                n = tramos[subida[viaje][2]] + 1
                llega[b], tramos[b], previo[b] = tarde, n, ("bus", subida[viaje], c)
                for v, t in self.vecinas.get(b, []):
                    if tarde + timedelta(minutes=t) < llega.get(v, INF):
                        llega[v], tramos[v], previo[v] = tarde + timedelta(minutes=t), n, ("pie", b, t)
        if destino not in llega or destino not in previo:
            return None
        # reconstrucción
        pasos, x = [], destino
        while x != origen:
            p = previo[x]
            if p[0] == "pie":
                pasos.append(dict(tipo="pie", desde=p[1], hasta=x, minutos=p[2]))
                x = p[1]
            else:
                sub, baj = p[1], p[2]
                pasos.append(dict(tipo="bus", linea=sub[5], destino=sub[6], viaje=sub[4], nota=sub[9],
                                  desde=sub[2], hasta=baj[3], sale=sub[0], sale_est=sub[7], llega=baj[1], llega_est=baj[8]))
                x = sub[2]
        pasos.reverse()
        return pasos if any(p["tipo"] == "bus" for p in pasos) else None

    def itinerarios(self, origen, destino, ahora, n=4, horas=20):
        """Hasta n itinerarios que merezcan la pena en las próximas 'horas' horas.

        Se buscan con 1, 2 y 3 guaguas como máximo y se descarta una opción si otra sale a la misma hora o más tarde,
        llega a la misma hora o antes y no tiene más transbordos (así no se propone dar vueltas para llegar igual)."""
        conns = self._conexiones(ahora)
        limite = ahora + timedelta(hours=horas)
        candidatos = {}
        for k in range(1, MAX_TRAMOS + 1):
            salida = ahora
            for _ in range(n + 2):
                it = self._buscar(conns, origen, destino, salida, k)
                if not it:
                    break
                buses = [p for p in it if p["tipo"] == "bus"]
                if buses[0]["sale"] > limite:
                    break
                candidatos.setdefault(tuple(p["viaje"] for p in buses), it)
                salida = buses[0]["sale"] + timedelta(minutes=1)
        def resumen(it):
            buses = [p for p in it if p["tipo"] == "bus"]
            return buses[0]["sale"], buses[-1]["llega"], len(buses)
        lista = list(candidatos.values())
        buenas = []
        for it in lista:
            s0, l0, t0 = resumen(it)
            peor = any((s1 >= s0 and l1 <= l0 and t1 <= t0) and (s1, l1, t1) != (s0, l0, t0)
                       for s1, l1, t1 in map(resumen, lista))
            if not peor:
                buenas.append(it)
        buenas.sort(key=lambda it: resumen(it)[:2])
        return buenas[:n]


# ---------------------------------------------------------------------- el camino en el mapa
class Caminos:
    """Trozo exacto del trazado de una línea entre la parada donde se sube y la parada donde se baja."""

    def __init__(self, base, parada_de, paradas):
        self.base, self.paradas = base, paradas
        self.de_linea = {}        # (línea, parada del mapa) → [índices de parada de la línea]
        for lid, pid in parada_de.items():
            num, i = lid.rsplit(":", 1)
            self.de_linea.setdefault((num, pid), []).append(int(i))
        self.inv = {(n, j): pid for (n, pid), idx in self.de_linea.items() for j in idx}
        self._geo = {}

    def _geometria(self, num):
        """Puntos del trazado en metros (plano local) con su distancia acumulada."""
        if num not in self._geo:
            l = self.base[num]
            tr = l.get("trazado") or [[[p["lat"], p["lon"]] for p in l["paradas"] if "opt" not in p]]
            pts, acc, ini = [], 0.0, set()
            for parte in tr:
                ini.add(len(pts))
                for j, (la, lo) in enumerate(parte):
                    if j:
                        acc += g.km(dict(lat=parte[j - 1][0], lon=parte[j - 1][1]), dict(lat=la, lon=lo))
                    pts.append((la, lo, acc))
            total_k = max((p.get("k", 0) for p in l["paradas"]), default=0) or acc
            self._geo[num] = (pts, ini, acc / total_k if total_k else 1.0)
        return self._geo[num]

    def _proyectar(self, num, lat, lon, k):
        """Posición (km a lo largo del trazado) de un punto, buscando cerca de donde dice su km 'k'."""
        pts, ini, esc = self._geometria(num)
        cx = math.cos(math.radians(lat)) * 111.32
        mejor = None
        for i in range(1, len(pts)):
            if i in ini:
                continue
            (a0, o0, d0), (a1, o1, d1) = pts[i - 1], pts[i]
            ax, ay, bx, by = (o0 - lon) * cx, (a0 - lat) * 110.57, (o1 - lon) * cx, (a1 - lat) * 110.57
            vx, vy = bx - ax, by - ay
            l2 = vx * vx + vy * vy
            t = 0.0 if l2 < 1e-12 else max(0.0, min(1.0, -(ax * vx + ay * vy) / l2))
            dist = math.hypot(ax + t * vx, ay + t * vy)
            pos = d0 + t * (d1 - d0)
            coste = dist + (0 if k is None else 0.1 * max(0.0, abs(pos - k * esc) - 1.5))   # mismo sitio, pero en su vuelta
            if mejor is None or coste < mejor[0]:
                mejor = (coste, pos)
        return mejor[1] if mejor else 0.0

    def _cortar(self, num, p0, p1):
        """Puntos del trazado entre dos posiciones (en el orden del viaje), en trozos si el trazado tiene saltos."""
        pts, ini, _ = self._geometria(num)
        a, b = min(p0, p1), max(p0, p1)
        trozos, cur = [], []
        for i in range(1, len(pts)):
            if i in ini:
                if cur:
                    trozos.append(cur)
                cur = []
                continue
            (la0, lo0, d0), (la1, lo1, d1) = pts[i - 1], pts[i]
            if d1 < a or d0 > b or d1 == d0:
                continue
            en = lambda x: [round(la0 + (x - d0) / (d1 - d0) * (la1 - la0), 6), round(lo0 + (x - d0) / (d1 - d0) * (lo1 - lo0), 6)]
            if not cur:
                cur.append(en(max(a, d0)))
            cur.append(en(min(b, d1)))
        if cur:
            trozos.append(cur)
        trozos = [t for t in trozos if len(t) >= 2]
        if p1 < p0:
            trozos = [t[::-1] for t in trozos[::-1]]
        return trozos

    def tramo(self, num, sentido, desde, hasta):
        """{'p': [[[lat, lon]…]…], 'paradas': [ids del mapa por las que pasa]} del viaje de 'desde' a 'hasta'."""
        ps = self.base[num]["paradas"]
        ia_, ib_ = self.de_linea.get((num, desde), []), self.de_linea.get((num, hasta), [])
        pares = [(ia, ib) for ia in ia_ for ib in ib_
                 if (ps[ib]["k"] > ps[ia]["k"]) == (sentido == "ida") and ps[ib]["k"] != ps[ia]["k"]]
        if not pares:
            return None
        ia, ib = min(pares, key=lambda x: abs(ps[x[1]]["k"] - ps[x[0]]["k"]))
        ka, kb = ps[ia]["k"], ps[ib]["k"]
        p0 = self._proyectar(num, ps[ia]["lat"], ps[ia]["lon"], ka)
        p1 = self._proyectar(num, ps[ib]["lat"], ps[ib]["lon"], kb)
        lo_, hi_ = min(ka, kb), max(ka, kb)
        medio = sorted((i for i, x in enumerate(ps) if "opt" not in x and lo_ < x["k"] < hi_),
                       key=lambda i: ps[i]["k"], reverse=sentido != "ida")
        por = []
        for x in [ia] + medio + [ib]:
            pid = self.inv.get((num, x))
            if pid and pid not in por:
                por.append(pid)
        return dict(p=self._cortar(num, p0, p1), paradas=por, i=(ia, ib))
