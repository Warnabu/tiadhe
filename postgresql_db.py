"""PostgreSQL/Supabase database layer for Guaguas FTV comunidad module.
Replaces SQLite with asyncpg/psycopg2 for production on Render + Supabase.
Supports local testing with SQLite fallback when DATABASE_URL not set.
"""
import os
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
USE_POSTGRES = DATABASE_URL and PSYCOPG2_AVAILABLE

if USE_POSTGRES:
    try:
        POOL = ThreadedConnectionPool(1, 10, dsn=DATABASE_URL, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"PostgreSQL connection failed: {e}. Falling back to SQLite.")
        USE_POSTGRES = False
        POOL = None
        _SQLITE_DB = None
        _SQLITE_LOCK = threading.Lock()
else:
    POOL = None
    _SQLITE_DB = None
    _SQLITE_LOCK = threading.Lock()


PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS dispositivos (
    id TEXT PRIMARY KEY,
    pub BYTEA NOT NULL,
    creado BIGINT NOT NULL,
    ult BIGINT NOT NULL,
    rep REAL NOT NULL DEFAULT 20.0,
    confirmadas INT NOT NULL DEFAULT 0,
    discrepantes INT NOT NULL DEFAULT 0,
    subidas INT NOT NULL DEFAULT 0,
    strikes INT NOT NULL DEFAULT 0,
    strikes_ts BIGINT NOT NULL DEFAULT 0,
    bloqueado BIGINT NOT NULL DEFAULT 0,
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
    creado BIGINT NOT NULL,
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
            db_path = os.environ.get("SQLITE_DB_PATH", "datos/comunidad_test.db")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            _SQLITE_DB = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
            _SQLITE_DB.row_factory = sqlite3.Row
        return _SQLITE_DB


def init_schema() -> None:
    """Initialize database schema (idempotent)."""
    global _INITIALIZED
    with INIT_LOCK:
        if _INITIALIZED:
            return
        if USE_POSTGRES:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(PG_SCHEMA)
                    conn.commit()
        else:
            db = _get_sqlite_db()
            db.executescript(SQLITE_SCHEMA)
            db.commit()
        _INITIALIZED = True


@contextmanager
def get_conn():
    """Get a connection from the pool (PostgreSQL) or SQLite connection."""
    if USE_POSTGRES:
        conn = POOL.getconn()
        try:
            yield conn
        finally:
            POOL.putconn(conn)
    else:
        yield _get_sqlite_db()


@contextmanager
def transaction():
    """Context manager for a transaction."""
    if USE_POSTGRES:
        with get_conn() as conn:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
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
    with get_conn() as conn:
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


def execute_one(query: str, params: Tuple = ()) -> Optional[Dict[str, Any]]:
    """Execute a query and fetch one result."""
    query, params = _convert_params(query, params)
    with get_conn() as conn:
        if USE_POSTGRES:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchone()
        else:
            cur = conn.cursor()
            cur.execute(query, params)
            row = cur.fetchone()
            return dict(row) if row else None


def cleanup_old_nonces(max_age_seconds: int = 600, ahora: int = None) -> None:
    """Remove expired nonces."""
    if ahora is None:
        ahora = int(time.time())
    execute("DELETE FROM nonces WHERE ts < %s", (ahora - max_age_seconds,))


def cleanup_old_ubicaciones(max_age_seconds: int = 300, ahora: int = None) -> None:
    """Remove stale active locations."""
    if ahora is None:
        ahora = int(time.time())
    execute("DELETE FROM ubicaciones_activas WHERE ts < %s", (ahora - max_age_seconds,))