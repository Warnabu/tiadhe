# Guaguas FTV

¿Qué guagua pasa por mi parada en Fuerteventura y cuándo? Horarios oficiales de las 18 líneas de Tiadhe,
corregidos en vivo por la gente que se sube ("me he subido a esta guagua").

## Estructura

```
app/             lo que corre en producción
  servidor.py      web y API (Flask)
  guaguas.py       horarios y tiempos de paso por parada
  comunidad.py     avisos "me he subido": firmas, consenso, reputación, aprendizaje
  db.py            PostgreSQL (SQLite en local)
  bandas.py        líneas en paralelo donde comparten calle
web/             la web (PWA): index.html, cliente.js, sw.js
ios/             app iOS (SwiftUI) — montaje en docs/IOS_SETUP_GUIDE.md
datos/           horarios, paradas y trazados — qué es cada archivo en datos/README.md
herramientas/    scripts para preparar las rutas (importar, ajustar a las calles, revisar)
investigacion/   prototipo de detección automática de subida (NO está en producción)
tests/           pruebas del servidor
docs/            guías
servidor.py      punto de entrada (Render arranca `gunicorn servidor:app`)
```

## Uso diario

```bash
pip install -r requirements-dev.txt                 # una vez (requirements.txt = solo lo del servidor)
ALLOW_SQLITE_FALLBACK=1 python servidor.py          # web en http://127.0.0.1:5000
python -m app.guaguas -p "Las Playitas"             # consulta rápida en la terminal
```

Con `GUAGUAS_DEBUG=1` se puede simular la hora: `/api/parada/<id>?hora=2026-09-24T10:05`.

**Pruebas** (también se ejecutan solas en GitHub en cada push, pestaña *Actions*):

```bash
ALLOW_SQLITE_FALLBACK=1 python tests/test_comunidad.py
ALLOW_SQLITE_FALLBACK=1 python tests/test_produccion.py     # mejor con DATABASE_URL real
python investigacion/test_motor_senales.py
```

**Actualizar las rutas** (si cambian los mapas de Tiadhe):

```bash
python herramientas/importar_mapas.py      # mapas de Google → datos/trazados_originales.json
python herramientas/ajustar_rutas.py       # los pega a las calles reales → datos/paradas_reales.json
python herramientas/revisar_trazados.py    # informe de cosas raras (solo lectura)
```

## Despliegue

Render (`render.yaml`): instala `requirements.txt` y arranca `gunicorn servidor:app`, Python de `.python-version`.
Variables: `DATABASE_URL`, `JWT_SECRET`, `FIREBASE_CRED_PATH`, `FIREBASE_AUTH_HOST`, `TRUST_CF_CONNECTING_IP` /
`GUAGUAS_PROXY_HOPS` (IP real detrás de proxy), `MAX_REGISTROS_IP`.

## Cómo funciona

1. **Horarios** (`datos/horarios.json`): salidas por línea, sentido, tipo de día y variante.
2. **Recorridos** (`datos/paradas_reales.json`): paradas de cada línea con `k` = km desde el origen, y el trazado.
   `app/guaguas.py` reparte el tiempo de viaje según `k` y calcula a qué hora pasa cada salida por cada parada.
3. **Comunidad** (`app/comunidad.py`): cada dispositivo tiene una clave ECDSA P-256 propia y firma sus avisos; el
   servidor solo guarda la clave pública. Un aviso no cambia nada hasta que coinciden ≥2 dispositivos con peso
   suficiente; entonces la hora se corrige para las paradas siguientes y, con el tiempo, se aprende cuánto tarda
   de verdad cada línea.
4. **Rutas en el mapa** (`herramientas/ajustar_rutas.py`): el trazado de los mapas de Google se "pega" a la red de
   calles de OpenStreetMap con un modelo oculto de Markov (el método estándar para ajustar un GPS a un mapa). La ruta
   va por las mismas calles que el original sin inventar rodeos; una segunda pasada da preferencia a las calles que
   usan otras líneas. Donde el original va por una calle que no está en OSM se conserva su dibujo. Las paradas
   oficiales a ≤25 m de la ruta no se mueven. Donde varias líneas comparten calle, `app/bandas.py` las separa en
   paralelo y la web las desplaza unos píxeles según el zoom.
