"""Configuración: todo lo que viene del entorno (variables de Render) y las constantes de la app, en un solo sitio."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).resolve().parent.parent
WEB = RAIZ / "web"
TZ = ZoneInfo("Atlantic/Canary")
EN_RENDER = bool(os.environ.get("RENDER"))

# Modo pruebas (simular la hora con ?hora=, endpoints /api/debug/*). Nunca en producción, aunque se ponga la variable.
DEBUG = bool(os.environ.get("GUAGUAS_DEBUG")) and not EN_RENDER

# Sesión interna (JWT) que se entrega tras iniciar sesión con Google
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    if EN_RENDER:
        raise SystemExit("Falta la variable de entorno JWT_SECRET (genera una con: python -c \"import secrets;print(secrets.token_hex(32))\")")
    JWT_SECRET = "solo-desarrollo-local"  # nunca en producción
JWT_ALG = "HS256"
JWT_EXP_HOURS = 24

# Firebase (inicio de sesión con Google y notificaciones push)
FIREBASE_CRED_PATH = os.environ.get("FIREBASE_CRED_PATH", "/etc/secrets/guaguas-ftv1-0c96bbab863c.json")
FIREBASE_AUTH_HOST = os.environ.get("FIREBASE_AUTH_HOST", "guaguas-ftv1.firebaseapp.com")

# IP real del visitante: proxy propio con N saltos de confianza, o Render/Cloudflare (CF-Connecting-IP)
PROXY_HOPS = int(os.environ.get("GUAGUAS_PROXY_HOPS", os.environ.get("GUAGUAS_PROXY", "0")) or 0)
DETRAS_DE_PROXY = bool(os.environ.get("TRUST_CF_CONNECTING_IP") or EN_RENDER) and not PROXY_HOPS
LOG_IP = bool(os.environ.get("GUAGUAS_LOG_IP"))   # diagnóstico: registrar qué cabeceras de IP llegan

# «Avísame 5 min antes»: el hilo que envía los avisos solo corre en producción (un solo proceso de gunicorn)
RECORDATORIOS_ACTIVOS = EN_RENDER or bool(os.environ.get("GUAGUAS_RECORDATORIOS"))
MINUTOS_AVISO = 5

# Cómo llegar: desde una parada también se miran las de alrededor; desde un punto, todas las cercanas
RADIO_PARADA = 500    # m andando
RADIO_PUNTO = 1500    # m andando

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
