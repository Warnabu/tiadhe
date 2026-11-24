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
  planificador.py  cómo llegar con transbordos
  seguridad.py     cabeceras HTTP, IP real detrás del proxy, límite de peticiones por IP
web/             la web (PWA): index.html, cliente.js, sw.js
datos/           horarios, paradas y trazados — qué es cada archivo en datos/README.md
herramientas/    scripts para preparar las rutas (importar, ajustar a las calles, revisar)
tests/           pruebas del servidor
docs/            guías (base de datos)
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
ALLOW_SQLITE_FALLBACK=1 python tests/test_seguridad.py
ALLOW_SQLITE_FALLBACK=1 python tests/test_funciones.py
ALLOW_SQLITE_FALLBACK=1 python tests/test_produccion.py     # mejor con DATABASE_URL real
```

**Actualizar las rutas** (si cambian los mapas de Tiadhe):

```bash
python herramientas/importar_mapas.py      # mapas de Google → datos/trazados_originales.json
python herramientas/ajustar_rutas.py       # los pega a las calles reales → datos/paradas_reales.json
python herramientas/limpiar_trazados.py    # quita rodeos fuera de la calle, ramales y picos que deja el ajuste
python herramientas/rutas_osrm.py          # cada línea, un trazo continuo por sus paradas (OSRM) — lo que se publica
python herramientas/revisar_trazados.py    # informe de cosas raras (solo lectura)
```

## Qué hace la app

- Próximas guaguas en cada parada. Las horas estimadas muestran siempre **lo más pronto** que puede pasar
  (rango "en 17-30 min" o "desde las 11:15"): es preferible esperar un poco a perderla.
- **Tiempo real sin registro**: "Me he subido a esta guagua"; cuando coinciden varias personas, la hora se corrige
  para las paradas siguientes. El panel muestra la actividad del día para animar a avisar.
- **Avísame 5 min antes** (notificación push, aunque la web esté cerrada).
- **Ver una línea** (tocando su número): recorrido, próximas salidas y paradas.
- **Cómo llegar** desde donde estás (o desde un pueblo o una parada) a una parada o un pueblo. Se miran todas las
  paradas a las que se puede ir andando (1,5 km desde un punto, 500 m desde una parada) y se elige la mejor, contando
  lo que se camina. Cada parada tiene «Ir aquí» y «Salir desde aquí». Opciones: guaguas directas y combinaciones
  con hasta 2 transbordos (`app/planificador.py`), dejando al menos 5 min entre lo más tarde que llega una y lo más
  pronto que sale la otra, y cambiando de guagua en la primera parada en la que se pueda (sin rodeos).
  Con **Elegir camino** el mapa muestra solo ese camino: el trozo exacto de cada línea (de la parada donde te subes a
  la que te bajas), sus paradas y lo que hay que andar (desde donde estás, si diste permiso de ubicación).
- **Ver una sola línea**: botón «Líneas» bajo el buscador; deja en el mapa solo esa línea y sus paradas (se recuerda).
- **Favoritas** (con cuenta de Google), recientes, enlace para compartir una parada con vista previa.

## Vigilancia

`.github/workflows/vigilancia.yml` visita la web cada 10 minutos (así Render no se duerme) y comprueba que responde y
que hay guaguas en las próximas horas. Si falla, abre una incidencia con la etiqueta `caida` (GitHub avisa por correo y
en la app); la cierra sola cuando vuelve a funcionar. GitHub desactiva las tareas programadas si el repo pasa 60 días
sin cambios: si ocurre, se reactiva en *Actions → Vigilancia → Enable workflow*.

## Despliegue

Render (`render.yaml`): instala `requirements.txt` y arranca `gunicorn servidor:app`, Python de `.python-version`.
Variables: `DATABASE_URL`, `JWT_SECRET`, `FIREBASE_CRED_PATH`, `FIREBASE_AUTH_HOST`, `TRUST_CF_CONNECTING_IP` /
`GUAGUAS_PROXY_HOPS` (IP real detrás de proxy), `MAX_REGISTROS_IP`.

## Base de datos y seguridad

- La base de datos (Supabase) y cómo consultarla: [docs/BASE_DE_DATOS.md](docs/BASE_DE_DATOS.md).
- Seguridad (`app/seguridad.py`): cabeceras HTTP (HSTS, anti-clickjacking, CSP), límite de peticiones por IP en lo que
  escribe o cuesta, peticiones de máx. 32 KB, Leaflet con integridad (SRI), modo pruebas imposible en producción,
  Row Level Security en todas las tablas de Supabase y las IP guardadas solo como huella (HMAC).
- Nunca subas al repo `.env`, el JSON de la cuenta de servicio de Firebase ni claves de Supabase.

## Cómo funciona

1. **Horarios** (`datos/horarios.json`): salidas por línea, sentido, tipo de día y variante.
2. **Recorridos** (`datos/paradas_reales.json`): paradas de cada línea con `k` = km desde el origen, y el trazado.
   `app/guaguas.py` reparte el tiempo de viaje según `k` y calcula a qué hora pasa cada salida por cada parada.
3. **Comunidad** (`app/comunidad.py`): cada dispositivo tiene una clave ECDSA P-256 propia y firma sus avisos; el
   servidor solo guarda la clave pública. Un aviso no cambia nada hasta que coinciden ≥2 dispositivos con peso
   suficiente; entonces la hora se corrige para las paradas siguientes y, con el tiempo, se aprende cuánto tarda
   de verdad cada línea.
4. **Rutas en el mapa** (`herramientas/rutas_osrm.py`): cada línea es un único trazo continuo calculado con OSRM
   (calles de OpenStreetMap con su geometría completa) pasando por sus paradas de ida en orden, con el sentido de la
   marcha en cada una (así toma la calzada correcta y no da medias vueltas) y puntos guía del mapa de Tiadhe; donde la
   ruta se aparta del mapa de Tiadhe se añaden más guías, y donde no hay calle razonable (falta en OpenStreetMap) se
   sigue el dibujo de Tiadhe. Imprime un informe por línea (km frente a los oficiales, paradas lejos del trazo, desvíos).
   Antes se usaba el ajuste a la red de calles (`herramientas/ajustar_rutas.py`): el trazado de los mapas de Google se "pega" a la red de
   calles de OpenStreetMap con un modelo oculto de Markov (el método estándar para ajustar un GPS a un mapa). La ruta
   va por las mismas calles que el original sin inventar rodeos; una segunda pasada da preferencia a las calles que
   usan otras líneas. Donde el original va por una calle que no está en OSM se conserva su dibujo. Las paradas
   oficiales a ≤25 m de la ruta no se mueven. Donde varias líneas comparten calle, `app/bandas.py` las separa en
   paralelo y la web las desplaza unos píxeles según el zoom.
