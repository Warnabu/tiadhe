#!/usr/bin/env python3
"""Rehace paradas y rutas desde los mapas de Tiadhe, en el orden correcto, y comprueba el resultado.

    python herramientas/rehacer_rutas.py              # todo (tarda: OSRM y las imágenes del mapa van con pausas)
    python herramientas/rehacer_rutas.py --desde 4    # desde el paso 4 (si los anteriores ya están hechos)

Pasos (cada uno es un script que también se puede ejecutar suelto; detalle en herramientas/README.md):
  1. importar_mapas.py    mapas de Google de Tiadhe              → datos/trazados_originales.json
  2. ajustar_rutas.py     paradas y km de cada parada            → datos/paradas_reales.json
  3. limpiar_trazados.py  primer trazo limpio (referencia)       → datos/paradas_reales.json
  4. paradas_mapa.py      paradas que pinta el mapa (icono bus)  → datos/paradas_mapa.json
  5. colocar_paradas.py   cada parada a su icono, con su sentido → datos/paradas_reales.json
  6. rutas_osrm.py        cada línea, un trazo por sus paradas   → datos/paradas_reales.json
  7. verificar.py         comprobaciones (solo lectura)
Después: reiniciar el servidor (en Render basta con subir datos/paradas_reales.json a main).
"""
import argparse
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PASOS = ["importar_mapas.py", "ajustar_rutas.py", "limpiar_trazados.py", "paradas_mapa.py", "colocar_paradas.py",
         "rutas_osrm.py", "verificar.py"]


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", type=int, default=1, help="número de paso por el que empezar (1-7)")
    a = ap.parse_args(argv)
    for n, script in enumerate(PASOS, 1):
        if n < a.desde:
            continue
        print(f"\n=== {n}. {script} ===", flush=True)
        r = subprocess.run([sys.executable, str(AQUI / script)])
        if r.returncode:
            print(f"El paso {n} ({script}) ha fallado: se para aquí.")
            return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
