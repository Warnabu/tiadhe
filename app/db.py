"""PostgreSQL/Supabase database layer for Guaguas FTV comunidad module.
Replaces SQLite with asyncpg/psycopg2 for production on Render + Supabase.
Supports local testing with SQLite fallback when DATABASE_URL not set.
"""
import os
from pathlib import Path
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from psycopg2.pool import ThreadedConnectionPool
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL and PSYCOPG2_AVAILABLE)

if USE_POSTGRES:
    try:
        POOL = ThreadedConnectionPool(1, 5, dsn=DATABASE_URL, cursor_factory=RealDictCursor,
                                      connect_timeout=10, keepalives=1, keepalives_idle=30,
                                      keepalives_interval=10, keepalives_count=5)
    except Exception as e:
        if not os.environ.get("ALLOW_SQLITE_FALLBACK"):
            raise RuntimeError(f"No se pudo conectar a PostgreSQL ({e}). Define ALLOW_SQLITE_FALLBACK=1 solo en local.") from e
        print(f"PostgreSQL connection failed: {e}. Falling back to SQLite (ALLOW_SQLITE_FALLBACK).")
        USE_POSTGRES = False
        POOL = None
        _SQLITE_DB = None
        _SQLITE_LOCK = threading.Lock()
else:
    POOL = None
    _SQLITE_DB = None
    _SQLITE_LOCK = threading.Lock()


def sql_greatest():
    """SQLite: MAX(a,b) escalar. PostgreSQL: GREATEST (MAX es agregado)."""
    return "GREATEST" if USE_POSTGRES else "MAX"


def sql_least():
    return "LEAST" if USE_POSTGRES else "MIN"


PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS dispositivos (
    id TEXT PRIMARY KEY,
    pub BYTEA NOT NULL,
    creado TIMESTAMPTZ NOT NULL,
    ult TIMESTAMPTZ NOT NULL,
    rep REAL NOT NULL DEFAULT 20.0,
    confirmadas INT NOT NULL DEFAULT 0,
    discrepantes INT NOT NULL DEFAULT 0,
    subidas INT NOT NULL DEFAULT 0,
    strikes INT NOT NULL DEFAULT 0,
    strikes_ts TIMESTAMPTZ NOT NULL DEFAULT 'epoch',
    bloqueado TIMESTAMPTZ NOT NULL DEFAULT 'epoch',
    firebase_uid TEXT,
    apple_uid TEXT,
    fcm_token TEXT
);

CREATE TABLE IF NOT EXISTS subidas (
    id BIGSERIAL PRIMARY KEY,
    dev TEXT NOT NULL REFERENCES dispositivos(id),
    viaje TEXT NOT NULL,
    cluster TEXT NOT NULL,
    linea TEXT NOT NULL,
    sentido TEXT NOT NULL,
    variante TEXT NOT NULL,
    ts BIGINT NOT NULL,
    pred_ts BIGINT NOT NULL,
    inicio_ts BIGINT NOT NULL,
    peso REAL NOT NULL,
    gps INT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendiente',
    evaluado BIGINT NOT NULL DEFAULT 0,
    intencion_id TEXT,
    confianza REAL,
    senales_json JSONB,
    UNIQUE(dev, viaje)
);

CREATE TABLE IF NOT EXISTS observaciones (
    viaje TEXT NOT NULL,
    cluster TEXT NOT NULL,
    linea TEXT NOT NULL,
    sentido TEXT NOT NULL,
    variante TEXT NOT NULL,
    ts BIGINT NOT NULL,
    pred_ts BIGINT NOT NULL,
    inicio_ts BIGINT NOT NULL,
    n INT NOT NULL,
    peso REAL NOT NULL,
    creado TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (viaje, cluster)
);

CREATE TABLE IF NOT EXISTS nonces (
    n TEXT PRIMARY KEY,
    ts BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS registros (
    ip TEXT NOT NULL,
    ts BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS ubicaciones_activas (
    dev TEXT NOT NULL REFERENCES dispositivos(id),
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    ts BIGINT NOT NULL,
    intencion_id TEXT,
    PRIMARY KEY (dev)
);

CREATE INDEX IF NOT EXISTS idx_subidas_dev_ts ON subidas(dev, ts);
CREATE INDEX IF NOT EXISTS idx_subidas_viaje_cluster ON subidas(viaje, cluster);
CREATE INDEX IF NOT EXISTS idx_subidas_linea_sentido ON subidas(linea, sentido, variante);
CREATE INDEX IF NOT EXISTS idx_nonces_ts ON nonces(ts);
CREATE INDEX IF NOT EXISTS idx_registros_ip_ts ON registros(ip, ts);
CREATE INDEX IF NOT EXISTS idx_ubicaciones_ts ON ubicaciones_activas(ts);
"""

SQLITE_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS dispositivos(
    id TEXT PRIMARY KEY, pub BLOB, creado INT, ult INT, rep REAL,
    confirmadas INT DEFAULT 0, discrepantes INT DEFAULT 0, subidas INT DEFAULT 0,
    strikes INT DEFAULT 0, strikes_ts INT DEFAULT 0, bloqueado INT DEFAULT 0,
    firebase_uid TEXT, apple_uid TEXT, fcm_token TEXT
);
CREATE TABLE IF NOT EXISTS subidas(
    id INTEGER PRIMARY KEY AUTOINCREMENT, dev TEXT, viaje TEXT, cluster TEXT,
    linea TEXT, sentido TEXT, variante TEXT, ts INT, pred_ts INT, inicio_ts INT,
    peso REAL, gps INT, estado TEXT DEFAULT 'pendiente', evaluado INT DEFAULT 0,
    intencion_id TEXT, confianza REAL, senales_json TEXT, UNIQUE(dev, viaje)
);
CREATE TABLE IF NOT EXISTS observaciones(
    viaje TEXT, cluster TEXT, linea TEXT, sentido TEXT, variante TEXT,
    ts INT, pred_ts INT, inicio_ts INT, n INT, peso REAL, creado INT,
    PRIMARY KEY(viaje, cluster)
);
CREATE TABLE IF NOT EXISTS nonces(n TEXT PRIMARY KEY, ts INT);
CREATE TABLE IF NOT EXISTS registros(ip TEXT, ts INT);
CREATE TABLE IF NOT EXISTS ubicaciones_activas(
    dev TEXT PRIMARY KEY, lat REAL, lon REAL, ts INT, intencion_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_subidas_dev_ts ON subidas(dev, ts);
CREATE INDEX IF NOT EXISTS idx_subidas_viaje_cluster ON subidas(viaje, cluster);
CREATE INDEX IF NOT EXISTS idx_subidas_linea_sentido ON subidas(linea, sentido, variante);
CREATE INDEX IF NOT EXISTS idx_nonces_ts ON nonces(ts);
CREATE INDEX IF NOT EXISTS idx_registros_ip_ts ON registros(ip, ts);
CREATE INDEX IF NOT EXISTS idx_ubicaciones_ts ON ubicaciones_activas(ts);
"""

INIT_LOCK = threading.Lock()
_INITIALIZED = False


def _get_sqlite_db():
    global _SQLITE_DB
    with _SQLITE_LOCK:
        if _SQLITE_DB is None:
            db_path = os.environ.get("SQLITE_DB_PATH", str(Path(__file__).resolve().parent.parent / "datos" / "comunidad_test.db"))
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            _SQLITE_DB = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
            _SQLITE_DB.row_factory = sqlite3.Row
        return _SQLITE_DB


# Columnas que el código trata como enteros (epoch). Si la BD real viene de un esquema antiguo, se corrige o se avisa.
_ENTEROS = {"subidas": ("ts", "pred_ts", "inicio_ts", "evaluado"), "observaciones": ("ts", "pred_ts", "inicio_ts"),
            "nonces": ("ts",), "registros": ("ts",), "ubicaciones_activas": ("ts",)}


def _migrar(cur) -> None:
    """CREATE TABLE IF NOT EXISTS no corrige tablas viejas. Aquí se arreglan los tipos conocidos y se avisa del resto."""
    cur.execute("SELECT table_name AS t, column_name AS c, data_type AS d FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = ANY(%s)", (list(_ENTEROS),))
    tipos = {(r["t"], r["c"]): r["d"] for r in cur.fetchall()}
    if tipos.get(("subidas", "evaluado")) == "boolean":
        print("MIGRACIÓN: subidas.evaluado boolean -> bigint")
        cur.execute("ALTER TABLE subidas ALTER COLUMN evaluado DROP DEFAULT")
        cur.execute("ALTER TABLE subidas ALTER COLUMN evaluado TYPE BIGINT USING (CASE WHEN evaluado THEN 1 ELSE 0 END)")
        cur.execute("ALTER TABLE subidas ALTER COLUMN evaluado SET DEFAULT 0")
        tipos[("subidas", "evaluado")] = "bigint"
    for t, cols in _ENTEROS.items():
        for c in cols:
            d = tipos.get((t, c))
            if d and d not in ("bigint", "integer", "smallint"):
                print(f"AVISO ESQUEMA: {t}.{c} es {d}, el código espera entero")
    # Seguridad (Supabase): la API REST pública de Supabase expone todas las tablas del esquema. Con Row Level
    # Security activado y sin ninguna política, nadie puede leerlas ni escribirlas por esa vía (ni con la clave
    # "anon"). El servidor no se ve afectado: se conecta como propietario de las tablas, que se salta RLS.
    try:
        cur.execute("SAVEPOINT rls")
        cur.execute("SELECT c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = current_schema() AND c.relkind = 'r' AND NOT c.relrowsecurity "
                    "AND pg_get_userbyid(c.relowner) = current_user")
        for fila in cur.fetchall():
            print(f"SEGURIDAD: activando Row Level Security en {fila['t']}", flush=True)
            cur.execute(f'ALTER TABLE "{fila["t"]}" ENABLE ROW LEVEL SECURITY')
        cur.execute("RELEASE SAVEPOINT rls")
        cur.execute("SELECT count(*) FILTER (WHERE c.relrowsecurity) AS si, count(*) AS total FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = current_schema() AND c.relkind = 'r'")
        r = cur.fetchone()
        print(f"SEGURIDAD: Row Level Security activo en {r['si']} de {r['total']} tablas", flush=True)
    except Exception as e:   # nunca impedir que el servidor arranque por esto
        cur.execute("ROLLBACK TO SAVEPOINT rls")
        print(f"AVISO SEGURIDAD: no se pudo activar Row Level Security ({e})", flush=True)


def init_schema() -> None:
    """Initialize database schema (idempotent)."""
    global _INITIALIZED
    with INIT_LOCK:
        if _INITIALIZED:
            return
        if USE_POSTGRES:
            conn = POOL.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(PG_SCHEMA)
                    conn.commit()
                    _migrar(cur)
                    conn.commit()
            finally:
                POOL.putconn(conn)
        else:
            db = _get_sqlite_db()
            db.executescript(SQLITE_SCHEMA)
            db.commit()
        _INITIALIZED = True


@contextmanager
def transaction():
    """Context manager for a transaction."""
    if USE_POSTGRES:
        conn = POOL.getconn()
        try:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            POOL.putconn(conn)
    else:
        db = _get_sqlite_db()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


def _convert_params(query: str, params: Tuple) -> Tuple[str, Tuple]:
    """Convert %s placeholders to ? for SQLite."""
    if USE_POSTGRES:
        return query, params
    return query.replace("%s", "?"), params


def execute(query: str, params: Tuple = (), fetch: bool = False) -> Optional[List[Dict[str, Any]]]:
    """Execute a query and optionally fetch results."""
    query, params = _convert_params(query, params)
    for attempt in range(3):
        conn = None
        try:
            conn = POOL.getconn() if USE_POSTGRES else _get_sqlite_db()
            try:
                if USE_POSTGRES:
                    with conn.cursor() as cur:
                        cur.execute(query, params)
                        if fetch:
                            return cur.fetchall()
                        conn.commit()
                        return None
                else:
                    cur = conn.cursor()
                    cur.execute(query, params)
                    if fetch:
                        rows = cur.fetchall()
                        return [dict(r) for r in rows]
                    conn.commit()
                    return None
            finally:
                if USE_POSTGRES and conn:
                    POOL.putconn(conn)
        except psycopg2.OperationalError as e:
            if "SSL" in str(e) or "connection" in str(e).lower():
                print(f"PostgreSQL SSL/connection error (attempt {attempt + 1}/3): {e}")
                if attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))
            else:
                raise
        except Exception:
            raise


def execute_one(query: str, params: Tuple = ()) -> Optional[Dict[str, Any]]:
    """Execute a query and fetch one result."""
    query, params = _convert_params(query, params)
    for attempt in range(3):
        conn = None
        try:
            conn = POOL.getconn() if USE_POSTGRES else _get_sqlite_db()
            try:
                if USE_POSTGRES:
                    with conn.cursor() as cur:
                        cur.execute(query, params)
                        return cur.fetchone()
                else:
                    cur = conn.cursor()
                    cur.execute(query, params)
                    row = cur.fetchone()
                    return dict(row) if row else None
            finally:
                if USE_POSTGRES and conn:
                    POOL.putconn(conn)
        except psycopg2.OperationalError as e:
            if "SSL" in str(e) or "connection" in str(e).lower():
                print(f"PostgreSQL SSL/connection error (attempt {attempt + 1}/3): {e}")
                if attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))
            else:
                raise
        except Exception:
            raise


def cleanup_old_nonces(max_age_seconds: int = 600, ahora: int = None) -> None:
    """Remove expired nonces."""
    if ahora is None:
        ahora = int(time.time())
    corte = ahora - max_age_seconds
    execute("DELETE FROM nonces WHERE ts < %s", (corte,))

def cleanup_old_ubicaciones(max_age_seconds: int = 300, ahora: int = None) -> None:
    """Remove stale active locations."""
    if ahora is None:
        ahora = int(time.time())
    corte = ahora - max_age_seconds
    execute("DELETE FROM ubicaciones_activas WHERE ts < %s", (corte,))
