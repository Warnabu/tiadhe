# Datos

| Archivo | Qué es | Quién lo escribe |
|---|---|---|
| `horarios.json` | Salidas de cada línea: sentido, tipo de día, hora, variante y notas | a mano |
| `lineas_base.json` | Esqueleto de cada línea (núcleos con coordenadas), duración de Moovit y desvíos opcionales (`opt`) | a mano |
| `trazados_originales.json` | Paradas y trazados tal como vienen de los mapas de Google de Tiadhe | `herramientas/importar_mapas.py` |
| `paradas_reales.json` | Lo que usa el servidor: trazados pegados a las calles reales, paradas colocadas y `k` (km desde el origen) | `herramientas/rutas_osrm.py` (antes `ajustar_rutas.py` + `limpiar_trazados.py`) |
| `paradas_mapa.json` | Paradas tal como las pinta el mapa (icono del bus), para colocar las nuestras | `herramientas/paradas_mapa.py` |
| `paradas_osm.json` | Paradas (marquesinas) de OpenStreetMap, caché | `python -m app.guaguas --actualizar-osm` |

Para actualizar paradas y rutas: `python herramientas/rehacer_rutas.py` (pasos y detalle en herramientas/README.md).

`k` de cada parada está en la escala de la longitud original de la línea (`km_total`), para que la duración total
de cada línea no cambie al redibujar. La longitud real por calles está en `km_calles`.
