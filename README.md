# Guaguas FTV

¿Qué guagua pasa por mi parada en Fuerteventura y cuándo? Horarios oficiales de las 18 líneas de Tiadhe,
corregidos en vivo por la gente que se sube ("me he subido a esta guagua").

- **Web (PWA)**: `web/` — mapa Leaflet, buscador, tablero por parada, modo "tiempo real" con cuenta de Google.
- **Servidor**: `servidor.py` (Flask + gunicorn) desplegado en Render con PostgreSQL (`render.yaml`).
- **App iOS**: `ios/GuaguasFTV/` (SwiftUI) — guía de montaje en `IOS_SETUP_GUIDE.md`.

## Arrancar en local

```bash
pip install -r requirements.txt
ALLOW_SQLITE_FALLBACK=1 python servidor.py      # http://127.0.0.1:5000
```

`GUAGUAS_DEBUG=1` permite simular la hora con `?hora=2026-09-24T10:05`. Sin `DATABASE_URL` la comunidad usa SQLite.

Variables de producción: `DATABASE_URL`, `JWT_SECRET`, `FIREBASE_CRED_PATH`, `FIREBASE_AUTH_HOST`,
`TRUST_CF_CONNECTING_IP` / `GUAGUAS_PROXY_HOPS` (IP real detrás de proxy), `MAX_REGISTROS_IP`.

## Pruebas

```bash
ALLOW_SQLITE_FALLBACK=1 python test.py               # comunidad: firmas, consenso, reputación, aprendizaje
ALLOW_SQLITE_FALLBACK=1 python test_produccion.py    # regresiones de producción (mejor con DATABASE_URL real)
python test_motor_senales.py                          # motor de detección de subida (investigación)
```

## Cómo funciona

1. **Horarios** (`datos/horarios.json`): salidas por línea, sentido, tipo de día y variante.
2. **Recorridos** (`datos/paradas_reales.json`): paradas de cada línea con `k` = km desde el origen y el trazado
   por carretera. `guaguas.py` reparte el tiempo de viaje según `k` y calcula la hora a la que cada salida pasa por
   cada parada.
3. **Comunidad** (`comunidad_pg.py`): cada dispositivo tiene una clave ECDSA P-256 propia y firma sus avisos; el
   servidor solo guarda la clave pública. Un aviso no cambia nada hasta que coinciden ≥2 dispositivos con peso
   suficiente; entonces la hora se corrige para las paradas siguientes y, con el tiempo, se aprende cuánto tarda
   de verdad cada línea.

## Datos de las rutas

```bash
python importar_mapas.py              # paradas y trazados desde los mapas de Google de tiadhe.com
python reconstruir_trazados.py        # trazados por carretera real (OSRM), rotondas como esquinas, paradas recolocadas
python revisar_trazados.py            # informe de solo lectura: tramos largos, puntas, paradas lejos del trazado
python analizar_rutas.py              # análisis de k, desvíos y paradas por línea
```

`reconstruir_trazados.py` usa el trazado de Google solo como guía: enruta de parada en parada por la red viaria
(OSRM, en el sentido de la marcha), sustituye cada rotonda por una esquina en su centro, quita idas y vueltas
sin parada, coloca cada parada en su marquesina de OpenStreetMap (o sobre la vía) y recalcula `k`. Avisa de los
tramos que no encajan con el original para revisarlos a mano. Las respuestas de OSRM se guardan en
`datos/.cache_osrm/`.

## Investigación

`motor_senales.py`, `boton_subir.py`, `inferencia_colectiva.py`, `eta_dinamico.py`, `edge_cases.py`, `api_sube.py` y
`prototipo_sube.py` son un prototipo de detección automática de subida (12 señales). **No están conectados al
servidor de producción.** Ver `ML_CONSIDERACIONES.md`.
