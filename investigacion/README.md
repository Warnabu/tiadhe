# Investigación: detección automática de subida

Prototipo de un motor que detectaría, sin que la persona pulse nada, que se ha subido a una guagua (12 señales:
GPS, acelerómetro, inferencia colectiva…). **No está conectado al servidor de producción**: la app usa el botón
"Me he subido a esta guagua" y el consenso de `app/comunidad.py`.

| Archivo | Qué es |
|---|---|
| `prototipo_sube.py` | Simulador de GPS + primera versión del motor (estructuras `Parada`, `Linea`, `PosicionGPS`) |
| `motor_senales.py` | Las 12 señales y su combinación |
| `boton_subir.py` | Estados del botón "me voy a subir" y ventana de 2 min |
| `inferencia_colectiva.py` | "¿Ha salido la guagua?" a partir de varios usuarios |
| `eta_dinamico.py` | Tiempo de llegada combinando horario y observaciones |
| `edge_cases.py` | 14 casos límite (GPS en callejón, dos líneas a la vez…) |
| `api_sube.py` | Endpoints de ejemplo (FastAPI/Flask) que unirían todo |
| `comunidad_sqlite.py` | Versión antigua (SQLite) de la comunidad, la que usan estos módulos |
| `ML_CONSIDERACIONES.md` | Ideas para pasar a un modelo aprendido |

```bash
python investigacion/test_motor_senales.py
```

Los módulos se importan entre sí por su nombre (`from motor_senales import …`); el test añade esta carpeta y la raíz
del repo al `sys.path`.
