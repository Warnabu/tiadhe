#!/usr/bin/env python3
"""
Prototipo de Detección de Subida - Guaguas FTV
==============================================
Simulador de GPS + Motor de 12 señales probabilísticas
Objetivo: accuracy > 85% en 100+ escenarios
"""

import json
import math
import random
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum
import statistics

# ============================================================
# 1. ESTRUCTURAS DE DATOS
# ============================================================

@dataclass
class PosicionGPS:
    lat: float
    lon: float
    timestamp: int
    accuracy: float = 10.0
    heading: Optional[float] = None
    speed: Optional[float] = None

@dataclass
class Parada:
    id: str
    nombre: str
    lat: float
    lon: float
    k: float

@dataclass
class Linea:
    numero: str
    nombre: str
    trazado: List[List[float]]  # [[lat, lon], ...]
    paradas: List[Parada]
    velocidad_kmh: float = 30.0

@dataclass
class Usuario:
    id: str
    posiciones: List[PosicionGPS] = field(default_factory=list)
    consulto_app_recientemente: bool = False
    ultima_consulta_app: int = 0
    gps_activo: bool = True
    ultima_subida: int = 0
    reputacion: float = 20.0

@dataclass
class Escenario:
    nombre: str
    usuario: Usuario
    linea: Linea
    parada_objetivo: Parada
    subida_real: bool
    descripcion: str

# ============================================================
# 2. UTILIDADES GEOESPACIALES
# ============================================================

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en metros entre dos puntos GPS"""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def punto_en_trazado(lat: float, lon: float, trazado: List[List[float]], umbral: float = 50.0) -> Tuple[bool, float]:
    """¿Está el punto cerca del trazado? Retorna (bool, distancia_min)"""
    dist_min = min(haversine(lat, lon, p[0], p[1]) for p in trazado)
    return dist_min <= umbral, dist_min

def direccion_hacia_parada(lat: float, lon: float, parada: Parada, heading: Optional[float]) -> float:
    """0-1: qué tan alineado está el heading con la dirección a la parada"""
    if heading is None:
        return 0.5
    # Bearing hacia la parada
    dlon = math.radians(parada.lon - lon)
    lat1, lat2 = math.radians(lat), math.radians(parada.lat)
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing_parada = (math.degrees(math.atan2(y, x)) + 360) % 360
    diff = abs(heading - bearing_parada)
    diff = min(diff, 360 - diff)
    return max(0, 1 - diff / 90)  # 1 si alineado, 0 si perpendicular/opuesto

def velocidad_humana(speed: Optional[float]) -> float:
    """0-1: probabilidad de que la velocidad sea humana (caminando)"""
    if speed is None:
        return 0.5
    if speed < 0.5:
        return 1.0  # quieto
    if speed < 2.0:
        return 0.9  # caminando lento
    if speed < 3.0:
        return 0.7  # caminando rápido
    if speed < 5.0:
        return 0.3  # corriendo
    return 0.05  # vehículo

# ============================================================
# 3. MOTOR DE 12 SEÑALES
# ============================================================

class MotorSenales:
    def __init__(self):
        # (probabilidad_base, peso_confiabilidad)
        self.señales = {
            's01_boton': (0.95, 1.0),
            's02_gps_app': (0.80, 1.0),
            's03_gps_no_app': (0.50, 1.0),
            's04_camina_app': (0.70, 1.0),
            's05_camina_no_app': (0.30, 1.0),
            's06_quietud': (0.60, 0.7),
            's07_movimiento': (0.40, 0.7),
            's08_social': (0.85, 0.8),
            's09_ruta': (0.75, 1.0),
            's10_retraso': (0.65, 0.9),
            's11_multitud': (0.70, 0.8),
            's12_en_bus': (0.85, 1.0),
        }

    def evaluar(self, escenario: Escenario, otros_usuarios: List[Usuario] = None) -> Dict:
        """Evalúa todas las señales y retorna score final + detalle"""
        u = escenario.usuario
        p = escenario.parada_objetivo
        l = escenario.linea
        pos = u.posiciones[-1] if u.posiciones else None

        if not pos:
            return {'score': 0.0, 'detalle': {}, 'umbral': 'sin_datos'}

        dist_parada = haversine(pos.lat, pos.lon, p.lat, p.lon)
        en_parada = dist_parada <= 200
        cerca_parada = dist_parada <= 500

        # Velocidad actual (pos.speed está en m/s, convertir a km/h)
        speed = pos.speed * 3.6 if pos.speed is not None else None
        if speed is None and len(u.posiciones) >= 2:
            p1, p2 = u.posiciones[-2], u.posiciones[-1]
            dt = p2.timestamp - p1.timestamp
            if dt > 0:
                speed = haversine(p1.lat, p1.lon, p2.lat, p2.lon) / dt * 3.6  # km/h

        # Calcular tiempo en parada (solo posiciones CERCA de la parada Y quietas)
        t_espera = 0
        if en_parada and len(u.posiciones) >= 2:
            # Filtrar posiciones en/cerca de la parada (≤ 200m) Y speed < 1 km/h (quietas)
            pos_parada = []
            for p_u in u.posiciones:
                dist = haversine(p_u.lat, p_u.lon, p.lat, p.lon)
                sp = p_u.speed * 3.6 if p_u.speed is not None else 0
                if dist <= 200 and sp < 1.0:
                    pos_parada.append(p_u)
            if len(pos_parada) >= 2:
                t_espera = pos_parada[-1].timestamp - pos_parada[0].timestamp

        # Calcular calidad GPS (accuracy promedio reciente)
        accuracy_prom = pos.accuracy
        if len(u.posiciones) >= 3:
            accuracy_prom = statistics.mean(p.accuracy for p in u.posiciones[-3:])

        # Detectar si está EN RUTA (trazado) con consistencia
        en_ruta, dist_ruta = punto_en_trazado(pos.lat, pos.lon, l.trazado)
        en_ruta_consistente = False
        # Ventana más larga (5 puntos), tolerar 2 saltos GPS
        if len(u.posiciones) >= 5:
            pts_en_ruta = sum(1 for p_u in u.posiciones[-5:]
                             if punto_en_trazado(p_u.lat, p_u.lon, l.trazado)[0])
            en_ruta_consistente = pts_en_ruta >= 3
        elif len(u.posiciones) >= 3:
            pts_en_ruta = sum(1 for p_u in u.posiciones[-3:]
                             if punto_en_trazado(p_u.lat, p_u.lon, l.trazado)[0])
            en_ruta_consistente = pts_en_ruta >= 2
        # Si speed coincide con bus y hay al menos 1 punto en ruta, considerar consistente
        if speed and 10 <= speed <= 50 and en_ruta:
            en_ruta_consistente = True

        # Detectar ambigüedad de líneas (flag explícito o variante)
        ambigueza_lineas = getattr(u, 'ambigueza_lineas', False) or getattr(u, 'variante_posible', False)

        # --- Señales ---
        detalle = {}

        # S01: Botón explícito
        boton_activado = getattr(u, 'boton_activado', False)
        detalle['s01_boton'] = self.señales['s01_boton'][0] if boton_activado else 0.0

        # S02/S03: GPS en parada + consultó app
        if en_parada:
            # Penalizar si accuracy muy mala (GPS poco fiable)
            penalty_gps = 1.0
            if accuracy_prom > 30:
                penalty_gps = 0.5
            elif accuracy_prom > 20:
                penalty_gps = 0.75

            if u.consulto_app_recientemente:
                detalle['s02_gps_app'] = self.señales['s02_gps_app'][0] * penalty_gps
            else:
                # Penalizar espera muy corta (< 2 min) sin app MÁS FUERTE
                penalty_tiempo = 1.0
                if t_espera < 120:
                    penalty_tiempo = 0.15  # Muy bajo: 1 min sin app = poco fiable
                elif t_espera < 300:
                    penalty_tiempo = 0.4
                detalle['s03_gps_no_app'] = self.señales['s03_gps_no_app'][0] * penalty_gps * penalty_tiempo

        # S04/S05: Camina hacia parada
        if cerca_parada and not en_parada:
            dir_score = direccion_hacia_parada(pos.lat, pos.lon, p, pos.heading)
            if dir_score > 0.5:
                if u.consulto_app_recientemente:
                    detalle['s04_camina_app'] = self.señales['s04_camina_app'][0] * dir_score
                else:
                    detalle['s05_camina_no_app'] = self.señales['s05_camina_no_app'][0] * dir_score

        # S06/S07: Acelerómetro (quietud/movimiento)
        if speed is not None:
            if speed < 1.0:
                detalle['s06_quietud'] = self.señales['s06_quietud'][0]
            else:
                detalle['s07_movimiento'] = self.señales['s07_movimiento'][0]

        # S08: Otros usuarios reportan subida
        if otros_usuarios:
            n_con_gps = sum(1 for ou in otros_usuarios if ou.gps_activo and ou.posiciones)
            if n_con_gps >= 2:
                detalle['s08_social'] = self.señales['s08_social'][0] * min(1.0, n_con_gps / 5)

        # S09: GPS coincide con trazado (CONSISTENTE y EN MOVIMIENTO)
        # Requiere: en ruta consistente + speed > 2 km/h (movimiento) + speed < 60
        if en_ruta_consistente and speed and 2 < speed < 60:
            # Mayor peso si speed coincide con bus (10-50 km/h)
            speed_match = 1.0
            if 10 <= speed <= 50:
                speed_match = 1.3  # BOOST para bus
            elif speed < 5:
                speed_match = 0.7
            # Clamp factor de distancia a [0, 1] para evitar negativos por GPS ruidoso
            dist_factor = max(0.0, min(1.0, 1 - dist_ruta / 50))
            detalle['s09_ruta'] = min(1.0, self.señales['s09_ruta'][0] * speed_match * dist_factor)
        
        # BONUS: Si claramente en bus (ruta consistente + speed 10-50 + accuracy mala OK)
        en_bus = en_ruta_consistente and speed and 10 <= speed <= 50
        if en_bus:
            # Señal extra fuerte para viaje en bus
            detalle['s12_en_bus'] = 0.85

        # S10: Tiempo espera > estimado (posible cancelación) -> penalización
        # S11: ≥3 usuarios en parada
        if otros_usuarios:
            n_en_parada = sum(1 for ou in otros_usuarios
                             if ou.posiciones and haversine(ou.posiciones[-1].lat, ou.posiciones[-1].lon, p.lat, p.lon) <= 200)
            if n_en_parada >= 3:
                detalle['s11_multitud'] = self.señales['s11_multitud'][0]

        # --- PENALIZACIONES (reducen score final) ---
        penalizaciones = []

        # Penalización: GPS muy impreciso (callejón, interior)
        # PERO no penalizar si vamos en bus (speed 5-50 en ruta consistente)
        en_bus = en_ruta_consistente and speed and 5 <= speed <= 50
        if not en_bus:
            if accuracy_prom > 50:
                penalizaciones.append(('gps_muy_malo', 0.3))
            elif accuracy_prom > 30:
                penalizaciones.append(('gps_malo', 0.6))
        else:
            # EN BUS: GPS malo es normal, NO penalizar, incluso bonus por consistencia
            if accuracy_prom > 30:
                detalle['s09_ruta'] = min(1.0, detalle.get('s09_ruta', 0) * 1.2)

        # Penalización: Espera muy larga sin bus (posible cancelación)
        if en_parada and t_espera > 600:
            penalizaciones.append(('espera_larga', 0.25))

        # Penalización: Ambigüedad de líneas (FUERTE - 2 líneas en misma parada)
        if ambigueza_lineas and not getattr(u, 'variante_posible', False):
            # Solo penalizar ambigüedad pura, no variante
            penalizaciones.append(('ambiguedad_lineas', 0.25))

        # Penalización: Variante de línea (MÍNIMA - solo incertidumbre menor)
        # NO aplicar si ya hay penalización de ambigüedad
        if getattr(u, 'variante_posible', False) and not ambigueza_lineas:
            penalizaciones.append(('variante_linea', 0.98))

        # Penalización: No en ruta ni en parada (lejos de todo)
        # Usar en_ruta_consistente para no penalizar GPS ruidoso en bus
        if not en_parada and not cerca_parada and not en_ruta_consistente:
            penalizaciones.append(('fuera_de_zona', 0.2))

        # --- Combinación Bayesiana ---
        prob_no_subir = 1.0
        for nombre, (p_base, peso) in self.señales.items():
            if nombre in detalle:
                p_eff = detalle[nombre] * peso
                prob_no_subir *= (1 - p_eff)

        score = 1 - prob_no_subir

        # Aplicar penalizaciones multiplicativas
        for nombre, factor in penalizaciones:
            score *= factor
            detalle[f'pen_{nombre}'] = factor

        # Señal explícita anula implícitas (pero respeta penalizaciones)
        if boton_activado:
            score = max(score, 0.95)

        # Umbral de decisión
        if score >= 0.90:
            umbral = 'confirmar_auto'
        elif score >= 0.70:
            umbral = 'notificar_confirmar'
        elif score >= 0.40:
            umbral = 'sospechoso'
        else:
            umbral = 'descartar'

        return {
            'score': round(score, 3),
            'umbral': umbral,
            'detalle': {k: round(v, 3) for k, v in detalle.items()},
            'dist_parada': round(dist_parada, 1),
            'en_parada': en_parada,
            'speed_kmh': round(speed, 1) if speed else None,
            'accuracy_prom': round(accuracy_prom, 1),
            't_espera': t_espera,
            'en_ruta_consistente': en_ruta_consistente
        }

# ============================================================
# 4. SIMULADOR DE ESCENARIOS
# ============================================================

class SimuladorGPS:
    def __init__(self, linea: Linea):
        self.linea = linea
        self.trazado = linea.trazado

    def generar_camino_hacia_parada(self, inicio_lat: float, inicio_lon: float,
                                     parada: Parada, duracion_seg: int = 300) -> List[PosicionGPS]:
        """Usuario camina desde inicio hasta parada"""
        posiciones = []
        n_puntos = max(10, duracion_seg // 30)
        for i in range(n_puntos + 1):
            t = i / n_puntos
            lat = inicio_lat + (parada.lat - inicio_lat) * t
            lon = inicio_lon + (parada.lon - inicio_lon) * t
            # Añadir ruido GPS
            lat += random.uniform(-0.0001, 0.0001)
            lon += random.uniform(-0.0001, 0.0001)
            speed = 1.5 * 3.6  # km/h caminando
            heading = self._calcular_heading(inicio_lat, inicio_lon, parada.lat, parada.lon)
            posiciones.append(PosicionGPS(
                lat=lat, lon=lon,
                timestamp=1000000 + i * 30,
                accuracy=random.uniform(5, 15),
                heading=heading,
                speed=speed / 3.6
            ))
        return posiciones

    def generar_espera_en_parada(self, parada: Parada, duracion_seg: int = 600,
                                  ruido: float = 0.00005) -> List[PosicionGPS]:
        """Usuario espera en la parada (quietud con ruido GPS)"""
        posiciones = []
        n_puntos = max(2, duracion_seg // 30)  # 1 punto cada 30s, mínimo 2
        intervalo = duracion_seg / n_puntos if n_puntos > 0 else 30
        for i in range(n_puntos + 1):
            lat = parada.lat + random.uniform(-ruido, ruido)
            lon = parada.lon + random.uniform(-ruido, ruido)
            posiciones.append(PosicionGPS(
                lat=lat, lon=lon,
                timestamp=2000000 + int(i * intervalo),
                accuracy=random.uniform(5, 20),
                heading=None,
                speed=0.0
            ))
        return posiciones

    def generar_subida_y_viaje(self, parada: Parada, duracion_seg: int = 1200) -> List[PosicionGPS]:
        """Usuario se sube y sigue el trazado de la guagua"""
        # Encontrar punto más cercano en trazado
        idx_inicio = min(range(len(self.trazado)),
                        key=lambda i: haversine(parada.lat, parada.lon,
                                                self.trazado[i][0], self.trazado[i][1]))
        posiciones = []
        velocidad = self.linea.velocidad_kmh / 3.6  # m/s
        puntos_por_seg = velocidad / 10  # aproximado
        n_puntos = int(duracion_seg * puntos_por_seg)
        for i in range(min(n_puntos, len(self.trazado) - idx_inicio)):
            idx = idx_inicio + i
            if idx >= len(self.trazado):
                break
            lat, lon = self.trazado[idx]
            # Ruido GPS en movimiento
            lat += random.uniform(-0.0002, 0.0002)
            lon += random.uniform(-0.0002, 0.0002)
            heading = self._calcular_heading_segmento(idx) if idx < len(self.trazado) - 1 else None
            posiciones.append(PosicionGPS(
                lat=lat, lon=lon,
                timestamp=3000000 + i * 10,
                accuracy=random.uniform(10, 30),
                heading=heading,
                speed=velocidad
            ))
        return posiciones

    def generar_falso_positivo_callejón(self, parada: Parada, duracion_seg: int = 300) -> List[PosicionGPS]:
        """Usuario cerca de parada pero en callejón (GPS impreciso)"""
        posiciones = []
        # Punto a 80m de la parada pero no en la ruta
        offset_lat = 0.0007
        offset_lon = 0.0007
        for i in range(duracion_seg // 30):
            lat = parada.lat + offset_lat + random.uniform(-0.0003, 0.0003)
            lon = parada.lon + offset_lon + random.uniform(-0.0003, 0.0003)
            posiciones.append(PosicionGPS(
                lat=lat, lon=lon,
                timestamp=4000000 + i * 30,
                accuracy=random.uniform(30, 60),  # GPS malo
                heading=None,
                speed=0.5
            ))
        return posiciones

    def _calcular_heading(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        dlon = math.radians(lon2 - lon1)
        lat1, lat2 = math.radians(lat1), math.radians(lat2)
        y = math.sin(dlon) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
        return (math.degrees(math.atan2(y, x)) + 360) % 360

    def _calcular_heading_segmento(self, idx: int) -> float:
        if idx >= len(self.trazado) - 1:
            return 0
        lat1, lon1 = self.trazado[idx]
        lat2, lon2 = self.trazado[idx + 1]
        return self._calcular_heading(lat1, lon1, lat2, lon2)

# ============================================================
# 5. CARGA DE DATOS REALES (Línea 01)
# ============================================================

def cargar_linea_01() -> Linea:
    with open('datos/paradas_reales.json', 'r') as f:
        data = json.load(f)
    l01 = data['01']
    paradas = []
    for i, p in enumerate(l01['paradas']):
        paradas.append(Parada(
            id=f"01:{i}",
            nombre=p['n'],
            lat=p['lat'],
            lon=p['lon'],
            k=p['k']
        ))
    # Trazado: aplanar si está nested
    trazado = l01['trazado']
    if trazado and isinstance(trazado[0][0], list):
        trazado = trazado[0]
    return Linea(
        numero='01',
        nombre='Puerto del Rosario – Morro Jable',
        trazado=trazado,
        paradas=paradas,
        velocidad_kmh=30.0
    )

# ============================================================
# 6. GENERADOR DE ESCENARIOS DE PRUEBA
# ============================================================

def generar_escenarios(linea: Linea, simulador: SimuladorGPS) -> List[Escenario]:
    escenarios = []
    parada = linea.paradas[10]  # Parada intermedia típica

    # ESCENARIOS POSITIVOS (subida_real=True)

    # 1. Usuario camina -> espera -> se sube (momento decisión: EN PARADA tras espera)
    u = Usuario(id='pos_01', gps_activo=True)
    # Solo camino + espera (sin viaje), la decisión es al llegar el bus
    u.posiciones = (simulador.generar_camino_hacia_parada(parada.lat + 0.002, parada.lon + 0.002, parada, 300) +
                    simulador.generar_espera_en_parada(parada, 300))
    u.consulto_app_recientemente = True
    u.ultima_consulta_app = u.posiciones[-1].timestamp - 100
    escenarios.append(Escenario('Caminar + esperar + subir', u, linea, parada, True,
                                 'Usuario típico: camina a parada, espera, bus llega'))

    # 2. Solo espera en parada + consultó app
    u = Usuario(id='pos_02', gps_activo=True)
    u.posiciones = simulador.generar_espera_en_parada(parada, 600)
    u.consulto_app_recientemente = True
    u.ultima_consulta_app = u.posiciones[-1].timestamp - 50
    escenarios.append(Escenario('Espera + app', u, linea, parada, True,
                                 'Ya en parada, consultó app'))

    # 3. Solo espera en parada SIN app
    u = Usuario(id='pos_03', gps_activo=True)
    u.posiciones = simulador.generar_espera_en_parada(parada, 600)
    u.consulto_app_recientemente = False
    escenarios.append(Escenario('Espera sin app', u, linea, parada, True,
                                 'En parada pero no miró app'))

    # 4. Camina hacia parada + app
    u = Usuario(id='pos_04', gps_activo=True)
    u.posiciones = simulador.generar_camino_hacia_parada(parada.lat + 0.003, parada.lon + 0.003, parada, 300)
    u.consulto_app_recientemente = True
    u.ultima_consulta_app = u.posiciones[-1].timestamp - 50
    escenarios.append(Escenario('Camina hacia parada + app', u, linea, parada, True,
                                 'Llegando a parada, miró app'))

    # 5. Botón explícito
    u = Usuario(id='pos_05', gps_activo=True)
    u.posiciones = simulador.generar_espera_en_parada(parada, 120)
    u.boton_activado = True
    escenarios.append(Escenario('Botón explícito', u, linea, parada, True,
                                 'Tocó "Me voy a subir" en parada'))

    # 6. Con otros usuarios (social)
    u = Usuario(id='pos_06', gps_activo=True)
    u.posiciones = simulador.generar_espera_en_parada(parada, 300)
    otros = [Usuario(id=f'otro_{i}', gps_activo=True,
                     posiciones=simulador.generar_espera_en_parada(parada, 300))
             for i in range(3)]
    esc = Escenario('Social: 3 usuarios en parada', u, linea, parada, True,
                     'Multitud en parada')
    esc.otros = otros
    escenarios.append(esc)

    # 7. GPS en trazado moviéndose lento
    u = Usuario(id='pos_07', gps_activo=True)
    u.posiciones = simulador.generar_subida_y_viaje(parada, 300)[:20]  # Primeros 20 puntos
    escenarios.append(Escenario('En trazado velocidad bus', u, linea, parada, True,
                                 'Ya en bus, moviéndose a velocidad de guagua'))

    # ESCENARIOS NEGATIVOS (subida_real=False)

    # 8. Falso positivo: callejón cerca de parada
    u = Usuario(id='neg_01', gps_activo=True)
    u.posiciones = simulador.generar_falso_positivo_callejón(parada, 300)
    u.consulto_app_recientemente = True
    escenarios.append(Escenario('Falso positivo: callejón', u, linea, parada, False,
                                 'Cerca de parada pero en callejón, GPS impreciso'))

    # 9. Usuario camina por la calle, no va a parada
    u = Usuario(id='neg_02', gps_activo=True)
    u.posiciones = simulador.generar_camino_hacia_parada(parada.lat + 0.01, parada.lon + 0.01,
                                                          Parada('x', 'lejos', parada.lat + 0.01, parada.lon + 0.01, 0), 300)
    u.consulto_app_recientemente = False
    escenarios.append(Escenario('Caminando lejos sin app', u, linea, parada, False,
                                 'Camina en dirección opuesta, no miró app'))

    # 10. En parada pero GPS ruido, sin app, poco tiempo
    u = Usuario(id='neg_03', gps_activo=True)
    u.posiciones = simulador.generar_espera_en_parada(parada, 60)
    u.consulto_app_recientemente = False
    escenarios.append(Escenario('Parada poco tiempo sin app', u, linea, parada, False,
                                 'Parada 1 min, no miró app'))

    # 11. GPS desactivado
    u = Usuario(id='neg_04', gps_activo=False)
    u.posiciones = []
    escenarios.append(Escenario('GPS desactivado', u, linea, parada, False,
                                 'Usuario sin GPS'))

    # 12. Dos líneas misma parada (ambigüedad)
    u = Usuario(id='neg_05', gps_activo=True)
    u.posiciones = simulador.generar_espera_en_parada(parada, 300)
    u.consulto_app_recientemente = True
    u.ambigueza_lineas = True  # Flag para penalizar
    # Simular otra línea pasando por ahí
    escenarios.append(Escenario('Ambigüedad 2 líneas', u, linea, parada, False,
                                 'Dos líneas pasan por misma parada'))

    # 13. Bus cancelado (espera 20 min)
    u = Usuario(id='neg_06', gps_activo=True)
    u.posiciones = simulador.generar_espera_en_parada(parada, 1200)
    u.consulto_app_recientemente = True
    escenarios.append(Escenario('Bus cancelado: espera 20 min', u, linea, parada, False,
                                 'Espera mucho, bus no llega'))

    # 14. Usuario en bus pero GPS salta (interior)
    u = Usuario(id='neg_07', gps_activo=True)
    pos_bus = simulador.generar_subida_y_viaje(parada, 600)
    # Añadir saltos GPS típicos en interior
    for p in pos_bus:
        p.accuracy = random.uniform(40, 80)
        if random.random() < 0.2:
            p.lat += random.uniform(-0.001, 0.001)
            p.lon += random.uniform(-0.001, 0.001)
    u.posiciones = pos_bus
    escenarios.append(Escenario('GPS interior bus con saltos', u, linea, parada, True,
                                 'En bus pero GPS salta mucho'))

    # 15. Variante de línea (pregunta al usuario)
    u = Usuario(id='pos_08', gps_activo=True)
    u.posiciones = simulador.generar_espera_en_parada(parada, 300)
    u.consulto_app_recientemente = True
    u.variante_posible = True
    escenarios.append(Escenario('Parada con variante', u, linea, parada, True,
                                 'Línea tiene variante Triquivijate/Las Playitas'))

    return escenarios

# ============================================================
# 7. EJECUCIÓN Y MÉTRICAS
# ============================================================

def evaluar_escenario(motor: MotorSenales, esc: Escenario, otros: List[Usuario] = None) -> Dict:
    if hasattr(esc, 'otros') and esc.otros:
        otros = esc.otros
    resultado = motor.evaluar(esc, otros)
    predicho = resultado['score'] >= 0.70  # umbral notificar/confirmar
    return {
        'escenario': esc.nombre,
        'subida_real': esc.subida_real,
        'score': resultado['score'],
        'umbral': resultado['umbral'],
        'predicho': predicho,
        'correcto': predicho == esc.subida_real,
        'detalle': resultado['detalle'],
        'dist_parada': resultado.get('dist_parada'),
        'en_parada': resultado.get('en_parada'),
        'speed_kmh': resultado.get('speed_kmh'),
        'accuracy_prom': resultado.get('accuracy_prom'),
        't_espera': resultado.get('t_espera'),
        'en_ruta_consistente': resultado.get('en_ruta_consistente')
    }

def main():
    print("=" * 70)
    print("PROTOTIPO DETECCION DE SUBIDA - GUAGUAS FTV")
    print("=" * 70)

    # Cargar datos
    print("\n[1/4] Cargando linea 01...")
    linea = cargar_linea_01()
    print(f"    Paradas: {len(linea.paradas)}")
    print(f"    Trazado: {len(linea.trazado)} puntos")
    print(f"    Velocidad: {linea.velocidad_kmh} km/h")

    # Simulador
    print("\n[2/4] Inicializando simulador GPS...")
    simulador = SimuladorGPS(linea)

    # Generar escenarios
    print("\n[3/4] Generando escenarios de prueba...")
    escenarios = generar_escenarios(linea, simulador)
    print(f"    Escenarios: {len(escenarios)}")

    # Motor
    print("\n[4/4] Evaluando con motor de 12 senales...")
    motor = MotorSenales()
    resultados = []
    for esc in escenarios:
        otros = getattr(esc, 'otros', None)
        res = evaluar_escenario(motor, esc, otros)
        resultados.append(res)
        status = "OK" if res['correcto'] else "FAIL"
        print(f"    {status} {esc.nombre:40s} score={res['score']:.3f} umbral={res['umbral']} real={esc.subida_real}")
        
        # DEBUG para casos fallados
        if not res['correcto']:
            print(f"         DEBUG: dist_parada={res.get('dist_parada')} en_parada={res.get('en_parada')} speed={res.get('speed_kmh')} accuracy={res.get('accuracy_prom')} t_espera={res.get('t_espera')} en_ruta={res.get('en_ruta_consistente')}")
            print(f"         DETALLE: {res['detalle']}")

    # Métricas
    print("\n" + "=" * 70)
    print("MÉTRICAS")
    print("=" * 70)

    tp = sum(1 for r in resultados if r['subida_real'] and r['predicho'])
    tn = sum(1 for r in resultados if not r['subida_real'] and not r['predicho'])
    fp = sum(1 for r in resultados if not r['subida_real'] and r['predicho'])
    fn = sum(1 for r in resultados if r['subida_real'] and not r['predicho'])

    total = len(resultados)
    accuracy = (tp + tn) / total
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    print(f"Total:          {total}")
    print(f"Verdaderos +:   {tp}")
    print(f"Verdaderos -:   {tn}")
    print(f"Falsos +:       {fp}")
    print(f"Falsos -:       {fn}")
    print(f"Accuracy:       {accuracy:.2%}")
    print(f"Precision:      {precision:.2%}")
    print(f"Recall:         {recall:.2%}")
    print(f"F1 Score:       {f1:.2%}")

    # Por umbral
    print("\nDistribución por umbral:")
    for umbral in ['confirmar_auto', 'notificar_confirmar', 'sospechoso', 'descartar']:
        n = sum(1 for r in resultados if r['umbral'] == umbral)
        print(f"  {umbral:25s}: {n}")

    # Casos fallados
    print("\nCasos FALLADOS:")
    for r in resultados:
        if not r['correcto']:
            print(f"  FAIL {r['escenario']}: score={r['score']:.3f} predicho={r['predicho']} real={r['subida_real']}")

    # Objetivo
    print(f"\n{'='*70}")
    if accuracy >= 0.85:
        print(f"OK OBJETIVO CUMPLIDO: Accuracy {accuracy:.2%} >= 85%")
    else:
        print(f"FAIL OBJETIVO NO CUMPLIDO: Accuracy {accuracy:.2%} < 85%")
        print("   Revisar senales y umbrales")

    return accuracy >= 0.85

if __name__ == '__main__':
    main()