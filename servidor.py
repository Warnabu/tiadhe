#!/usr/bin/env python3
"""Punto de entrada del servidor. El código está en app/servidor.py.

    python servidor.py                      # local: http://127.0.0.1:5000
    gunicorn servidor:app                   # producción (lo que arranca Render)
"""
import os

from app.servidor import app, PARADAS, OSM  # noqa: F401  (gunicorn importa "app")

if __name__ == "__main__":
    print(f"{len(PARADAS)} paradas · {len(OSM)} paradas OSM · http://127.0.0.1:{os.environ.get('PORT', 5000)}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("GUAGUAS_DEBUG")))
