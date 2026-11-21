"""
ETA Dinámico - Guaguas FTV
==========================
Combina ETA base (Guaguas FTV/horarios) con observaciones reales de usuarios
usando aprendizaje bayesiano con K_PRIOR.

Fórmula: ETA = (K_PRIOR * ETA_base + Σ(w_i * ETA_i)) / (K_PRIOR + Σw_i)
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import time
import statistics
from enum import Enum

from prototipo_sube import Linea, Parada
from comunidad import Comunidad
from motor_senales import UsuarioContexto, MotorSenales


class OrigenETA(Enum):
    GUAGUAS_FTV_BASE = "guaguas_ftv_base"
    USUARIO_GPS_CLARO = "usuario_gps_claro"
    USUARIO_GPS_RUIDOSO = "usuario_gps_ruidoso"
    USUARIO_BOTON_SOLO = "usuario_boton_solo"
    ACELEROMETRO = "acelerometro"
    INFERENCIA_COLECTIVA = "inferencia_colectiva"


@dataclass
class ObservacionETA:
    """Observación individual de ETA desde un usuario"""
    origen: OrigenETA
    usuario_id: str
    linea_id: str
    sentido: str
    parada_origen_id: str
    parada_destino_id: str
    eta_observado_seg: int  # ETA en segundos desde parada_origen
    ts_observacion: int
    peso_reputacion: float  # 0.3 - 1.0
    confianza: float  # 0.0 - 1.0
    metadatos: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ETACalculado:
    """Resultado del cálculo ETA dinámico"""
    linea_id: str
    sentido: str
    parada_origen_id: str
    parada_destino_id: str
    eta_base_seg: int
    eta_dinamico_seg: int
    eta_final_seg: int
    confianza: float  # 0.0 - 1.0
    observaciones_usadas: int
    peso_total_observaciones: float
    desglose: Dict[str, Any]
    ts_calculo: int
    advertencias: List[str] = field(default_factory=list)


class MotorETADinamico:
    """
    Motor de ETA dinámico con aprendizaje bayesiano.
    
    Parámetros:
    - K_PRIOR: 4.0 (peso del modelo base Guaguas FTV)
    - Pesos por origen (ver PESOS_ORIGEN)
    - Ventana validez observaciones: 15 min
    - Mín observaciones para ajustar: 1
    """
    
    K_PRIOR = 4.0
    VENTANA_VALIDEZ_OBS = 900  # 15 min
    MIN_OBS_PARA_AJUSTAR = 1
    
    PESOS_ORIGEN = {
        OrigenETA.GUAGUAS_FTV_BASE: 1.0,           # Normalizado por K_PRIOR
        OrigenETA.USUARIO_GPS_CLARO: 1.0,     # * reputación
        OrigenETA.USUARIO_GPS_RUIDOSO: 0.5,   # * reputación
        OrigenETA.USUARIO_BOTON_SOLO: 0.3,    # * reputación
        OrigenETA.ACELEROMETRO: 0.7,          # * reputación
        OrigenETA.INFERENCIA_COLECTIVA: 1.5,  # Boost por consenso
    }
    
    # Pesos base por reputación (se multiplican por peso_origen)
    PESO_REP_MIN = 0.35
    PESO_REP_MAX = 0.90
    
    def __init__(self, comunidad: Comunidad = None):
        self.comunidad = comunidad
        self.observaciones: List[ObservacionETA] = []
    
    def calcular_eta(self,
                     linea: Linea,
                     sentido: str,
                     parada_origen: Parada,
                     parada_destino: Parada,
                     eta_base_seg: int = None,
                     ahora: int = None) -> ETACalculado:
        """
        Calcula ETA dinámico combinando base + observaciones.
        
        Args:
            linea: Línea de la guagua
            sentido: 'ida' o 'vuelta'
            parada_origen: Parada desde donde se consulta
            parada_destino: Parada destino (donde está el usuario)
            eta_base_seg: ETA base en segundos (si None, calcula de horarios)
            ahora: Timestamp actual
            
        Returns:
            ETACalculado con ETA final y desglose
        """
        if ahora is None:
            ahora = int(time.time())
        
        # 1. Obtener ETA base (Guaguas FTV/horarios)
        if eta_base_seg is None:
            eta_base_seg = self._calcular_eta_base(linea, sentido, parada_origen, parada_destino, ahora)
        
        # 2. Filtrar observaciones válidas
        obs_validas = self._filtrar_observaciones(
            linea.num, sentido, parada_origen.id, parada_destino.id, ahora
        )
        
        # 3. Calcular ETA ponderado
        if len(obs_validas) >= self.MIN_OBS_PARA_AJUSTAR:
            eta_dinamico, peso_total, confianza = self._combinar_observaciones(obs_validas, eta_base_seg)
            eta_final = int((self.K_PRIOR * eta_base_seg + peso_total * eta_dinamico) / 
                           (self.K_PRIOR + peso_total))
        else:
            eta_dinamico = eta_base_seg
            peso_total = 0.0
            confianza = 0.5
            eta_final = eta_base_seg
        
        # 4. Construir resultado
        return ETACalculado(
            linea_id=linea.num,
            sentido=sentido,
            parada_origen_id=parada_origen.id,
            parada_destino_id=parada_destino.id,
            eta_base_seg=eta_base_seg,
            eta_dinamico_seg=eta_dinamico,
            eta_final_seg=eta_final,
            confianza=round(confianza, 2),
            observaciones_usadas=len(obs_validas),
            peso_total_observaciones=round(peso_total, 2),
            desglose=self._construir_desglose(obs_validas, eta_base_seg, eta_dinamico, peso_total),
            ts_calculo=ahora,
            advertencias=self._generar_advertencias(obs_validas, eta_base_seg, eta_final),
        )
    
    def _calcular_eta_base(self, linea: Linea, sentido: str, 
                           origen: Parada, destino: Parada, ahora: int) -> int:
        """Calcula ETA base desde horarios (simplificado)"""
        # Usar duración estimada de la línea
        duracion_key = 'ida' if sentido == 'ida' else 'vuelta'
        duracion_total = linea.duracion_min.get(duracion_key, 45)
        
        # Encontrar índices en paradas
        idx_origen = next((i for i, p in enumerate(linea.paradas) if p.id == origen.id), 0)
        idx_destino = next((i for i, p in enumerate(linea.paradas) if p.id == destino.id), len(linea.paradas)-1)
        
        if idx_destino <= idx_origen:
            return duracion_total * 60
        
        # Proporción del recorrido
        proporcion = (idx_destino - idx_origen) / max(1, len(linea.paradas) - 1)
        return int(duracion_total * 60 * proporcion)
    
    def _filtrar_observaciones(self, linea_id: str, sentido: str,
                                origen_id: str, destino_id: str, 
                                ahora: int) -> List[ObservacionETA]:
        """Filtra observaciones válidas para este trayecto"""
        obs = []
        for o in self.observaciones:
            if (o.linea_id == linea_id and 
                o.sentido == sentido and
                o.parada_origen_id == origen_id and
                o.parada_destino_id == destino_id and
                ahora - o.ts_observacion <= self.VENTANA_VALIDEZ_OBS):
                obs.append(o)
        return obs
    
    def _combinar_observaciones(self, obs: List[ObservacionETA], 
                                 eta_base: int) -> tuple:
        """Combina observaciones con pesos"""
        suma_pesos = 0.0
        suma_eta_ponderada = 0.0
        total_confianza = 0.0
        
        for o in obs:
            peso_origen = self.PESOS_ORIGEN.get(o.origen, 0.5)
            peso_final = peso_origen * o.peso_reputacion * o.confianza
            
            # Filtrar outliers (>3x ETA base)
            if o.eta_observado_seg > eta_base * 3:
                continue
            
            suma_pesos += peso_final
            suma_eta_ponderada += o.eta_observado_seg * peso_final
            total_confianza += o.confianza * peso_final
        
        if suma_pesos == 0:
            return eta_base, 0.0, 0.5
        
        eta_combinado = suma_eta_ponderada / suma_pesos
        confianza = min(1.0, total_confianza / len(obs)) if obs else 0.5
        
        return int(eta_combinado), suma_pesos, confianza
    
    def _construir_desglose(self, obs: List[ObservacionETA], 
                            eta_base: int, eta_din: int, peso_total: float) -> Dict:
        return {
            'eta_base_seg': eta_base,
            'eta_observaciones_seg': eta_din,
            'peso_total_obs': round(peso_total, 2),
            'k_prior': self.K_PRIOR,
            'observaciones': [
                {
                    'origen': o.origen.value,
                    'usuario': o.usuario_id,
                    'eta_seg': o.eta_observado_seg,
                    'peso_rep': o.peso_reputacion,
                    'confianza': o.confianza,
                    'peso_final': round(self.PESOS_ORIGEN.get(o.origen, 0.5) * o.peso_reputacion * o.confianza, 3),
                    'antiguedad_min': round((time.time() - o.ts_observacion) / 60, 1),
                }
                for o in obs
            ],
        }
    
    def _generar_advertencias(self, obs: List[ObservacionETA], 
                              eta_base: int, eta_final: int) -> List[str]:
        adv = []
        
        if not obs:
            adv.append("Sin observaciones recientes - usando solo horario base")
        
        if len(obs) == 1:
            adv.append("Solo 1 observación - baja confianza")
        
        # Verificar consistencia
        etas = [o.eta_observado_seg for o in obs]
        if len(etas) >= 2:
            cv = statistics.stdev(etas) / statistics.mean(etas) if statistics.mean(etas) > 0 else 0
            if cv > 0.5:
                adv.append(f"Alta variabilidad entre observaciones (CV={cv:.1%})")
        
        # Desviación grande vs base
        if eta_base > 0:
            desviacion = abs(eta_final - eta_base) / eta_base
            if desviacion > 0.5:
                adv.append(f"ETA difiere {desviacion:.0%} del horario base")
        
        return adv
    
    def registrar_observacion(self, obs: ObservacionETA):
        """Registra nueva observación de ETA"""
        # Calcular peso por reputación si hay comunidad
        if self.comunidad and obs.usuario_id in self.comunidad.dispositivos:
            disp = self.comunidad.dispositivos[obs.usuario_id]
            obs.peso_reputacion = max(self.PESO_REP_MIN, 
                                     min(self.PESO_REP_MAX, disp.reputacion / 100))
        
        self.observaciones.append(obs)
        
        # Limpiar antiguas (>30 min)
        ahora = int(time.time())
        self.observaciones = [
            o for o in self.observaciones 
            if ahora - o.ts_observacion <= 1800
        ]
    
    def registrar_desde_subida(self,
                                usuario: UsuarioContexto,
                                linea: Linea,
                                sentido: str,
                                parada_subida: Parada,
                                parada_destino: Parada,
                                ts_subida: int,
                                origen: OrigenETA,
                                confianza: float = 1.0) -> Optional[ObservacionETA]:
        """
        Registra observación ETA desde una subida confirmada.
        Calcula ETA observado = tiempo desde subida hasta destino estimado.
        """
        # Estimar ETA desde subida hasta destino usando horarios
        eta_estimado = self._calcular_eta_base(linea, sentido, parada_subida, parada_destino, ts_subida)
        
        # El ETA observado es el estimado (se validará cuando usuario baje o llegue)
        # Para implementación real, se guardaría ts_subida y se calcularía al llegar
        
        peso_rep = 0.5
        if self.comunidad and usuario.id in self.comunidad.dispositivos:
            disp = self.comunidad.dispositivos[usuario.id]
            peso_rep = max(self.PESO_REP_MIN, min(self.PESO_REP_MAX, disp.reputacion / 100))
        
        obs = ObservacionETA(
            origen=origen,
            usuario_id=usuario.id,
            linea_id=linea.num,
            sentido=sentido,
            parada_origen_id=parada_subida.id,
            parada_destino_id=parada_destino.id,
            eta_observado_seg=eta_estimado,
            ts_observacion=ts_subida,
            peso_reputacion=peso_rep,
            confianza=confianza,
            metadatos={'parada_subida': parada_subida.id}
        )
        
        self.registrar_observacion(obs)
        return obs
    
    def obtener_estadisticas(self, linea_id: str = None) -> Dict[str, Any]:
        """Estadísticas del motor"""
        obs = self.observaciones
        if linea_id:
            obs = [o for o in obs if o.linea_id == linea_id]
        
        return {
            'total_observaciones': len(obs),
            'por_origen': {
                k.value: len([o for o in obs if o.origen == k])
                for k in OrigenETA
            },
            'lineas_activas': list(set(o.linea_id for o in obs)),
            'ultima_actualizacion': max((o.ts_observacion for o in obs), default=None),
        }


# Funciones de conveniencia
def calcular_eta_dinamico(linea: Linea, sentido: str,
                           origen: Parada, destino: Parada,
                           comunidad: Comunidad = None) -> ETACalculado:
    motor = MotorETADinamico(comunidad)
    return motor.calcular_eta(linea, sentido, origen, destino)

def registrar_eta_desde_subida(motor: MotorETADinamico, usuario: UsuarioContexto,
                                linea: Linea, sentido: str,
                                parada_subida: Parada, parada_destino: Parada,
                                ts_subida: int, origen: OrigenETA,
                                confianza: float = 1.0):
    return motor.registrar_desde_subida(usuario, linea, sentido, 
                                        parada_subida, parada_destino,
                                        ts_subida, origen, confianza)