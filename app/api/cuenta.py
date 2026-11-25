"""Inicio de sesión con Google: canje del token de Firebase y proxy de Firebase Auth en nuestro dominio."""
import logging

from flask import Blueprint, Response, jsonify, request

from ..comunidad import Error
from ..config import FIREBASE_AUTH_HOST
from ..cuenta import create_internal_token, verificar_token_google

bp = Blueprint("cuenta", __name__)
log = logging.getLogger("guaguas")


@bp.post("/api/firebase-token")
def api_firebase_token():
    id_token = (request.get_json(silent=True) or {}).get("id_token")
    if not id_token:
        raise Error("falta id_token", 400)
    d = verificar_token_google(id_token)
    uid, email, name = d.get("uid"), d.get("email"), d.get("name")
    return jsonify(ok=True, token=create_internal_token(uid, email, name), uid=uid, email=email, name=name)


# Proxy de Firebase Auth (/__/auth/*): permite usar authDomain = este dominio. Requiere añadir
# https://<tu-dominio>/__/auth/handler como URI de redirección autorizada en el cliente OAuth de Google.
@bp.route("/__/auth/<path:resto>", methods=["GET", "POST"])
@bp.route("/__/firebase/<path:resto>", methods=["GET"])
def proxy_firebase_auth(resto):
    import requests as rq
    destino = f"https://{FIREBASE_AUTH_HOST}{request.path}"
    try:
        r = rq.request(request.method, destino, params=request.args, data=request.get_data() or None,
                       headers={"Content-Type": request.headers.get("Content-Type", ""),
                                "Accept-Language": request.headers.get("Accept-Language", "")},
                       timeout=10, allow_redirects=False)
    except rq.RequestException as e:
        log.warning("proxy firebase auth falló: %s", e)
        return jsonify(ok=False, error="no se pudo contactar con Firebase Auth"), 502
    permitidas = ("Content-Type", "Location", "Content-Language")
    resp = Response(r.content, status=r.status_code, headers={k: v for k, v in r.headers.items() if k in permitidas})
    resp.headers["Cache-Control"] = "no-store"
    return resp
