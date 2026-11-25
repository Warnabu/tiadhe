"""La página (con vista previa al compartir), comprobación de salud y utilidades de pruebas."""
import os
import subprocess
from html import escape

from flask import Blueprint, Response, jsonify, request

from .. import db as pg_db
from ..comunidad import Error
from ..config import DEBUG, EN_RENDER, WEB
from ..datos import PARADAS, nombre_corto

bp = Blueprint("web", __name__)
_index = {}


@bp.get("/")
def inicio():
    """La web, con la vista previa (Open Graph) adaptada si el enlace es de una parada: ?parada=<id>."""
    if "html" not in _index:
        _index["html"] = (WEB / "index.html").read_text(encoding="utf-8")
    html_ = _index["html"]
    base = ("https://" if EN_RENDER else request.scheme + "://") + request.host
    titulo, desc = "Guaguas Fuerteventura", ("Cuándo pasa la guagua por tu parada en Fuerteventura: horarios oficiales "
                                             "de todas las líneas, paradas y cómo llegar.")
    url = base + "/"
    pid = request.args.get("parada", "")
    if pid in PARADAS:
        gr = PARADAS[pid]
        titulo = f"Guaguas en {nombre_corto(pid)}"
        desc = f"Próximas guaguas de las líneas {', '.join(sorted(gr['lineas']))} en esta parada, con sus horarios oficiales."
        url = f"{base}/?parada={pid}"
    og = "\n".join([
        f'<meta name="description" content="{escape(desc)}">',
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="Guaguas Fuerteventura">',
        f'<meta property="og:title" content="{escape(titulo)}">',
        f'<meta property="og:description" content="{escape(desc)}">',
        f'<meta property="og:url" content="{escape(url)}">',
        f'<meta property="og:image" content="{base}/icons/icon-512.png">',
        '<meta name="twitter:card" content="summary">'])
    i, j = html_.index("<!--og-->"), html_.index("<!--/og-->") + len("<!--/og-->")
    html_ = html_[:i] + og + html_[j:]
    if pid in PARADAS:
        html_ = html_.replace("<title>Guaguas Fuerteventura</title>", f"<title>{escape(titulo)} · Guaguas FTV</title>", 1)
    return Response(html_, mimetype="text/html")


@bp.get("/health")
def health():
    return jsonify(ok=True, service="guaguas-ftv", db="postgresql" if pg_db.USE_POSTGRES else "sqlite")


@bp.get("/api/debug/pool")
def api_debug_pool():
    if not DEBUG:
        raise Error("no encontrado", 404)
    pool = pg_db.POOL
    return jsonify(use_postgres=pg_db.USE_POSTGRES,
                   pool_min=pool.minconn if pool else None, pool_max=pool.maxconn if pool else None,
                   pool_size=len(getattr(pool, "_pool", [])) if pool else None,
                   pool_used=len(getattr(pool, "_used", {})) if pool else None,
                   pool_rused=len(getattr(pool, "_rused", {})) if pool else None)


@bp.get("/api/debug/version")
def api_debug_version():
    if not DEBUG:
        raise Error("no encontrado", 404)
    try:
        git_hash = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=os.path.dirname(__file__)).decode().strip()
    except Exception:
        git_hash = "unknown"
    return jsonify(git_hash=git_hash)
