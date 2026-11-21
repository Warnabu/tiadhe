"""
Lógica del Botón 'Me voy a subir' - Guaguas FTV
===============================================
Maneja habilitación condicional, registro de intención y temporizador 2 min.

Estados: INACTIVO → HABILITADO → PULSADO → MONITOREANDO → CONFIRMADO/DESCARTADO
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from enum import Enum
import time

from prototipo_sube import Parada, Linea, PosicionGPS, haversine, punto_en_trazado
from motor_senales import UsuarioContexto, MotorSenales


class EstadoBoton(Enum):
    INACTIVO = "inactivo"           # No cumple condiciones
    HABILITADO = "habilitado"       # Listo para pulsar
    PULSADO = "pulsado"             # Usuario tocó botón
    MONITOREANDO = "monitoreando"   # Ventana 2 min activa
    CONFIRMADO = "confirmado"       # Subida detectada
    DESCARTADO = "descartado"       # Falso positivo / timeout
    EXPIRADO = "expirado"           # Timeout 2 min sin confirmación


@dataclass
class IntencionSubida:
    """Registro de intención de subida"""
    id: str
    usuario_id: str
    linea_id: str
    sentido: str
    parada_id: str
    ts_creacion: int
    ts_expiracion: int
    gps_creacion: PosicionGPS
    estado: EstadoBoton = EstadoBoton.PULSADO
    ts_confirmacion: Optional[int] = None
    confirmada_por: Optional[str] = None  # 'gps_movimiento', 'gps_ruta', 'social', 'timeout', 'usuario_cancela'
    ubicaciones_monitoreo: List[PosicionGPS] = field(default_factory=list)
    
    def esta_expirada(self, ahora: int = None) -> bool:
        if ahora is None:
            ahora = int(time.time())
        return ahora >= self.ts_expiracion
    
    def duracion_monitoreo(self, ahora: int = None) -> int:
        if ahora is None:
            ahora = int(time.time())
        return ahora - self.ts_creacion


class ValidadorBoton:
    """
    Valida si el botón 'Me voy a subir' debe estar habilitado.
    
    Condiciones TODAS obligatorias:
    1. GPS activo compartido
    2. Distancia < 200m a la parada
    3. Línea pasa por esa parada
    4. Próxima salida en 20-30 min (no 5 horas, no ya pasó)
    5. Línea compatible geográficamente (usuario a <500m del trazado)
    6. No ha reportado subida en últimos 5 min
    """
    
    VENTANA_MINIMA_MIN = 20
    VENTANA_MAXIMA_MIN = 30
    DIST_MAX_PARADA = 200
    DIST_MAX_TRAZADO = 500
    COOLDOWN_SUBIDA = 300  # 5 min
    
    def __init__(self, motor_senales: MotorSenales = None):
        self.motor = motor_senales or MotorSenales()
    
    def validar(self, 
                usuario: UsuarioContexto, 
                parada: Parada, 
                linea: Linea,
                proxima_salida_ts: int,
                ultima_subida_ts: Optional[int] = None,
                ahora: int = None) -> Dict[str, Any]:
        """
        Valida condiciones y retorna dict con habilitado=True/False y razones.
        """
        if ahora is None:
            ahora = int(time.time())
        
        razones = []
        
        # 1. GPS activo
        if not usuario.gps_activo:
            razones.append("GPS no activo")
        
        # 2. Posición disponible
        if not usuario.posiciones:
            razones.append("Sin posición GPS reciente")
        
        # 3. Distancia a parada
        if usuario.posiciones:
            pos = usuario.posiciones[-1]
            dist = haversine(pos.lat, pos.lon, parada.lat, parada.lon)
            if dist > self.DIST_MAX_PARADA:
                razones.append(f"Lejos de parada ({dist:.0f}m > {self.DIST_MAX_PARADA}m)")
        
        # 4. Línea pasa por parada
        parada_en_linea = any(p.id == parada.id for p in linea.paradas)
        if not parada_en_linea:
            razones.append("Línea no pasa por esta parada")
        
        # 5. Ventana temporal 20-30 min
        dt_min = (proxima_salida_ts - ahora) / 60
        if dt_min < self.VENTANA_MINIMA_MIN:
            razones.append(f"Bus muy pronto ({dt_min:.0f} min < {self.VENTANA_MINIMA_MIN} min)")
        elif dt_min > self.VENTANA_MAXIMA_MIN:
            razones.append(f"Bus muy tarde ({dt_min:.0f} min > {self.VENTANA_MAXIMA_MIN} min)")
        
        # 6. Compatibilidad geográfica (usuario cerca del trazado)
        if usuario.posiciones:
            pos = usuario.posiciones[-1]
            _, dist_ruta = punto_en_trazado(pos.lat, pos.lon, linea.trazado)
            if dist_ruta > self.DIST_MAX_TRAZADO:
                razones.append(f"Lejos del trazado ({dist_ruta:.0f}m > {self.DIST_MAX_TRAZADO}m)")
        
        # 7. Cooldown subida reciente
        if ultima_subida_ts and (ahora - ultima_subida_ts) < self.COOLDOWN_SUBIDA:
            razones.append(f"Subida reciente hace {(ahora - ultima_subida_ts)//60} min")
        
        habilitado = len(razones) == 0
        
        return {
            'habilitado': habilitado,
            'razones': razones,
            'ventana_min': round(dt_min, 1) if 'dt_min' in locals() else None,
            'dist_parada': round(dist, 1) if 'dist' in locals() else None,
            'dist_trazado': round(dist_ruta, 1) if 'dist_ruta' in locals() else None,
        }
    
    def crear_intencion(self, 
                       usuario: UsuarioContexto,
                       parada: Parada,
                       linea: Linea,
                       sentido: str,
                       id_intencion: str = None) -> IntencionSubida:
        """Crea registro de intención tras pulsar botón"""
        if id_intencion is None:
            id_intencion = f"int_{usuario.id}_{int(time.time())}"
        
        ahora = int(time.time())
        pos = usuario.posiciones[-1] if usuario.posiciones else None
        
        return IntencionSubida(
            id=id_intencion,
            usuario_id=usuario.id,
            linea_id=linea.numero,
            sentido=sentido,
            parada_id=parada.id,
            ts_creacion=ahora,
            ts_expiracion=ahora + 120,  # 2 min
            gps_creacion=pos,
            estado=EstadoBoton.PULSADO,
        )


class MonitorIntencion:
    """
    Monitorea intención durante 2 min post-click.
    
    Eventos:
    - Se aleja >100m de parada → "¿Se equivocó?"
    - Se mueve en dirección ruta + velocidad bus → "Detectamos subida"
    - 2 min sin movimiento → "¿Sigues en la parada?"
    - Usuario cancela → DESCARTADO
    """
    
    DIST_ALERTA_ALEJAMIENTO = 100
    VENTANA_MONITOREO = 120  # 2 min
    SPEED_BUS_MIN = 5  # km/h
    
    def __init__(self, motor_senales: MotorSenales = None):
        self.motor = motor_senales or MotorSenales()
    
    def actualizar(self, 
                   intencion: IntencionSubida,
                   usuario: UsuarioContexto,
                   parada: Parada,
                   linea: Linea,
                   ahora: int = None) -> Dict[str, Any]:
        """
        Actualiza estado de monitoreo con nueva posición.
        Retorna dict con acciones requeridas (notificaciones, cambio estado).
        """
        if ahora is None:
            ahora = int(time.time())
        
        if intencion.esta_expirada(ahora):
            return self._expirar(intencion, ahora)
        
        if not usuario.posiciones:
            return {'accion': 'esperar_gps'}
        
        pos_actual = usuario.posiciones[-1]
        intencion.ubicaciones_monitoreo.append(pos_actual)
        
        # Distancia desde parada original
        dist_origen = haversine(
            pos_actual.lat, pos_actual.lon,
            intencion.gps_creacion.lat, intencion.gps_creacion.lon
        )
        
        # Distancia a parada objetivo
        dist_parada = haversine(pos_actual.lat, pos_actual.lon, parada.lat, parada.lon)
        
        # Velocidad actual
        speed = pos_actual.speed * 3.6 if pos_actual.speed else None
        
        # Verificar alejamiento (>100m de donde pulsó botón)
        if dist_origen > self.DIST_ALERTA_ALEJAMIENTO:
            return self._alejamiento_detectado(intencion, dist_origen, ahora)
        
        # Verificar subida: movimiento en ruta + velocidad bus
        en_ruta, _ = punto_en_trazado(pos_actual.lat, pos_actual.lon, linea.trazado)
        if en_ruta and speed and speed >= self.SPEED_BUS_MIN:
            return self._subida_confirmada(intencion, 'gps_ruta', ahora)
        
        # Verificar subida por motor de señales
        resultado_motor = self.motor.evaluar(usuario, parada, linea)
        if resultado_motor['umbral'] in ('confirmar_auto', 'notificar_confirmar'):
            if resultado_motor['score'] >= 0.85:
                return self._subida_confirmada(intencion, 'motor_senales', ahora)
        
        # Timeout sin movimiento (quietud prolongada)
        if intencion.duracion_monitoreo(ahora) >= 90 and speed is not None and speed < 1.0:
            return {'accion': 'notificar', 'tipo': 'sigues_en_parada', 'estado': intencion.estado}
        
        return {'accion': 'continuar_monitoreo', 'estado': EstadoBoton.MONITOREANDO}
    
    def _alejamiento_detectado(self, intencion: IntencionSubida, dist: float, ahora: int) -> Dict:
        intencion.estado = EstadoBoton.DESCARTADO
        intencion.ts_confirmacion = ahora
        intencion.confirmada_por = 'usuario_cancela'
        return {
            'accion': 'notificar',
            'tipo': 'se_alejo',
            'mensaje': f'Te alejaste {dist:.0f}m de la parada. ¿Te equivocaste?',
            'estado': EstadoBoton.DESCARTADO,
            'intencion': intencion,
        }
    
    def _subida_confirmada(self, intencion: IntencionSubida, motivo: str, ahora: int) -> Dict:
        intencion.estado = EstadoBoton.CONFIRMADO
        intencion.ts_confirmacion = ahora
        intencion.confirmada_por = motivo
        return {
            'accion': 'confirmar_subida',
            'motivo': motivo,
            'estado': EstadoBoton.CONFIRMADO,
            'intencion': intencion,
        }
    
    def _expirar(self, intencion: IntencionSubida, ahora: int) -> Dict:
        intencion.estado = EstadoBoton.EXPIRADO
        intencion.ts_confirmacion = ahora
        intencion.confirmada_por = 'timeout'
        return {
            'accion': 'expirar',
            'estado': EstadoBoton.EXPIRADO,
            'intencion': intencion,
        }
    
    def cancelar_usuario(self, intencion: IntencionSubida, ahora: int = None) -> Dict:
        """Usuario cancela explícitamente"""
        if ahora is None:
            ahora = int(time.time())
        intencion.estado = EstadoBoton.DESCARTADO
        intencion.ts_confirmacion = ahora
        intencion.confirmada_por = 'usuario_cancela'
        return {'accion': 'cancelado', 'estado': EstadoBoton.DESCARTADO, 'intencion': intencion}


# Funciones de conveniencia
def validar_boton(usuario: UsuarioContexto, parada: Parada, linea: Linea,
                  proxima_salida_ts: int, ultima_subida_ts: int = None) -> Dict:
    validador = ValidadorBoton()
    return validador.validar(usuario, parada, linea, proxima_salida_ts, ultima_subida_ts)

def crear_intencion(usuario: UsuarioContexto, parada: Parada, linea: Linea,
                    sentido: str, id_intencion: str = None) -> IntencionSubida:
    validador = ValidadorBoton()
    return validador.crear_intencion(usuario, parada, linea, sentido, id_intencion)

def monitorear_intencion(intencion: IntencionSubida, usuario: UsuarioContexto,
                         parada: Parada, linea: Linea) -> Dict:
    monitor = MonitorIntencion()
    return monitor.actualizar(intencion, usuario, parada, linea)