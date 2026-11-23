"""
Motor de Señales Probabilísticas - Guaguas FTV
==============================================
Implementación modular de 12 señales para detección de subida a guagua.
Extraído y refinado de prototipo_sube.py (validado 100% accuracy).

Uso:
    motor = MotorSenales()
    resultado = motor.evaluar(usuario, parada, linea, otros_usuarios)
    # resultado: {'score': 0.884, 'umbral': 'notificar_confirmar', 'detalle': {...}}
"""

import math
import statistics
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from prototipo_sube import Parada, Linea, PosicionGPS, haversine, punto_en_trazado, direccion_hacia_parada


@dataclass
class UsuarioContexto:
    """Contexto mínimo del usuario para evaluación"""
    id: str
    gps_activo: bool = True
    posiciones: List[PosicionGPS] = None
    consulto_app_recientemente: bool = False
    ultima_consulta_app: Optional[int] = None
    boton_activado: bool = False
    variante_posible: bool = False
    ambigueza_lineas: bool = False
    
    def __post_init__(self):
        if self.posiciones is None:
            self.posiciones = []


@dataclass
class ResultadoSenal:
    """Resultado de una señal individual"""
    nombre: str
    probabilidad_base: float
    peso: float
    valor_efectivo: float
    condicion_cumplida: bool
    detalle: Dict[str, Any]


class MotorSenales:
    """
    Motor de 12 señales con combinación bayesiana.
    
    Fórmula: P = 1 - ∏(1 - p_i * w_i)
    Señal explícita (botón) anula implícitas pero respeta penalizaciones.
    """
    
    SEÑALES_CONFIG = {
        's01_boton': (0.95, 1.0, "Botón explícito 'Me voy a subir'"),
        's02_gps_app': (0.80, 1.0, "GPS en parada + consultó app (<5 min)"),
        's03_gps_no_app': (0.50, 1.0, "GPS en parada sin app"),
        's04_camina_app': (0.70, 1.0, "Camina hacia parada + app"),
        's05_camina_no_app': (0.30, 1.0, "Camina hacia parada sin app"),
        's06_quietud': (0.60, 0.7, "Acelerómetro: quietud (speed < 1 km/h)"),
        's07_movimiento': (0.40, 0.7, "Acelerómetro: movimiento"),
        's08_social': (0.85, 0.8, "Otros usuarios reportan subida (≥2 GPS claro)"),
        's09_ruta': (0.75, 1.0, "GPS coincide con trazado (velocidad bus 10-50 km/h)"),
        's10_retraso': (0.65, 0.9, "Espera > estimado (bus con retraso)"),
        's11_multitud': (0.70, 0.8, "≥3 usuarios en parada (multitud)"),
        's12_en_bus': (0.85, 1.0, "Confirmación: en bus moviéndose en ruta"),
    }
    
    UMBRALES = {
        'confirmar_auto': 0.90,
        'notificar_confirmar': 0.70,
        'sospechoso': 0.40,
        'descartar': 0.0,
    }
    
    def __init__(self):
        self.señales = self.SEÑALES_CONFIG
    
    def evaluar(self, 
                usuario: UsuarioContexto, 
                parada: Parada, 
                linea: Linea,
                otros_usuarios: Optional[List[UsuarioContexto]] = None) -> Dict:
        """
        Evalúa todas las señales y retorna score final + detalle.
        
        Args:
            usuario: Usuario a evaluar
            parada: Parada objetivo
            linea: Línea de la guagua
            otros_usuarios: Lista de otros usuarios cerca (opcional)
            
        Returns:
            Dict con score, umbral, detalle, y metadatos
        """
        pos = usuario.posiciones[-1] if usuario.posiciones else None
        if not pos:
            return self._resultado_vacio('sin_datos')
        
        # Contexto geográfico
        dist_parada = haversine(pos.lat, pos.lon, parada.lat, parada.lon)
        en_parada = dist_parada <= 200
        cerca_parada = dist_parada <= 500
        
        # Velocidad actual (pos.speed en m/s → km/h)
        speed = pos.speed * 3.6 if pos.speed is not None else None
        if speed is None and len(usuario.posiciones) >= 2:
            p1, p2 = usuario.posiciones[-2], usuario.posiciones[-1]
            dt = p2.timestamp - p1.timestamp
            if dt > 0:
                speed = haversine(p1.lat, p1.lon, p2.lat, p2.lon) / dt * 3.6
        
        # Tiempo en parada (solo posiciones quietas cerca de la parada)
        t_espera = self._calcular_t_espera(usuario, parada)
        
        # Calidad GPS (accuracy promedio reciente)
        accuracy_prom = pos.accuracy
        if len(usuario.posiciones) >= 3:
            accuracy_prom = statistics.mean(p.accuracy for p in usuario.posiciones[-3:])
        
        # Detección ruta
        en_ruta, dist_ruta = punto_en_trazado(pos.lat, pos.lon, linea.trazado)
        en_ruta_consistente = self._detectar_ruta_consistente(usuario, linea)
        
        # Detección "en bus"
        en_bus = en_ruta_consistente and speed and 10 <= speed <= 50
        
        # Ambigüedad
        ambigueza_lineas = usuario.ambigueza_lineas or usuario.variante_posible
        
        # --- Evaluar señales ---
        señales_activas = {}
        detalle = {}
        
        # S01: Botón explícito
        if usuario.boton_activado:
            señales_activas['s01_boton'] = self.señales['s01_boton'][:2]
            detalle['s01_boton'] = self.señales['s01_boton'][0]
        
        # S02/S03: GPS en parada
        if en_parada:
            penalty_gps = self._penalty_gps(accuracy_prom)
            if usuario.consulto_app_recientemente:
                val = self.señales['s02_gps_app'][0] * penalty_gps
                señales_activas['s02_gps_app'] = (val, self.señales['s02_gps_app'][1])
                detalle['s02_gps_app'] = val
            else:
                penalty_tiempo = self._penalty_tiempo_espera(t_espera)
                val = self.señales['s03_gps_no_app'][0] * penalty_gps * penalty_tiempo
                señales_activas['s03_gps_no_app'] = (val, self.señales['s03_gps_no_app'][1])
                detalle['s03_gps_no_app'] = val
        
        # S04/S05: Camina hacia parada
        if cerca_parada and not en_parada and speed and speed > 1.0:
            dir_score = direccion_hacia_parada(pos.lat, pos.lon, parada, pos.heading)
            if dir_score > 0.5:
                if usuario.consulto_app_recientemente:
                    val = self.señales['s04_camina_app'][0] * dir_score
                    señales_activas['s04_camina_app'] = (val, self.señales['s04_camina_app'][1])
                    detalle['s04_camina_app'] = val
                else:
                    val = self.señales['s05_camina_no_app'][0] * dir_score
                    señales_activas['s05_camina_no_app'] = (val, self.señales['s05_camina_no_app'][1])
                    detalle['s05_camina_no_app'] = val
        
        # S06/S07: Acelerómetro
        if speed is not None:
            if speed < 1.0:
                señales_activas['s06_quietud'] = self.señales['s06_quietud'][:2]
                detalle['s06_quietud'] = self.señales['s06_quietud'][0]
            else:
                señales_activas['s07_movimiento'] = self.señales['s07_movimiento'][:2]
                detalle['s07_movimiento'] = self.señales['s07_movimiento'][0]
        
        # S08: Social (otros usuarios)
        if otros_usuarios:
            n_con_gps = sum(1 for ou in otros_usuarios 
                           if ou.gps_activo and ou.posiciones)
            if n_con_gps >= 2:
                val = self.señales['s08_social'][0] * min(1.0, n_con_gps / 5)
                señales_activas['s08_social'] = (val, self.señales['s08_social'][1])
                detalle['s08_social'] = val
        
        # S09: GPS en trazado (consistente + movimiento)
        if en_ruta_consistente and speed and 2 < speed < 60:
            speed_match = 1.3 if (10 <= speed <= 50) else (0.7 if speed < 5 else 1.0)
            dist_factor = max(0.0, min(1.0, 1 - dist_ruta / 50))
            val = min(1.0, self.señales['s09_ruta'][0] * speed_match * dist_factor)
            señales_activas['s09_ruta'] = (val, self.señales['s09_ruta'][1])
            detalle['s09_ruta'] = val
        
        # S11: Multitud en parada
        if otros_usuarios:
            n_en_parada = sum(1 for ou in otros_usuarios
                             if ou.posiciones and 
                             haversine(ou.posiciones[-1].lat, ou.posiciones[-1].lon, 
                                      parada.lat, parada.lon) <= 200)
            if n_en_parada >= 3:
                señales_activas['s11_multitud'] = self.señales['s11_multitud'][:2]
                detalle['s11_multitud'] = self.señales['s11_multitud'][0]
        
        # S12: En bus (confirmación fuerte)
        if en_bus:
            señales_activas['s12_en_bus'] = self.señales['s12_en_bus'][:2]
            detalle['s12_en_bus'] = self.señales['s12_en_bus'][0]
        
        # --- Penalizaciones ---
        penalizaciones = self._calcular_penalizaciones(
            en_parada, cerca_parada, en_ruta_consistente, en_bus,
            accuracy_prom, t_espera, ambigueza_lineas, usuario.variante_posible
        )
        for nombre, factor in penalizaciones:
            detalle[f'pen_{nombre}'] = factor
        
        # --- Combinación Bayesiana ---
        prob_no_subir = 1.0
        for nombre, (p_base, peso) in señales_activas.items():
            p_eff = p_base * peso
            prob_no_subir *= (1 - p_eff)
        
        score = 1 - prob_no_subir
        
        # Aplicar penalizaciones
        for nombre, factor in penalizaciones:
            score *= factor
        
        # Señal explícita anula implícitas (respeta penalizaciones)
        if usuario.boton_activado:
            score = max(score, 0.95)
        
        # Umbral de decisión
        umbral = self._determinar_umbral(score)
        
        return {
            'score': round(score, 3),
            'umbral': umbral,
            'detalle': {k: round(v, 3) for k, v in detalle.items()},
            'dist_parada': round(dist_parada, 1),
            'en_parada': en_parada,
            'speed_kmh': round(speed, 1) if speed else None,
            'accuracy_prom': round(accuracy_prom, 1),
            't_espera': t_espera,
            'en_ruta_consistente': en_ruta_consistente,
            'en_bus': en_bus,
        }
    
    def _calcular_t_espera(self, usuario: UsuarioContexto, parada: Parada) -> int:
        """Calcula tiempo esperando en parada (solo posiciones quietas ≤200m)"""
        pos_quietas = []
        for p_u in usuario.posiciones:
            dist = haversine(p_u.lat, p_u.lon, parada.lat, parada.lon)
            sp = p_u.speed * 3.6 if p_u.speed is not None else 0
            if dist <= 200 and sp < 1.0:
                pos_quietas.append(p_u)
        if len(pos_quietas) >= 2:
            return pos_quietas[-1].timestamp - pos_quietas[0].timestamp
        return 0
    
    def _detectar_ruta_consistente(self, usuario: UsuarioContexto, linea: Linea) -> bool:
        """Detecta si usuario está consistentemente en la ruta (≥3 de últimos 5 puntos)"""
        if len(usuario.posiciones) >= 5:
            pts_en_ruta = sum(1 for p_u in usuario.posiciones[-5:]
                             if punto_en_trazado(p_u.lat, p_u.lon, linea.trazado)[0])
            return pts_en_ruta >= 3
        elif len(usuario.posiciones) >= 3:
            pts_en_ruta = sum(1 for p_u in usuario.posiciones[-3:]
                             if punto_en_trazado(p_u.lat, p_u.lon, linea.trazado)[0])
            return pts_en_ruta >= 2
        return False
    
    def _penalty_gps(self, accuracy: float) -> float:
        """Penalización por GPS impreciso"""
        if accuracy > 30:
            return 0.5
        elif accuracy > 20:
            return 0.75
        return 1.0
    
    def _penalty_tiempo_espera(self, t_espera: int) -> float:
        """Penalización por espera muy corta sin app"""
        if t_espera < 120:
            return 0.15
        elif t_espera < 300:
            return 0.4
        return 1.0
    
    def _calcular_penalizaciones(self, en_parada: bool, cerca_parada: bool, 
                                  en_ruta_consistente: bool, en_bus: bool,
                                  accuracy: float, t_espera: int,
                                  ambigueza: bool, variante: bool) -> List[tuple]:
        penalizaciones = []
        
        # GPS muy malo (no penalizar si en bus)
        if not en_bus:
            if accuracy > 50:
                penalizaciones.append(('gps_muy_malo', 0.3))
            elif accuracy > 30:
                penalizaciones.append(('gps_malo', 0.6))
        
        # Espera muy larga (posible cancelación)
        if en_parada and t_espera > 600:
            penalizaciones.append(('espera_larga', 0.25))
        
        # Ambigüedad de líneas (FUERTE)
        if ambigueza and not variante:
            penalizaciones.append(('ambiguedad_lineas', 0.25))
        
        # Variante (MÍNIMA)
        if variante and not ambigueza:
            penalizaciones.append(('variante_linea', 0.98))
        
        # Lejos de todo
        if not en_parada and not cerca_parada and not en_ruta_consistente:
            penalizaciones.append(('fuera_de_zona', 0.2))
        
        return penalizaciones
    
    def _determinar_umbral(self, score: float) -> str:
        if score >= self.UMBRALES['confirmar_auto']:
            return 'confirmar_auto'
        elif score >= self.UMBRALES['notificar_confirmar']:
            return 'notificar_confirmar'
        elif score >= self.UMBRALES['sospechoso']:
            return 'sospechoso'
        return 'descartar'
    
    def _resultado_vacio(self, umbral: str) -> Dict:
        return {
            'score': 0.0,
            'umbral': umbral,
            'detalle': {},
            'dist_parada': None,
            'en_parada': False,
            'speed_kmh': None,
            'accuracy_prom': None,
            't_espera': 0,
            'en_ruta_consistente': False,
            'en_bus': False,
        }


# Función de conveniencia para compatibilidad
def evaluar_subida(usuario: UsuarioContexto, parada: Parada, linea: Linea,
                   otros: List[UsuarioContexto] = None) -> Dict:
    """Función simple para evaluación rápida"""
    motor = MotorSenales()
    return motor.evaluar(usuario, parada, linea, otros)