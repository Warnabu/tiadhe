"""Guaguas FTV: lo que corre en producción.

Arranque y web
    servidor.py     monta la aplicación (seguridad, rutas, errores, hilo de avisos)
    api/            rutas HTTP por tema: paradas, viajes (cómo llegar), comunidad, avisos, cuenta, web
    config.py       variables de entorno y constantes
Datos y cálculo (sin Flask: se pueden usar desde la terminal o las herramientas)
    datos.py        horarios, líneas y paradas cargados al arrancar
    guaguas.py      horarios y tiempos de paso (python -m app.guaguas -p "Las Playitas")
    paradas.py      qué paradas de las líneas son el mismo punto del mapa
    planificador.py cómo llegar, con transbordos; y el trozo de línea de cada tramo
    bandas.py       líneas en paralelo donde comparten calle
    tablero.py      próximas guaguas de una parada, agrupadas por destino
Usuarios
    contexto.py     hora, IP, dispositivo firmado y comunidad de cada petición
    comunidad.py    avisos «me he subido»: firmas, consenso, reputación, recordatorios, favoritas
    cuenta.py       Google/Firebase (sesión) y notificaciones push
    avisos.py       «avísame 5 min antes» (hilo que manda las notificaciones)
    db.py           PostgreSQL (o SQLite en local)
    seguridad.py    cabeceras HTTP, IP real, límite de peticiones
"""
