"""Rutas HTTP de la app, una por tema. registrar(app) las monta todas."""
from . import avisos, comunidad, cuenta, paradas, viajes, web


def registrar(app):
    for m in (web, paradas, viajes, comunidad, avisos, cuenta):
        app.register_blueprint(m.bp)
