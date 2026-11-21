"""Comunidad: subidas colaborativas con PostgreSQL/Supabase.
Reemplaza comunidad.py (SQLite) para producción en Render + Supabase.
"""
import base64, hashlib, math, threading, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

import postgresql_db as pg

TZ = ZoneInfo("Atlantic/Canary")

def _ts_to_dt(ts: float) -> datetime:
    """Convert Unix timestamp to timezone-aware datetime for PostgreSQL."""
    return datetime.fromtimestamp(ts, TZ)

# ------------------------------------------------ parámetros (ajustables)
REP_INICIAL, PESO_MIN, PESO_MAX = 20.0, 0.35, 0.90
FACTOR_SIN_GPS = 0.4
QUORUM_DISPOSITIVOS, QUORUM_PESO, MAX_CUOTA = 2, 1.0, 0.75
TOLERANCIA = 3 * 60
SUBE, BAJA = 0.06, 10.0
MAX_AL_DIA, MIN_ENTRE_SUBIDAS, VEL_MAX_KMH = 12, 8 * 60, 110
RADIO_GPS_M, MAX_REGISTROS_IP = 200, 8
K_PRIOR, MIN_OBS_APRENDER = 4, 3
NIVELES = [(30, "Nuevo"), (55, "Colaborador"), (75, "Fiable"), (101, "Veterano")]


def mediana_pond(pares):
    pares = sorted(pares)
    if not pares:
        return 0.0
    total, acc = sum(w for _, w in pares), 0.0
    for v, w in pares:
        acc += w
        if acc >= total / 2:
            return v
    return pares[-1][0]


def mensaje(metodo, ruta, ts, nonce, cuerpo):
    return "\n".join([metodo, ruta, str(ts), nonce, cuerpo]).encode()


def km_ll(a, b):
    p = math.pi / 180
    h = math.sin((b[0] - a[0]) * p / 2) ** 2 + math.cos(a[0] * p) * math.cos(b[0] * p) * math.sin((b[1] - a[1]) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


class Error(Exception):
    def __init__(self, msg, codigo=400):
        super().__init__(msg)
        self.codigo = codigo


class Comunidad:
    def __init__(self, ids_cluster, centros, tz, reloj=time.time):
        """ids_cluster: {cluster: {ids de paradas de línea}} · centros: {cluster: (lat, lon)}"""
        self.ids_cluster, self.centros, self.tz, self.reloj = ids_cluster, centros, tz, reloj
        self.cluster_de = {i: c for c, ids in ids_cluster.items() for i in ids}
        self.lock = threading.Lock()
        pg.init_schema()
        self._snap, self._snap_ts = None, 0

    # ---------------------------------------------------------- dispositivos y firmas
    @staticmethod
    def id_de(pub_raw):
        return hashlib.sha256(pub_raw).hexdigest()[:32]

    def registrar(self, pub_b64, ip, ahora):
        try:
            raw = base64.b64decode(pub_b64)
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        except Exception:
            raise Error("clave pública no válida")
        dev = self.id_de(raw)
        with self.lock:
            existing = pg.execute_one("SELECT 1 FROM dispositivos WHERE id=%s", (dev,))
            if not existing:
                corte = int(ahora - 86400)
                n = pg.execute_one("SELECT COUNT(*) as c FROM registros WHERE ip=%s AND ts>%s", (ip, corte))["c"]
                if n >= MAX_REGISTROS_IP:
                    raise Error("demasiados dispositivos nuevos desde esta red; inténtalo mañana", 429)
                ahora_dt = _ts_to_dt(ahora)
                pg.execute("INSERT INTO registros VALUES(%s,%s)", (ip, ahora_dt))
                pg.execute("INSERT INTO dispositivos(id,pub,creado,ult,rep) VALUES(%s,%s,%s,%s,%s)",
                           (dev, raw, ahora_dt, ahora_dt, REP_INICIAL))
        return dev, raw

    def verificar(self, dev, ts, nonce, firma_b64, msg, ahora, pub_raw=None):
        if abs(ahora - int(ts)) > 120:
            raise Error(f"reloj del dispositivo desajustado o petición antigua (servidor: {int(ahora)})", 401)
        with self.lock:
            if pub_raw is None:
                fila = pg.execute_one("SELECT pub FROM dispositivos WHERE id=%s", (dev,))
                if not fila:
                    raise Error("dispositivo desconocido", 401)
                pub_raw = fila["pub"]
            try:
                sig = base64.b64decode(firma_b64)
                der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
                ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub_raw).verify(der, msg, ec.ECDSA(hashes.SHA256()))
            except (InvalidSignature, ValueError):
                raise Error("firma no válida", 401)
            nonce_key = dev + nonce
            if pg.execute_one("SELECT 1 FROM nonces WHERE n=%s", (nonce_key,)):
                raise Error("petición repetida", 401)
            pg.execute("INSERT INTO nonces VALUES(%s,%s)", (nonce_key, int(ahora)))
            pg.cleanup_old_nonces(ahora=ahora)
        return dev

    # ------------------------------------------------------------ reputación
    @staticmethod
    def rep_efectiva(fila, ahora):
        dias = max(0, (ahora - fila["ult"]) / 86400)
        return fila["rep"] if dias < 30 else REP_INICIAL + (fila["rep"] - REP_INICIAL) * 0.5 ** (dias / 90)

    @staticmethod
    def peso(rep):
        return PESO_MIN + (PESO_MAX - PESO_MIN) * max(0, min(100, rep)) / 100

    @staticmethod
    def nivel(rep):
        return next(n for lim, n in NIVELES if rep < lim)

    def perfil(self, dev, ahora):
        f = pg.execute_one("SELECT * FROM dispositivos WHERE id=%s", (dev,))
        if not f:
            return None
        rep = self.rep_efectiva(f, ahora)
        return dict(nivel=self.nivel(rep), puntos=round(rep), peso=round(self.peso(rep), 2), subidas=f["subidas"],
                    confirmadas=f["confirmadas"], discrepantes=f["discrepantes"])

    # ---------------------------------------------------------------- subidas
    def enviar_subida(self, dev, viaje, cluster, pred, dist_m, ahora):
        with self.lock:
            f = pg.execute_one("SELECT * FROM dispositivos WHERE id=%s", (dev,))
            if not f:
                raise Error("dispositivo no encontrado", 401)
            if f["bloqueado"] > ahora:
                raise Error("tus avisos están pausados por avisos imposibles repetidos; vuelve mañana", 429)

            def rechazar(msg, castigo=True):
                if castigo:
                    st = f["strikes"] + 1 if ahora - f["strikes_ts"] < 86400 else 1
                    ahora_dt = _ts_to_dt(ahora)
                    pg.execute("""UPDATE dispositivos SET strikes=%s, strikes_ts=%s, rep=MAX(0, rep-2), bloqueado=%s WHERE id=%s""",
                               (st, ahora_dt, _ts_to_dt(ahora + 86400) if st >= 6 else _ts_to_dt(0), dev))
                raise Error(msg)

            if pg.execute_one("SELECT 1 FROM subidas WHERE dev=%s AND viaje=%s", (dev, viaje)):
                raise Error("ya avisaste de esta guagua", 409)
            cnt = pg.execute_one("SELECT COUNT(*) as c FROM subidas WHERE dev=%s AND ts>%s", (dev, int(ahora - 86400)))["c"]
            if cnt >= MAX_AL_DIA:
                raise Error(f"máximo {MAX_AL_DIA} avisos al día", 429)
            ult = pg.execute_one("SELECT cluster, ts FROM subidas WHERE dev=%s ORDER BY ts DESC LIMIT 1", (dev,))
            if ult:
                dt = ahora - ult["ts"]
                if dt < MIN_ENTRE_SUBIDAS:
                    rechazar("ya marcaste una subida hace muy poco", castigo=False)
                if ult["cluster"] in self.centros and cluster in self.centros:
                    d = km_ll(self.centros[ult["cluster"]], self.centros[cluster])
                    if d > 1 and d / (dt / 3600) > VEL_MAX_KMH:
                        rechazar(f"imposible: {d:.0f} km en {dt // 60} min desde tu último aviso")
            ventana = 12 * 60 + 0.25 * pred["off"] * 60
            if abs(ahora - pred["llegada_ts"]) > ventana:
                rechazar("esa guagua no debería estar pasando por aquí ahora mismo")
            gps = 0
            if dist_m is not None:
                if dist_m > RADIO_GPS_M:
                    rechazar(f"tu ubicación está a {int(dist_m)} m de la parada")
                gps = 1
            rep_ef = self.rep_efectiva(f, ahora)
            peso = self.peso(rep_ef) * (1 if gps else FACTOR_SIN_GPS)
            ahora_int = int(ahora)
            pg.execute("""INSERT INTO subidas(dev,viaje,cluster,linea,sentido,variante,ts,pred_ts,inicio_ts,peso,gps)
                          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                       (dev, viaje, cluster, pred["linea"], pred["sentido"], pred["variante"],
                        ahora_int, pred["llegada_ts"], pred["inicio_ts"], peso, gps))
            pg.execute("UPDATE dispositivos SET subidas=subidas+1, ult=%s WHERE id=%s", (ahora_int, dev))
            estado = self._evaluar(viaje, cluster, ahora)
            self._snap_ts = 0
            return dict(estado=estado, gps=bool(gps))

    def _evaluar(self, viaje, cluster, ahora):
        filas = pg.execute("SELECT * FROM subidas WHERE viaje=%s AND cluster=%s", (viaje, cluster), fetch=True)
        m = mediana_pond([(f["ts"], f["peso"]) for f in filas])
        acuerdo = [f for f in filas if abs(f["ts"] - m) <= TOLERANCIA]
        peso = sum(f["peso"] for f in acuerdo)
        quorum = (len({f["dev"] for f in acuerdo}) >= QUORUM_DISPOSITIVOS and peso >= QUORUM_PESO
                  and max(f["peso"] for f in acuerdo) / peso <= MAX_CUOTA)
        if not quorum:
            return "pendiente"
        ts = int(mediana_pond([(f["ts"], f["peso"]) for f in acuerdo]))
        base = acuerdo[0]
ids_ok = {f["id"] for f in acuerdo}
        agora_int = int(ahora)
        for f in filas:
            if f["evaluado"]:
                continue
            if f["id"] in ids_ok:
                pg.execute("UPDATE dispositivos SET rep=MIN(100, rep+(100-rep)*%s), confirmadas=confirmadas+1 WHERE id=%s",
                           (SUBE, f["dev"]))
                est = "confirmada"
            else:
                pg.execute("UPDATE dispositivos SET rep=MAX(0, rep-%s), discrepantes=discrepantes+1 WHERE id=%s",
                           (BAJA, f["dev"]))
                est = "discrepante"
            pg.execute("UPDATE subidas SET estado=%s, evaluado=1 WHERE id=%s", (est, f["id"]))
        pg.execute("""INSERT INTO observaciones(viaje,cluster,linea,sentido,variante,ts,pred_ts,inicio_ts,n,peso,creado)
                      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                      ON CONFLICT (viaje, cluster) DO UPDATE SET
                          ts=EXCLUDED.ts, pred_ts=EXCLUDED.pred_ts, inicio_ts=EXCLUDED.inicio_ts,
                          n=EXCLUDED.n, peso=EXCLUDED.peso, creado=EXCLUDED.creado""",
                   (viaje, cluster, base["linea"], base["sentido"], base["variante"],
                    ts, int(base["pred_ts"]), base["inicio_ts"], len(acuerdo), peso, agora_int))
        return "confirmada"

    def limpiar(self, ahora):
        with self.lock:
            corte_antiguo = int(ahora - 90 * 86400)
            corte_reciente = int(ahora - 7200)
            pg.execute("DELETE FROM subidas WHERE ts<%s", (corte_antiguo,))
            pg.execute("UPDATE subidas SET estado='sin_confirmar', evaluado=1 WHERE estado='pendiente' AND ts<%s",
                       (corte_reciente,))
            pg.cleanup_old_ubicaciones()

    # ------------------------------------------- enganche con el cálculo de tiempos
    def _local(self, ts):
        return datetime.fromtimestamp(ts, self.tz).replace(tzinfo=None)

    def _snapshot(self):
        ahora = self.reloj()
        if self._snap is None or abs(ahora - self._snap_ts) > 15:
            with self.lock:
                viaje, aprendido = {}, {}
                corte_reciente = int(ahora - 36 * 3600)
                corte_aprendido = int(ahora - 60 * 86400)
                obs_recientes = pg.execute("SELECT * FROM observaciones WHERE ts>%s", (corte_reciente,), fetch=True)
                for o in obs_recientes:
                    viaje.setdefault(o["viaje"], []).append(dict(o))
                obs_aprend = pg.execute("SELECT * FROM observaciones WHERE creado>%s", (corte_aprendido,), fetch=True)
                for o in obs_aprend:
                    key = (o["linea"], o["sentido"], o["variante"], o["cluster"])
                    aprendido.setdefault(key, []).append(((o["ts"] - o["inicio_ts"]) / 60, o["peso"]))
                self._snap, self._snap_ts = dict(viaje=viaje, aprendido=aprendido), ahora
        return self._snap

    def aprendido(self, linea, sentido, variante, stop_id, off_modelo):
        vals = self._snapshot()["aprendido"].get((linea, sentido, variante, self.cluster_de.get(stop_id)))
        if not vals or len(vals) < MIN_OBS_APRENDER:
            return None
        aprendido = mediana_pond([(v, 1.0) for v, _ in vals])
        n = len(vals)
        return (n * aprendido + K_PRIOR * off_modelo) / (n + K_PRIOR)

    def ajuste(self, viaje, orden_ids, hit_id, llegada):
        obs = self._snapshot()["viaje"].get(viaje)
        if not obs or hit_id not in orden_ids:
            return None
        i, mejor = orden_ids.index(hit_id), None
        for o in obs:
            ids = self.ids_cluster.get(o["cluster"], ())
            if hit_id in ids:
                return dict(llegada=self._local(o["ts"]), prec="confirmada", n=o["n"])
            j = next((k for k, x in enumerate(orden_ids) if x in ids), None)
            if j is not None and j < i and (mejor is None or j > mejor[0]):
                mejor = (j, o)
        if mejor:
            o = mejor[1]
            retraso = max(-30, min(30, (o["ts"] - o["pred_ts"]) / 60))
            return dict(llegada=llegada + timedelta(minutes=retraso), prec="vivo", n=o["n"], retraso=retraso)
        return None

    def guardar_fcm_token(self, uid: str, fcm_token: str):
        with self.lock:
            pg.execute(
                "INSERT INTO dispositivos (id, fcm_token) VALUES (%s, %s) "
                "ON CONFLICT (id) DO UPDATE SET fcm_token=EXCLUDED.fcm_token",
                (uid, fcm_token)
            )

    def obtener_fcm_token(self, uid: str) -> str:
        row = pg.execute_one("SELECT fcm_token FROM dispositivos WHERE id=%s", (uid,))
        return row["fcm_token"] if row and row["fcm_token"] else None