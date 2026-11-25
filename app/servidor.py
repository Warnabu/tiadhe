#!/usr/bin/env python3
"""Monta la aplicación web (Flask): seguridad, rutas de la API, errores y el hilo de avisos.

    python servidor.py          # local: http://127.0.0.1:5000 (servidor.py de la raíz es el punto de entrada)
    gunicorn servidor:app       # producción (lo que arranca Render)

Dónde está cada cosa:
    config.py      entorno y constantes            datos.py      horarios, líneas y paradas cargados al arrancar
    paradas.py     puntos del mapa (agrupar)        contexto.py   hora, IP, dispositivo y comunidad de cada petición
    cuenta.py      Google/Firebase y push           avisos.py     «avísame 5 min antes» (hilo)
    tablero.py     próximas guaguas de una parada   api/          rutas HTTP, una por tema
    guaguas.py     horarios y tiempos de paso       planificador.py  cómo llegar con transbordos
    comunidad.py   avisos «me he subido»            db.py         base de datos   seguridad.py  cabeceras y límites
"""
import os

from flask import Flask, jsonify, request

from . import api, avisos, config
from .comunidad import Error
from .contexto import COM, ahora_ts, client_ip
from .seguridad import proteger

app = Flask(__name__, static_folder=str(config.WEB), static_url_path="")
if config.PROXY_HOPS:  # proxy propio (nginx…): X-Forwarded-For con N saltos de confianza
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=config.PROXY_HOPS)
proteger(app, client_ip)   # cabeceras de seguridad, límite por IP, tamaño máximo (app/seguridad.py)
api.registrar(app)


@app.errorhandler(Error)
def error_comunidad(e):
    resp = jsonify(ok=False, error=str(e))
    if e.codigo == 429:
        resp.headers["Retry-After"] = "3600"
    return resp, e.codigo


@app.errorhandler(500)
def error_500(e):
    app.logger.exception("Error 500 en %s %s", request.method, request.path)  # detalle al log, no al cliente
    return jsonify(ok=False, error="Error interno del servidor"), 500


@app.before_request
def _mantenimiento():
    if request.path.startswith("/api/") and request.method != "GET":
        try:
            COM.mantenimiento(ahora_ts())
        except Exception:
            app.logger.exception("mantenimiento fallido")


if config.RECORDATORIOS_ACTIVOS:
    avisos.iniciar()

# Nombres que usan las pruebas y las herramientas (from app import servidor as s; s.PARADAS…)
from . import guaguas as g  # noqa: E402,F401
from .comunidad import Comunidad  # noqa: E402,F401
from .config import DEBUG, RADIO_PARADA, RADIO_PUNTO, TZ  # noqa: E402,F401
from .contexto import ahora_local  # noqa: E402,F401
from .cuenta import create_internal_token, verify_internal_token  # noqa: E402,F401
from .datos import BASE, COLOR, HORARIOS, OSM, PARADAS, PLAN, nombre_corto  # noqa: E402,F401

if __name__ == "__main__":
    print(f"{len(PARADAS)} paradas · {len(OSM)} paradas OSM · http://127.0.0.1:{os.environ.get('PORT', 5000)}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=DEBUG)
