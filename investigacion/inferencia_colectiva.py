"""
Inferencia Colectiva '¿El Bus Salió?' - Guaguas FTV
====================================================
Protocolo para detectar salida de bus combinando múltiples usuarios.

Flujo:
1. N usuarios tocan botón en misma parada + línea
2. Esperar 2 min (ventana monitoreo individual)
3. Evaluar cada usuario con GPS claro:
   - Movimiento >50m de parada → subido=True
   - Sigue en parada → subido=False
   - GPS incierto → no cuenta
4. Si ≥50% dicen subido → "Bus salió" = True
5. Si <50% → "No ha salido", esperar 5 min más
6. Si 0 con GPS → "Inconclusivo", pedir confirmación manual
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
import time
import statistics

from prototipo_sube import Parada, Linea, PosicionGPS, haversine
from motor_senales import UsuarioContexto, MotorSenales
from boton_subir import IntencionSubida, EstadoBoton, MonitorIntencion


class EstadoColectivo(Enum):
    ESPERANDO = "esperando"           # Acumulando intenciones
    EVALUANDO = "evaluando"           # Ventana 2 min activa
    BUS_SALIO = "bus_salio"           # ≥50% confirmados subidos
    BUS_NO_SALIO = "bus_no_salio"     # <50% subidos
    INCONCLUSIVO = "inconclusivo"     # Sin datos GPS fiables
    EXPIRADO = "expirado"             # Timeout total


@dataclass
class VotoUsuario:
    """Voto individual de un usuario en la inferencia colectiva"""
    usuario_id: str
    intencion_id: str
    subido: bool
    confianza: float  # 0.0 - 1.0
    motivo: str  # 'gps_movimiento', 'gps_ruta', 'motor_senales', 'social', 'incierto'
    ts_voto: int
    dist_movimiento: float = 0.0


@dataclass
class SesionInferencia:
    """Sesión de inferencia colectiva para una parada + línea + sentido"""
    id: str
    parada_id: str
    linea_id: str
    sentido: str
    ts_inicio: int
    ts_evaluacion: int  # ts_inicio + 120s (2 min)
    ts_expiracion: int  # ts_inicio + 300s (5 min total)
    intenciones: List[str] = field(default_factory=list)  # IDs de intenciones
    votos: List[VotoUsuario] = field(default_factory=list)
    estado: EstadoColectivo = EstadoColectivo.ESPERANDO
    resultado_final: Optional[bool] = None
    ts_resultado: Optional[int] = None
    
    def esta_en_evaluacion(self, ahora: int = None) -> bool:
        if ahora is None:
            ahora = int(time.time())
        return self.ts_inicio < ahora < self.ts_evaluacion
    
    def esta_expirada(self, ahora: int = None) -> bool:
        if ahora is None:
            ahora = int(time.time())
        return ahora >= self.ts_expiracion


class MotorInferenciaColectiva:
    """
    Motor principal de inferencia colectiva.
    
    Parámetros:
    - VENTANA_EVALUACION: 120s (2 min) para votos iniciales
    - VENTANA_TOTAL: 300s (5 min) máximo
    - UMBRAL_BUS_SALIO: 0.50 (50% votos positivos)
    - MIN_VOTOS_GPS: 1 (mínimo votos con GPS fiable)
    - DIST_MIN_SUBIDA: 50m (movimiento mínimo para considerar subido)
    """
    
    VENTANA_EVALUACION = 120
    VENTANA_TOTAL = 300
    UMBRAL_BUS_SALIO = 0.50
    MIN_VOTOS_GPS = 1
    DIST_MIN_SUBIDA = 50
    CONFIANZA_GPS_MOV = 0.9
    CONFIANZA_GPS_RUTA = 0.85
    CONFIANZA_MOTOR = 0.8
    CONFIANZA_SOCIAL = 0.7
    
    def __init__(self, motor_senales: MotorSenales = None, monitor_intencion: MonitorIntencion = None):
        self.motor = motor_senales or MotorSenales()
        self.monitor = monitor_intencion or MonitorIntencion(motor_senales)
        self.sesiones: Dict[str, SesionInferencia] = {}
    
    def registrar_intencion(self, intencion: IntencionSubida) -> str:
        """Registra intención en sesión colectiva (crea o une existente)"""
        clave = f"{intencion.parada_id}_{intencion.linea_id}_{intencion.sentido}"
        
        if clave not in self.sesiones:
            ahora = int(time.time())
            self.sesiones[clave] = SesionInferencia(
                id=f"ses_{clave}_{ahora}",
                parada_id=intencion.parada_id,
                linea_id=intencion.linea_id,
                sentido=intencion.sentido,
                ts_inicio=ahora,
                ts_evaluacion=ahora + self.VENTANA_EVALUACION,
                ts_expiracion=ahora + self.VENTANA_TOTAL,
            )
            self.sesiones[clave].estado = EstadoColectivo.ESPERANDO
        
        sesion = self.sesiones[clave]
        if intencion.id not in sesion.intenciones:
            sesion.intenciones.append(intencion.id)
        
        return sesion.id
    
    def evaluar_sesion(self, 
                       sesion_id: str,
                       usuarios: Dict[str, UsuarioContexto],
                       intenciones: Dict[str, IntencionSubida],
                       parada: Parada,
                       linea: Linea,
                       ahora: int = None) -> Dict[str, Any]:
        """
        Evalúa sesión colectiva en ventana de evaluación (2 min).
        Retorna resultado y acciones.
        """
        if ahora is None:
            ahora = int(time.time())
        
        # Buscar sesión por ID (formato: ses_{clave}_{timestamp})
        sesion = self.sesiones.get(sesion_id)
        if not sesion:
            # Extraer clave del sesion_id: ses_{parada}_{linea}_{sentido}_{ts}
            partes = sesion_id.split('_')
            if len(partes) >= 4:
                clave = '_'.join(partes[1:-1])  # quita 'ses' y timestamp final
                sesion = self.sesiones.get(clave)
        
        if not sesion:
            return {'error': 'Sesión no encontrada'}
        
        if sesion.estado in (EstadoColectivo.BUS_SALIO, EstadoColectivo.BUS_NO_SALIO,
                            EstadoColectivo.INCONCLUSIVO, EstadoColectivo.EXPIRADO):
            return {'estado': sesion.estado, 'ya_evaluada': True}
        
        if ahora < sesion.ts_evaluacion:
            sesion.estado = EstadoColectivo.EVALUANDO
            return {'estado': EstadoColectivo.EVALUANDO, 'segundos_restantes': sesion.ts_evaluacion - ahora}
        
        # Ventana de evaluación completada - procesar votos
        return self._procesar_votos(sesion, usuarios, intenciones, parada, linea, ahora)
    
    def _procesar_votos(self, 
                        sesion: SesionInferencia,
                        usuarios: Dict[str, UsuarioContexto],
                        intenciones: Dict[str, IntencionSubida],
                        parada: Parada,
                        linea: Linea,
                        ahora: int) -> Dict[str, Any]:
        """Procesa votos de todas las intenciones en la sesión"""
        votos_validos = []
        votos_inciertos = 0
        
        for int_id in sesion.intenciones:
            if int_id not in intenciones:
                continue
            
            intencion = intenciones[int_id]
            user_id = intencion.usuario_id
            
            if user_id not in usuarios:
                votos_inciertos += 1
                continue
            
            usuario = usuarios[user_id]
            voto = self._evaluar_voto_individual(intencion, usuario, parada, linea, ahora)
            
            if voto:
                sesion.votos.append(voto)
                if voto.confianza >= 0.5:
                    votos_validos.append(voto)
                else:
                    votos_inciertos += 1
            else:
                votos_inciertos += 1
        
        # Decidir resultado
        if not votos_validos:
            # Sin votos fiables
            if votos_inciertos > 0:
                sesion.estado = EstadoColectivo.INCONCLUSIVO
                sesion.resultado_final = None
            else:
                sesion.estado = EstadoColectivo.EXPIRADO
            sesion.ts_resultado = ahora
            return self._construir_resultado(sesion, ahora)
        
        # Calcular proporción subidos
        subidos = sum(1 for v in votos_validos if v.subido)
        total_validos = len(votos_validos)
        proporcion = subidos / total_validos
        
        if proporcion >= self.UMBRAL_BUS_SALIO:
            sesion.estado = EstadoColectivo.BUS_SALIO
            sesion.resultado_final = True
        else:
            sesion.estado = EstadoColectivo.BUS_NO_SALIO
            sesion.resultado_final = False
        
        sesion.ts_resultado = ahora
        return self._construir_resultado(sesion, ahora)
    
    def _evaluar_voto_individual(self,
                                  intencion: IntencionSubida,
                                  usuario: UsuarioContexto,
                                  parada: Parada,
                                  linea: Linea,
                                  ahora: int) -> Optional[VotoUsuario]:
        """Evalúa voto de un usuario individual"""
        
        # Si intención ya tiene confirmación (monitor individual)
        if intencion.estado == EstadoBoton.CONFIRMADO:
            return VotoUsuario(
                usuario_id=usuario.id,
                intencion_id=intencion.id,
                subido=True,
                confianza=0.95,
                motivo=intencion.confirmada_por or 'monitor_individual',
                ts_voto=intencion.ts_confirmacion or ahora,
            )
        
        # Si intención descartada/expirada
        if intencion.estado in (EstadoBoton.DESCARTADO, EstadoBoton.EXPIRADO):
            return VotoUsuario(
                usuario_id=usuario.id,
                intencion_id=intencion.id,
                subido=False,
                confianza=0.7,
                motivo=intencion.confirmada_por or 'timeout',
                ts_voto=intencion.ts_confirmacion or ahora,
            )
        
        # Evaluar con GPS actual
        if not usuario.posiciones:
            return VotoUsuario(
                usuario_id=usuario.id,
                intencion_id=intencion.id,
                subido=False,
                confianza=0.1,
                motivo='sin_gps',
                ts_voto=ahora,
            )
        
        pos = usuario.posiciones[-1]
        dist_parada = haversine(pos.lat, pos.lon, parada.lat, parada.lon)
        
        # Movimiento significativo desde parada
        if dist_parada > self.DIST_MIN_SUBIDA:
            # Verificar si en ruta
            en_ruta, _ = punto_en_trazado(pos.lat, pos.lon, linea.trazado)
            speed = pos.speed * 3.6 if pos.speed else 0
            
            if en_ruta and speed >= 5:
                return VotoUsuario(
                    usuario_id=usuario.id,
                    intencion_id=intencion.id,
                    subido=True,
                    confianza=self.CONFIANZA_GPS_RUTA,
                    motivo='gps_ruta',
                    ts_voto=ahora,
                    dist_movimiento=dist_parada,
                )
            elif speed >= 2:
                return VotoUsuario(
                    usuario_id=usuario.id,
                    intencion_id=intencion.id,
                    subido=True,
                    confianza=self.CONFIANZA_GPS_MOV,
                    motivo='gps_movimiento',
                    ts_voto=ahora,
                    dist_movimiento=dist_parada,
                )
        
        # Evaluar con motor de señales
        resultado_motor = self.motor.evaluar(usuario, parada, linea)
        if resultado_motor['score'] >= 0.7:
            return VotoUsuario(
                usuario_id=usuario.id,
                intencion_id=intencion.id,
                subido=True,
                confianza=self.CONFIANZA_MOTOR * resultado_motor['score'],
                motivo='motor_senales',
                ts_voto=ahora,
            )
        
        # En parada, no subido
        return VotoUsuario(
            usuario_id=usuario.id,
            intencion_id=intencion.id,
            subido=False,
            confianza=0.6,
            motivo='en_parada_quieto',
            ts_voto=ahora,
        )
    
    def _construir_resultado(self, sesion: SesionInferencia, ahora: int) -> Dict[str, Any]:
        """Construye dict de resultado para API/notificaciones"""
        votos_subidos = [v for v in sesion.votos if v.subido]
        votos_no_subidos = [v for v in sesion.votos if not v.subido]
        
        return {
            'sesion_id': sesion.id,
            'estado': sesion.estado.value,
            'bus_salio': sesion.resultado_final,
            'total_intenciones': len(sesion.intenciones),
            'votos_validos': len(sesion.votos),
            'subidos': len(votos_subidos),
            'no_subidos': len(votos_no_subidos),
            'proporcion_subidos': round(len(votos_subidos) / len(sesion.votos), 2) if sesion.votos else 0,
            'detalle_votos': [
                {
                    'usuario_id': v.usuario_id,
                    'subido': v.subido,
                    'confianza': round(v.confianza, 2),
                    'motivo': v.motivo,
                    'dist_movimiento': round(v.dist_movimiento, 1),
                }
                for v in sesion.votos
            ],
            'timestamp': ahora,
            'accion_requerida': self._determinar_accion(sesion),
        }
    
    def _determinar_accion(self, sesion: SesionInferencia) -> str:
        if sesion.estado == EstadoColectivo.BUS_SALIO:
            return 'notificar_bus_salio'
        elif sesion.estado == EstadoColectivo.BUS_NO_SALIO:
            return 'notificar_bus_no_salio_esperar'
        elif sesion.estado == EstadoColectivo.INCONCLUSIVO:
            return 'pedir_confirmacion_manual'
        return 'esperar'
    
    def obtener_sesion(self, parada_id: str, linea_id: str, sentido: str) -> Optional[SesionInferencia]:
        clave = f"{parada_id}_{linea_id}_{sentido}"
        return self.sesiones.get(clave)
    
    def limpiar_expiradas(self, ahora: int = None) -> int:
        """Limpia sesiones expiradas, retorna count"""
        if ahora is None:
            ahora = int(time.time())
        
        expiradas = [k for k, s in self.sesiones.items() if s.esta_expirada(ahora)]
        for k in expiradas:
            del self.sesiones[k]
        return len(expiradas)


# Funciones de conveniencia
def crear_sesion(parada_id: str, linea_id: str, sentido: str) -> SesionInferencia:
    motor = MotorInferenciaColectiva()
    clave = f"{parada_id}_{linea_id}_{sentido}"
    ahora = int(time.time())
    sesion = SesionInferencia(
        id=f"ses_{clave}_{ahora}",
        parada_id=parada_id,
        linea_id=linea_id,
        sentido=sentido,
        ts_inicio=ahora,
        ts_evaluacion=ahora + motor.VENTANA_EVALUACION,
        ts_expiracion=ahora + motor.VENTANA_TOTAL,
    )
    motor.sesiones[clave] = sesion
    return sesion