"""
Catálogo de 14 Edge Cases - Guaguas FTV
=======================================
Manejo robusto de situaciones límite en detección de subida y ETA.
Cada caso incluye: detección, mitigación y respuesta al usuario.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Callable
from enum import Enum
import time

from prototipo_sube import Parada, Linea, PosicionGPS, haversine, punto_en_trazado
from motor_senales import UsuarioContexto, MotorSenales
from boton_subir import EstadoBoton, IntencionSubida, ValidadorBoton, MonitorIntencion
from inferencia_colectiva import MotorInferenciaColectiva, EstadoColectivo


class TipoEdgeCase(Enum):
    FALSO_POSITIVO_BOTON = "falso_positivo_boton"
    FALSO_NEGATIVO_SUBIDA = "falso_negativo_subida"
    MULTIPLES_LINEAS_MISMA_PARADA = "multiples_lineas_misma_parada"
    BUS_CANCELADO = "bus_cancelado"
    BUS_LLENO = "bus_lleno"
    GPS_IMPRECISO_CALLEJON = "gps_impreciso_callejon"
    PARADA_IDA_VUELTA_MISMO_LUGAR = "parada_ida_vuelta_mismo_lugar"
    SUBE_BAJA_SIGUIENTE_PARADA = "sube_baja_siguiente_parada"
    DOS_LINEAS_MISMA_HORA = "dos_lineas_misma_hora"
    GPS_INTERIOR_BUS = "gps_interior_bus"
    BATTERY_SAVER_GPS = "battery_saver_gps"
    TURISTA_SIN_SABER_LINEA = "turista_sin_saber_linea"
    VARIANTE_LINEA = "variante_linea"
    FESTIVOS_HORARIO_REDUCIDO = "festivos_horario_reducido"


@dataclass
class EdgeCaseResultado:
    """Resultado de evaluación de edge case"""
    tipo: TipoEdgeCase
    detectado: bool
    confianza: float  # 0.0 - 1.0
    accion: str  # 'ignorar', 'notificar', 'preguntar', 'ajustar_score', 'escalar'
    mensaje_usuario: Optional[str] = None
    metadatos: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadatos is None:
            self.metadatos = {}


class ManejadorEdgeCases:
    """
    Manejador centralizado de edge cases.
    Se invoca tras motor de señales para ajustar/validar decisiones.
    """
    
    def __init__(self, motor_senales: MotorSenales = None):
        self.motor = motor_senales or MotorSenales()
        self.handlers: Dict[TipoEdgeCase, Callable] = {
            TipoEdgeCase.FALSO_POSITIVO_BOTON: self._handle_falso_positivo_boton,
            TipoEdgeCase.FALSO_NEGATIVO_SUBIDA: self._handle_falso_negativo_subida,
            TipoEdgeCase.MULTIPLES_LINEAS_MISMA_PARADA: self._handle_multiples_lineas,
            TipoEdgeCase.BUS_CANCELADO: self._handle_bus_cancelado,
            TipoEdgeCase.BUS_LLENO: self._handle_bus_lleno,
            TipoEdgeCase.GPS_IMPRECISO_CALLEJON: self._handle_gps_callejon,
            TipoEdgeCase.PARADA_IDA_VUELTA_MISMO_LUGAR: self._handle_parada_ida_vuelta,
            TipoEdgeCase.SUBE_BAJA_SIGUIENTE_PARADA: self._handle_sube_baja_rapido,
            TipoEdgeCase.DOS_LINEAS_MISMA_HORA: self._handle_dos_lineas_misma_hora,
            TipoEdgeCase.GPS_INTERIOR_BUS: self._handle_gps_interior_bus,
            TipoEdgeCase.BATTERY_SAVER_GPS: self._handle_battery_saver,
            TipoEdgeCase.TURISTA_SIN_SABER_LINEA: self._handle_turista,
            TipoEdgeCase.VARIANTE_LINEA: self._handle_variante,
            TipoEdgeCase.FESTIVOS_HORARIO_REDUCIDO: self._handle_festivos,
        }
    
    def evaluar_todos(self,
                      usuario: UsuarioContexto,
                      parada: Parada,
                      linea: Linea,
                      resultado_motor: Dict,
                      intencion: IntencionSubida = None,
                      otras_lineas: List[Linea] = None,
                      ahora: int = None) -> List[EdgeCaseResultado]:
        """Evalúa todos los edge cases relevantes"""
        if ahora is None:
            ahora = int(time.time())
        
        resultados = []
        for tipo, handler in self.handlers.items():
            try:
                res = handler(usuario, parada, linea, resultado_motor, intencion, otras_lineas, ahora)
                if res.detectado:
                    resultados.append(res)
            except Exception as e:
                # Log error but continue
                pass
        return resultados
    
    def aplicar_mitigaciones(self,
                             resultado_motor: Dict,
                             edge_cases: List[EdgeCaseResultado]) -> Dict:
        """Aplica mitigaciones de edge cases al resultado del motor"""
        score_ajustado = resultado_motor['score']
        umbral_original = resultado_motor['umbral']
        acciones = []
        mensajes = []
        
        for ec in edge_cases:
            if ec.accion == 'ajustar_score':
                # Ajustar score según confianza
                if ec.metadatos.get('direccion') == 'reducir':
                    score_ajustado *= (1 - ec.confianza * 0.5)
                elif ec.metadatos.get('direccion') == 'aumentar':
                    score_ajustado = min(1.0, score_ajustado + ec.confianza * 0.3)
            elif ec.accion == 'notificar':
                acciones.append(f"notificar_{ec.tipo.value}")
                if ec.mensaje_usuario:
                    mensajes.append(ec.mensaje_usuario)
            elif ec.accion == 'preguntar':
                acciones.append(f"preguntar_{ec.tipo.value}")
                if ec.mensaje_usuario:
                    mensajes.append(ec.mensaje_usuario)
            elif ec.accion == 'escalar':
                acciones.append(f"escalar_{ec.tipo.value}")
        
        # Recalcular umbral
        umbral_nuevo = self._determinar_umbral(score_ajustado)
        
        return {
            **resultado_motor,
            'score': round(score_ajustado, 3),
            'umbral': umbral_nuevo,
            'umbral_original': umbral_original,
            'edge_cases_aplicados': [ec.tipo.value for ec in edge_cases],
            'acciones_requeridas': acciones,
            'mensajes_usuario': mensajes,
        }
    
    def _determinar_umbral(self, score: float) -> str:
        if score >= 0.90:
            return 'confirmar_auto'
        elif score >= 0.70:
            return 'notificar_confirmar'
        elif score >= 0.40:
            return 'sospechoso'
        return 'descartar'
    
    # === HANDLERS INDIVIDUALES ===
    
    def _handle_falso_positivo_boton(self, usuario, parada, linea, motor_res, intencion, otras, ahora):
        """Usuario pulsó botón pero NO se subió (se alejó, canceló, bus pasó)"""
        if not intencion or intencion.estado not in (EstadoBoton.PULSADO, EstadoBoton.MONITOREANDO):
            return EdgeCaseResultado(TipoEdgeCase.FALSO_POSITIVO_BOTON, False, 0, 'ignorar')
        
        # Verificar alejamiento >100m desde pulsación
        if usuario.posiciones and intencion.gps_creacion:
            pos = usuario.posiciones[-1]
            dist = haversine(pos.lat, pos.lon, 
                           intencion.gps_creacion.lat, intencion.gps_creacion.lon)
            if dist > 100:
                return EdgeCaseResultado(
                    TipoEdgeCase.FALSO_POSITIVO_BOTON, True, 0.9,
                    'ajustar_score',
                    "Te alejaste de la parada. ¿Te equivocaste al pulsar?",
                    {'direccion': 'reducir', 'dist_alejamiento': dist}
                )
        
        # Verificar timeout 2 min sin movimiento
        if intencion.duracion_monitoreo(ahora) > 120:
            if usuario.posiciones:
                speed = usuario.posiciones[-1].speed * 3.6 if usuario.posiciones[-1].speed else 0
                if speed < 1.0:
                    return EdgeCaseResultado(
                        TipoEdgeCase.FALSO_POSITIVO_BOTON, True, 0.7,
                        'preguntar',
                        "Llevas 2 min en la parada. ¿Sigues esperando el bus?",
                        {'direccion': 'reducir', 'tiempo_espera': intencion.duracion_monitoreo(ahora)}
                    )
        
        return EdgeCaseResultado(TipoEdgeCase.FALSO_POSITIVO_BOTON, False, 0, 'ignorar')
    
    def _handle_falso_negativo_subida(self, usuario, parada, linea, motor_res, intencion, otras, ahora):
        """Usuario SÍ se subió pero motor no lo detecta (GPS malo, inicio en ruta)"""
        if motor_res['score'] < 0.4 and motor_res.get('en_ruta_consistente'):
            # En ruta consistente pero score bajo
            if motor_res.get('speed_kmh', 0) > 10:
                return EdgeCaseResultado(
                    TipoEdgeCase.FALSO_NEGATIVO_SUBIDA, True, 0.8,
                    'ajustar_score',
                    None,
                    {'direccion': 'aumentar', 'motivo': 'en_ruta_velocidad_bus'}
                )
        
        # Inferencia colectiva: otros confirman subida
        if otras:
            subidos_otros = sum(1 for u in otras if u.posiciones and 
                               haversine(u.posiciones[-1].lat, u.posiciones[-1].lon, 
                                        parada.lat, parada.lon) > 200)
            if subidos_otros >= 2:
                return EdgeCaseResultado(
                    TipoEdgeCase.FALSO_NEGATIVO_SUBIDA, True, 0.7,
                    'ajustar_score',
                    None,
                    {'direccion': 'aumentar', 'motivo': 'inferencia_colectiva'}
                )
        
        return EdgeCaseResultado(TipoEdgeCase.FALSO_NEGATIVO_SUBIDA, False, 0, 'ignorar')
    
    def _handle_multiples_lineas(self, usuario, parada, linea, motor_res, intencion, otras, ahora):
        """Múltiples líneas pasan por misma parada - ambigüedad"""
        if not otras:
            return EdgeCaseResultado(TipoEdgeCase.MULTIPLES_LINEAS_MISMA_PARADA, False, 0, 'ignorar')
        
        lineas_en_parada = [l for l in otras if any(p.id == parada.id for p in l.paradas)]
        if len(lineas_en_parada) <= 1:
            return EdgeCaseResultado(TipoEdgeCase.MULTIPLES_LINEAS_MISMA_PARADA, False, 0, 'ignorar')
        
        # Usuario tiene GPS - usar dirección/velocidad para desambiguar
        if usuario.posiciones:
            pos = usuario.posiciones[-1]
            speed = pos.speed * 3.6 if pos.speed else 0
            
            # Verificar trazado de cada línea
            compatibilidad = {}
            for l in lineas_en_parada:
                en_ruta, dist = punto_en_trazado(pos.lat, pos.lon, l.trazado)
                if en_ruta and dist < 50:
                    compatibilidad[l.num] = 1.0 / max(1, dist)
            
            if len(compatibilidad) == 1:
                # Solo una línea compatible
                linea_correcta = list(compatibilidad.keys())[0]
                if linea_correcta != linea.num:
                    return EdgeCaseResultado(
                        TipoEdgeCase.MULTIPLES_LINEAS_MISMA_PARADA, True, 0.9,
                        'notificar',
                        f"Detectamos que vas hacia la línea {linea_correcta}. ¿Es correcto?",
                        {'linea_sugerida': linea_correcta, 'accion': 'cambiar_linea'}
                    )
            elif len(compatibilidad) > 1:
                return EdgeCaseResultado(
                    TipoEdgeCase.MULTIPLES_LINEAS_MISMA_PARADA, True, 0.6,
                    'preguntar',
                    f"Hay {len(lineas_en_parada)} líneas en esta parada. ¿Cuál tomas?",
                    {'lineas_posibles': [l.num for l in lineas_en_parada]}
                )
        
        return EdgeCaseResultado(
            TipoEdgeCase.MULTIPLES_LINEAS_MISMA_PARADA, True, 0.4,
            'ajustar_score',
            None,
            {'direccion': 'reducir', 'factor': 0.5, 'motivo': 'ambiguedad_lineas'}
        )
    
    def _handle_bus_cancelado(self, usuario, parada, linea, motor_res, intencion, otras, ahora):
        """Bus cancelado - espera >20 min sin señal"""
        if not usuario.posiciones:
            return EdgeCaseResultado(TipoEdgeCase.BUS_CANCELADO, False, 0, 'ignorar')
        
        # Usuario en parada mucho tiempo
        t_espera = motor_res.get('t_espera', 0)
        if t_espera > 1200:  # 20 min
            # Verificar si otros usuarios también esperan
            otros_esperando = 0
            if otras:
                for u in otras:
                    if u.posiciones:
                        dist = haversine(u.posiciones[-1].lat, u.posiciones[-1].lon,
                                        parada.lat, parada.lon)
                        if dist <= 200:
                            otros_esperando += 1
            
            return EdgeCaseResultado(
                TipoEdgeCase.BUS_CANCELADO, True, 0.8,
                'notificar',
                f"Llevas {t_espera//60} min esperando. ¿El bus no viene? Podría estar cancelado.",
                {'tiempo_espera': t_espera, 'otros_esperando': otros_esperando}
            )
        
        return EdgeCaseResultado(TipoEdgeCase.BUS_CANCELADO, False, 0, 'ignorar')
    
    def _handle_bus_lleno(self, usuario, parada, linea, motor_res, intencion, otras, ahora):
        """Bus lleno - usuario no puede subir"""
        # Detectable solo por reporte de quien SÍ subió
        # Si hay subidas confirmadas y luego usuario en parada reporta "bus lleno"
        # Para implementación completa, necesitaría feedback explícito
        return EdgeCaseResultado(TipoEdgeCase.BUS_LLENO, False, 0, 'ignorar')

    def _handle_gps_callejon(self, usuario, parada, linea, motor_res, intencion, otras, ahora):
        """GPS impreciso en callejón cerca de parada (falso positivo)"""
        if not usuario.posiciones:
            return EdgeCaseResultado(TipoEdgeCase.GPS_IMPRECISO_CALLEJON, False, 0, 'ignorar')
        
        pos = usuario.posiciones[-1]
        accuracy = pos.accuracy
        
        # Accuracy alta + en parada + speed 0 + no consultó app
        speed_kmh = motor_res.get('speed_kmh', 0) or 0
        if (accuracy > 30 and 
            motor_res.get('en_parada') and 
            speed_kmh < 1 and
            not usuario.consulto_app_recientemente):
            
            # Verificar si en trazado (callejón no está en trazado)
            en_ruta, _ = punto_en_trazado(pos.lat, pos.lon, linea.trazado)
            if not en_ruta:
                return EdgeCaseResultado(
                    TipoEdgeCase.GPS_IMPRECISO_CALLEJON, True, 0.85,
                    'ajustar_score',
                    "GPS inestable en esta zona. ¿Estás en la parada principal?",
                    {'direccion': 'reducir', 'factor': 0.3, 'accuracy': accuracy}
                )
        
        return EdgeCaseResultado(TipoEdgeCase.GPS_IMPRECISO_CALLEJON, False, 0, 'ignorar')

    def _handle_parada_ida_vuelta(self, usuario, parada, linea, motor_res, intencion, otras, ahora):
        """Parada de ida y vuelta en mismo lugar físico - heading resuelve"""
        if not usuario.posiciones or not usuario.posiciones[-1].heading:
            return EdgeCaseResultado(TipoEdgeCase.PARADA_IDA_VUELTA_MISMO_LUGAR, False, 0, 'ignorar')
        
        # Verificar si línea tiene ambas direcciones en misma parada
        paradas_ida = [p for p in linea.paradas if p.id == parada.id]
        # Simplificado: asumimos que si hay ambas direcciones, el heading ayuda
        
        heading = usuario.posiciones[-1].heading
        # Heading hacia ida vs vuelta
        return EdgeCaseResultado(
            TipoEdgeCase.PARADA_IDA_VUELTA_MISMO_LUGAR, True, 0.5,
            'ajustar_score',
            None,
            {'direccion': 'neutral', 'heading': heading}
        )
    
    def _handle_sube_baja_rapido(self, usuario, parada, linea, motor_res, intencion, otras, ahora):
        """Usuario sube y baja en siguiente parada (trasbordo rápido)"""
        # Detectable por cambio de parada GPS en < 5 min
        if intencion and intencion.estado == EstadoBoton.CONFIRMADO:
            if usuario.posiciones:
                pos = usuario.posiciones[-1]
                dist_parada = haversine(pos.lat, pos.lon, parada.lat, parada.lon)
                if dist_parada > 200 and intencion.duracion_monitoreo(ahora) < 300:
                    return EdgeCaseResultado(
                        TipoEdgeCase.SUBE_BAJA_SIGUIENTE_PARADA, True, 0.7,
                        'notificar',
                        "Detectamos viaje corto. ¿Te bajaste en la siguiente parada?",
                        {'duracion_viaje': intencion.duracion_monitoreo(ahora)}
                    )
        return EdgeCaseResultado(TipoEdgeCase.SUBE_BAJA_SIGUIENTE_PARADA, False, 0, 'ignorar')
    
    def _handle_dos_lineas_misma_hora(self, usuario, parada, linea, motor_res, intencion, otras, ahora):
        """Dos líneas pasan por misma parada a misma hora"""
        return self._handle_multiples_lineas(usuario, parada, linea, motor_res, intencion, otras, ahora)
    
    def _handle_gps_interior_bus(self, usuario, parada, linea, motor_res, intencion, otras, ahora):
        """GPS salta dentro del bus (accuracy alta, en ruta, velocidad bus)"""
        if not usuario.posiciones:
            return EdgeCaseResultado(TipoEdgeCase.GPS_INTERIOR_BUS, False, 0, 'ignorar')
        
        pos = usuario.posiciones[-1]
        accuracy = pos.accuracy
        speed = motor_res.get('speed_kmh', 0)
        en_ruta = motor_res.get('en_ruta_consistente', False)
        
        if accuracy > 40 and en_ruta and 10 <= speed <= 50:
            # GPS malo pero claramente en bus
            return EdgeCaseResultado(
                TipoEdgeCase.GPS_INTERIOR_BUS, True, 0.9,
                'ajustar_score',
                None,
                {'direccion': 'aumentar', 'motivo': 'gps_interior_confirmado_bus'}
            )
        
        return EdgeCaseResultado(TipoEdgeCase.GPS_INTERIOR_BUS, False, 0, 'ignorar')
    
    def _handle_battery_saver(self, usuario, parada, linea, motor_res, intencion, otras, ahora):
        """Battery saver desactiva GPS - notificar al usuario"""
        if not usuario.gps_activo or not usuario.posiciones:
            # Última posición muy antigua
            if usuario.posiciones:
                ultima = usuario.posiciones[-1]
                if ahora - ultima.timestamp > 300:  # 5 min sin GPS
                    return EdgeCaseResultado(
                        TipoEdgeCase.BATTERY_SAVER_GPS, True, 0.8,
                        'notificar',
                        "GPS desactivado por ahorro de batería. Activa 'Ubicación precisa' para mejor detección.",
                        {'ultimo_gps_hace_min': (ahora - ultima.timestamp) // 60}
                    )
            return EdgeCaseResultado(
                TipoEdgeCase.BATTERY_SAVER_GPS, True, 0.6,
                'notificar',
                "GPS no disponible. Algunas funciones requieren ubicación activa.",
                {}
            )
        return EdgeCaseResultado(TipoEdgeCase.BATTERY_SAVER_GPS, False, 0, 'ignorar')
    
    def _handle_turista(self, usuario, parada, linea, motor_res, intencion, otras, ahora):
        """Turista no sabe qué línea tomar - mostrar todas cercanas"""
        # Detectable si usuario no tiene historial de líneas
        # Para implementación completa, necesitaría perfil de usuario
        return EdgeCaseResultado(TipoEdgeCase.TURISTA_SIN_SABER_LINEA, False, 0, 'ignorar')
    
    def _handle_variante(self, usuario, parada, linea, motor_res, intencion, otras, ahora):
        """Línea tiene variante (ej. Triquivijate/Las Playitas)"""
        if usuario.variante_posible or motor_res.get('detalle', {}).get('pen_variante_linea'):
            return EdgeCaseResultado(
                TipoEdgeCase.VARIANTE_LINEA, True, 0.5,
                'preguntar',
                "Esta línea tiene variantes (Triquivijate / Las Playitas). ¿Por cuál vas?",
                {'variantes': ['Triquivijate', 'Las Playitas']}
            )
        return EdgeCaseResultado(TipoEdgeCase.VARIANTE_LINEA, False, 0, 'ignorar')
    
    def _handle_festivos(self, usuario, parada, linea, motor_res, intencion, otras, ahora):
        """Festivos - horario reducido"""
        # Verificar si hoy es festivo en Canarias
        # Para implementación completa, usar holidays.json
        return EdgeCaseResultado(TipoEdgeCase.FESTIVOS_HORARIO_REDUCIDO, False, 0, 'ignorar')


# Funciones de conveniencia
def evaluar_edge_cases(usuario: UsuarioContexto, parada: Parada, linea: Linea,
                        resultado_motor: Dict, **kwargs) -> List[EdgeCaseResultado]:
    manejador = ManejadorEdgeCases()
    return manejador.evaluar_todos(usuario, parada, linea, resultado_motor, **kwargs)

def aplicar_edge_cases(resultado_motor: Dict, edge_cases: List[EdgeCaseResultado]) -> Dict:
    manejador = ManejadorEdgeCases()
    return manejador.aplicar_mitigaciones(resultado_motor, edge_cases)