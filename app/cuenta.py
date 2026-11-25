"""Cuenta de Google (Firebase) y notificaciones push.

Al iniciar sesión, la web manda el id_token de Firebase y recibe una sesión interna (JWT) que usa para las favoritas.
Las notificaciones push van por Firebase Cloud Messaging.
"""
import logging
import time

import jwt
from flask import request

from .comunidad import Error
from .config import FIREBASE_CRED_PATH, JWT_ALG, JWT_EXP_HOURS, JWT_SECRET

try:
    import firebase_admin
    from firebase_admin import auth as firebase_auth, credentials, messaging
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False

log = logging.getLogger("guaguas")
_firebase_app = None


def init_firebase():
    global _firebase_app
    if not FIREBASE_AVAILABLE:
        return False
    if _firebase_app:
        return True
    try:
        _firebase_app = firebase_admin.initialize_app(credentials.Certificate(FIREBASE_CRED_PATH))
        return True
    except Exception as e:
        log.error("Firebase init failed (FIREBASE_CRED_PATH=%s): %s", FIREBASE_CRED_PATH, e)
        return False


def verificar_token_google(id_token):
    """id_token de Firebase → sus datos (uid, email, name). Error si no vale."""
    if not FIREBASE_AVAILABLE:
        raise Error("firebase-admin no instalado", 500)
    if not init_firebase():
        raise Error("Firebase no configurado (falta FIREBASE_CRED_PATH)", 500)
    try:
        return firebase_auth.verify_id_token(id_token)
    except Exception as e:
        log.info("firebase id_token rechazado: %s", e)
        raise Error("token de Firebase no válido", 401)


def create_internal_token(uid: str, email: str = None, display_name: str = None) -> str:
    ahora = int(time.time())
    return jwt.encode(dict(uid=uid, email=email, name=display_name, iat=ahora, exp=ahora + JWT_EXP_HOURS * 3600),
                      JWT_SECRET, algorithm=JWT_ALG)


def verify_internal_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise Error("token expirado", 401)
    except jwt.InvalidTokenError:
        raise Error("token inválido", 401)


def uid_sesion():
    """uid de la sesión de la petición (cabecera Authorization: Bearer <token interno>)."""
    cab = request.headers.get("Authorization", "")
    if not cab.startswith("Bearer "):
        raise Error("inicia sesión para guardar favoritas", 401)
    return verify_internal_token(cab[7:])["uid"]


def send_fcm_push(token: str, title: str, body: str, data: dict = None):
    """Devuelve (ok, motivo). motivo='token_invalido' si FCM dice que el token ya no existe."""
    if not FIREBASE_AVAILABLE:
        return False, "firebase_no_instalado"
    if not init_firebase():
        return False, "firebase_no_configurado"
    try:
        messaging.send(messaging.Message(notification=messaging.Notification(title=title, body=body),
                                         data=data or {}, token=token))
        return True, None
    except (messaging.UnregisteredError, messaging.SenderIdMismatchError):
        return False, "token_invalido"
    except Exception as e:
        log.warning("FCM send failed: %s", e)
        return False, "fallo_fcm"
