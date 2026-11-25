# Herramientas

Scripts para preparar los datos de paradas y rutas (`datos/`). No corren en el servidor: se ejecutan a mano cuando
cambian los mapas de Tiadhe, y el resultado (`datos/paradas_reales.json`) se sube al repo. Todo de una vez:

```bash
python herramientas/rehacer_rutas.py            # pasos 1 a 7
python herramientas/rehacer_rutas.py --desde 4  # si los mapas de Tiadhe no han cambiado
```

| Paso | Script | Qué hace | Escribe |
|---|---|---|---|
| 1 | `importar_mapas.py` | Descarga los mapas de Google de Tiadhe (paradas y trazado de cada línea) y los valida | `trazados_originales.json` |
| 2 | `ajustar_rutas.py` | Pega el trazado a la red de calles (map matching) y calcula `k` (km desde el origen) de cada parada | `paradas_reales.json` |
| 3 | `limpiar_trazados.py` | Quita rodeos, ramales y picos del paso 2 (queda como referencia para el paso 6) | `paradas_reales.json` |
| 4 | `paradas_mapa.py` | Paradas tal como las pinta el mapa de OpenStreetMap (icono del bus en las teselas) | `paradas_mapa.json` |
| 5 | `colocar_paradas.py` | Cada parada a su icono del mapa y con su sentido (capas Ida/Vuelta de Tiadhe); separa las de cada acera | `paradas_reales.json` |
| 6 | `rutas_osrm.py` | Cada línea, un único trazo por sus paradas (OSRM), sin rodeos, con consenso entre líneas y pulido final | `paradas_reales.json` |
| 7 | `verificar.py` | Comprobaciones (solo lectura): qué guaguas pasan por cada parada, comparación con Tiadhe, trazos | — |

Opciones útiles: casi todos admiten números de línea (`rutas_osrm.py 05 12`) y `--dry-run` (informa sin escribir).
`rutas_osrm.py --pulir` solo repasa los trazos actuales (espigas y escalones).

## Qué se descarga y dónde se guarda

Todo en `datos/.cache_osrm/` (no va al repo; se vuelve a bajar si falta):

- `red/`: teselas de calles de OSRM (pasos 2-3).
- `rutas/`: respuestas de OSRM (paso 6). Borrarlas obliga a pedirlas otra vez.
- `teselas/`: imágenes del mapa de OpenStreetMap (paso 4).
- `kml/`: mapas de Google de Tiadhe con sus capas Ida/Vuelta (paso 5). tiadhe.com bloquea durante un rato si se le
  piden varias páginas seguidas: los scripts esperan entre peticiones.

Los servidores públicos (OSRM, teselas de OpenStreetMap) se usan con pausas para no abusar de ellos.

## Reglas importantes (por qué los datos son como son)

- `k` de cada parada está en la escala de la longitud oficial de la línea (`km_total`): redibujar no cambia la
  duración de las líneas.
- Sentido de una parada (`solo: ida|vuelta`): de las capas de Tiadhe (en la línea 11 su «ida» es nuestra «vuelta»;
  se detecta sola). La primera y la última parada de cada línea valen siempre para los dos sentidos (lo hace el
  servidor al cargar).
- Paradas a ≤ 12 m son el mismo poste aunque sean de sentidos distintos (el servidor las junta).
