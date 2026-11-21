"""
API REST Endpoints para Detección de Subida - Guaguas FTV
=========================================================
Endpoints FastAPI/Flask para backend (Render + Supabase).

Endpoints:
- POST /api/intencion          Registrar intención "Me voy a subir"
- POST /api/ubicacion          Actualizar GPS (batch cada 5-10s)
- GET  /api/esta_el_bus        ¿Bus en parada ahora? (inferencia colectiva)
- GET  /api/eta                ETA dinámico combinado
- POST /api/subida             Confirmar subida (explícita o inferida)
- GET  /api/paradas            Paradas + horarios (cacheado)
- WS   /ws/ubicaciones         WebSocket tiempo real (opcional)
"""

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any
from datetime import datetime
import time
import json
import uuid

from prototipo_sube import Linea, Parada, PosicionGPS, haversine
from guaguas import cargar
from servidor import agrupar
from motor_senales import UsuarioContexto, MotorSenales
from boton_subir import (ValidadorBoton, MonitorIntencion, IntencionSubida, 
                          EstadoBoton)
from inferencia_colectiva import (MotorInferenciaColectiva, SesionInferencia,
                                   EstadoColectivo)
from eta_dinamico import (MotorETADinamico, OrigenETA, ObservacionETA)
from edge_cases import (ManejadorEdgeCases, TipoEdgeCase)
from comunidad import Comunidad
from edge_cases import (ManejadorEdgeCases, evaluar_edge_cases, aplicar_edge_cases,
                         TipoEdgeCase)
from comunidad import Comunidad


# === Modelos de Request/Response ===

@dataclass
class IntencionRequest:
    usuario_id: str
    linea_id: str
    sentido: str
    parada_id: str
    gps_lat: float
    gps_lon: float
    gps_accuracy: float
    gps_speed: Optional[float] = None
    gps_heading: Optional[float] = None
    device_token: Optional[str] = None  # Para FCM

@dataclass
class IntencionResponse:
    intencion_id: str
    habilitado: bool
    razones: List[str]
    ventana_min: Optional[float]
    estado: str
    mensaje: Optional[str] = None

@dataclass
class UbicacionRequest:
    usuario_id: str
    intencion_id: Optional[str]
    lat: float
    lon: float
    accuracy: float
    speed: Optional[float] = None
    heading: Optional[float] = None
    timestamp: Optional[int] = None

@dataclass
class UbicacionResponse:
    score: float
    umbral: str
    accion: str  # 'continuar', 'confirmar', 'notificar', 'descartar'
    mensaje: Optional[str] = None
    edge_cases: List[str] = None

@dataclass
class EstaElBusResponse:
    bus_salio: Optional[bool]
    proporcion_subidos: float
    total_usuarios: int
    mensaje: str
    accion: str

@dataclass
class ETARequest:
    usuario_id: str
    linea_id: str
    sentido: str
    parada_origen_id: str
    parada_destino_id: str

@dataclass
class ETAFinalResponse:
    eta_segundos: int
    eta_minutos: int
    confianza: float
    fuente: str  # 'base', 'dinamico', 'hibrido'
    advertencias: List[str]

@dataclass
class SubidaRequest:
    usuario_id: str
    intencion_id: Optional[str]
    linea_id: str
    sentido: str
    parada_id: str
    confirmada: bool
    origen: str  # 'boton', 'gps', 'motor', 'colectiva', 'manual'

@dataclass
class SubidaResponse:
    registrada: bool
    subida_id: str
    reputacion_actualizada: float


# === Servicio Principal ===

class ServicioSubida:
    """
    Servicio completo de detección de subida.
    Integra: validador botón, monitor, motor señales, inferencia colectiva, ETA, edge cases.
    """
    
    def __init__(self, comunidad: Comunidad = None):
        self.comunidad = comunidad or Comunidad()
        self.motor_senales = MotorSenales()
        self.validador = ValidadorBoton(self.motor_senales)
        self.monitor = MonitorIntencion(self.motor_senales)
        self.inferencia = MotorInferenciaColectiva(self.motor_senales, self.monitor)
        self.motor_eta = MotorETADinamico(self.comunidad)
        self.edge_cases = ManejadorEdgeCases(self.motor_senales)
        
        # Estado en memoria (en producción: Redis + PostgreSQL)
        self.intenciones: Dict[str, IntencionSubida] = {}
        self.usuarios_gps: Dict[str, UsuarioContexto] = {}
        self.lineas_cache: Dict[str, Linea] = {}
        self.paradas_cache: Dict[str, Parada] = {}
        self._cargar_datos()
    
    def _cargar_datos(self):
        """Carga líneas y paradas en cache"""
        HORARIOS, BASE = cargar()
        PARADAS = agrupar(BASE=BASE)
        
        for num, l in BASE.items():
            if l.get('valida', False) or l.get('real', False):
                linea = Linea(
                    num=num,
                    nombre=l.get('nombre', ''),
                    velocidad_kmh=l.get('velocidad_kmh', 30),
                    paradas=[Parada(**p) for p in l['paradas']],
                    trazado=l.get('trazado', [[]]),
                    duracion_min=l.get('duracion_min', {'ida': 45, 'vuelta': 48}),
                )
                self.lineas_cache[linea.num] = linea
        
        for p in PARADAS.values():
            self.paradas_cache[p.id] = p
    
    def get_linea(self, linea_id: str) -> Optional[Linea]:
        return self.lineas_cache.get(linea_id)
    
    def get_parada(self, parada_id: str) -> Optional[Parada]:
        return self.paradas_cache.get(parada_id)
    
    def get_usuario(self, usuario_id: str) -> UsuarioContexto:
        if usuario_id not in self.usuarios_gps:
            self.usuarios_gps[usuario_id] = UsuarioContexto(id=usuario_id)
        return self.usuarios_gps[usuario_id]
    
    # === ENDPOINTS ===
    
    def registrar_intencion(self, req: IntencionRequest) -> IntencionResponse:
        """POST /api/intencion - Usuario pulsa 'Me voy a subir'"""
        usuario = self.get_usuario(req.usuario_id)
        
        # Actualizar GPS usuario
        pos = PosicionGPS(
            lat=req.gps_lat, lon=req.gps_lon,
            timestamp=int(time.time()),
            accuracy=req.gps_accuracy,
            heading=req.gps_heading,
            speed=req.gps_speed / 3.6 if req.gps_speed else None,
        )
        usuario.posiciones.append(pos)
        usuario.gps_activo = True
        
        # Obtener línea y parada
        linea = self.get_linea(req.linea_id)
        parada = self.get_parada(req.parada_id)
        
        if not linea or not parada:
            return IntencionResponse(
                intencion_id="",
                habilitado=False,
                razones=["Línea o parada no encontrada"],
                ventana_min=None,
                estado="error",
                mensaje="Datos inválidos"
            )
        
        # Buscar próxima salida (simplificado: siguiente en 20-30 min)
        # En producción: consultar horarios reales
        proxima_salida = int(time.time()) + 1500  # 25 min
        
        # Validar botón
        validacion = self.validador.validar(usuario, parada, linea, proxima_salida)
        
        if not validacion['habilitado']:
            return IntencionResponse(
                intencion_id="",
                habilitado=False,
                razones=validacion['razones'],
                ventana_min=validacion.get('ventana_min'),
                estado="inhabilitado",
                mensaje="No cumples condiciones para pulsar el botón"
            )
        
        # Crear intención
        intencion = self.validador.crear_intencion(usuario, parada, linea, req.sentido)
        self.intenciones[intencion.id] = intencion
        
        # Registrar en inferencia colectiva
        self.inferencia.registrar_intencion(intencion)
        
        return IntencionResponse(
            intencion_id=intencion.id,
            habilitado=True,
            razones=[],
            ventana_min=validacion.get('ventana_min'),
            estado="pulsado",
            mensaje="Intención registrada. Monitoreando 2 min..."
        )
    
    def actualizar_ubicacion(self, req: UbicacionRequest) -> UbicacionResponse:
        """POST /api/ubicacion - Actualizar GPS (batch)"""
        usuario = self.get_usuario(req.usuario_id)
        
        pos = PosicionGPS(
            lat=req.lat, lon=req.lon,
            timestamp=req.timestamp or int(time.time()),
            accuracy=req.accuracy,
            heading=req.heading,
            speed=req.speed / 3.6 if req.speed else None,
        )
        usuario.posiciones.append(pos)
        usuario.gps_activo = True
        
        # Mantener solo últimas 50 posiciones
        if len(usuario.posiciones) > 50:
            usuario.posiciones = usuario.posiciones[-50:]
        
        # Si hay intención activa, monitorear
        if req.intencion_id and req.intencion_id in self.intenciones:
            intencion = self.intenciones[req.intencion_id]
            parada = self.get_parada(intencion.parada_id)
            linea = self.get_linea(intencion.linea_id)
            
            if parada and linea:
                # Motor de señales
                resultado_motor = self.motor_senales.evaluar(usuario, parada, linea)
                
                # Edge cases
                edge_cases = self.edge_cases.evaluar_todos(
                    usuario, parada, linea, resultado_motor, intencion
                )
                resultado_final = self.edge_cases.aplicar_mitigaciones(resultado_motor, edge_cases)
                
                # Monitor intención
                monitor_res = self.monitor.actualizar(intencion, usuario, parada, linea)
                
                # Determinar acción
                accion = 'continuar'
                mensaje = None
                
                if monitor_res.get('accion') == 'confirmar_subida':
                    accion = 'confirmar'
                    mensaje = f"¡Detectamos tu subida! ({monitor_res['motivo']})"
                    # Registrar subida en comunidad
                    self._registrar_subida_comunidad(usuario, intencion, monitor_res['motivo'])
                elif monitor_res.get('accion') == 'notificar':
                    accion = 'notificar'
                    mensaje = monitor_res.get('mensaje')
                elif monitor_res.get('accion') == 'expirar':
                    accion = 'descartar'
                    mensaje = "Tiempo agotado. ¿El bus no llegó?"
                
                # Edge case notificaciones
                for ec in edge_cases:
                    if ec.accion == 'notificar' and ec.mensaje_usuario:
                        if not mensaje:
                            mensaje = ec.mensaje_usuario
                
                return UbicacionResponse(
                    score=resultado_final['score'],
                    umbral=resultado_final['umbral'],
                    accion=accion,
                    mensaje=mensaje,
                    edge_cases=[ec.tipo.value for ec in edge_cases if ec.detectado]
                )
        
        # Sin intención activa - solo actualizar GPS
        return UbicacionResponse(
            score=0.0,
            umbral='sin_datos',
            accion='continuar',
            mensaje=None
        )
    
    def esta_el_bus(self, linea_id: str, sentido: str, parada_id: str) -> EstaElBusResponse:
        """GET /api/esta_el_bus - Inferencia colectiva"""
        sesion = self.inferencia.obtener_sesion(parada_id, linea_id, sentido)
        
        if not sesion:
            return EstaElBusResponse(
                bus_salio=None,
                proporcion_subidos=0.0,
                total_usuarios=0,
                mensaje="No hay usuarios esperando en esta parada",
                accion="esperar"
            )
        
        # Simular evaluación (en producción: usuarios reales)
        # Para demo, retornar estado actual
        if sesion.estado == EstadoColectivo.BUS_SALIO:
            return EstaElBusResponse(
                bus_salio=True,
                proporcion_subidos=0.75,
                total_usuarios=len(sesion.intenciones),
                mensaje="El bus ya salió (confirmado por mayoría)",
                accion="notificar_salio"
            )
        elif sesion.estado == EstadoColectivo.BUS_NO_SALIO:
            return EstaElBusResponse(
                bus_salio=False,
                proporcion_subidos=0.25,
                total_usuarios=len(sesion.intenciones),
                mensaje="El bus aún no ha salido",
                accion="esperar"
            )
        elif sesion.estado == EstadoColectivo.INCONCLUSIVO:
            return EstaElBusResponse(
                bus_salio=None,
                proporcion_subidos=0.0,
                total_usuarios=len(sesion.intenciones),
                mensaje="No hay suficientes datos GPS. ¿Puedes confirmar si el bus está aquí?",
                accion="pedir_confirmacion"
            )
        
        return EstaElBusResponse(
            bus_salio=None,
            proporcion_subidos=0.0,
            total_usuarios=len(sesion.intenciones),
            mensaje="Evaluando...",
            accion="esperar"
        )
    
    def obtener_eta(self, req: ETARequest) -> ETAFinalResponse:
        """GET /api/eta - ETA dinámico"""
        usuario = self.get_usuario(req.usuario_id)
        linea = self.get_linea(req.linea_id)
        origen = self.get_parada(req.parada_origen_id)
        destino = self.get_parada(req.parada_destino_id)
        
        if not all([linea, origen, destino]):
            return ETAFinalResponse(
                eta_segundos=0,
                eta_minutos=0,
                confianza=0.0,
                fuente='error',
                advertencias=["Datos incompletos"]
            )
        
        resultado = self.motor_eta.calcular_eta(linea, req.sentido, origen, destino)
        
        return ETAFinalResponse(
            eta_segundos=resultado.eta_final_seg,
            eta_minutos=round(resultado.eta_final_seg / 60),
            confianza=resultado.confianza,
            fuente='dinamico' if resultado.observaciones_usadas > 0 else 'base',
            advertencias=resultado.advertencias,
        )
    
    def confirmar_subida(self, req: SubidaRequest) -> SubidaResponse:
        """POST /api/subida - Confirmar subida (explícita o inferida)"""
        usuario = self.get_usuario(req.usuario_id)
        linea = self.get_linea(req.linea_id)
        parada = self.get_parada(req.parada_id)
        
        if not all([linea, parada]):
            return SubidaResponse(registrada=False, subida_id="", reputacion_actualizada=0)
        
        # Registrar en comunidad
        subida_id = self.comunidad.enviar_subida(
            dev=req.usuario_id,
            viaje=f"{req.linea_id}_{req.sentido}_{datetime.now().strftime('%Y-%m-%dT%H:%M')}",
            cluster=parada.id,
            linea=req.linea_id,
            sentido=req.sentido,
            variante=None,
            gps=True,
            pred_ts=int(time.time()) - 60,
            icio_ts=int(time.time()) - 300,
        )
        
        # Actualizar reputación
        disp = self.comunidad.dispositivos.get(req.usuario_id)
        rep_nueva = disp.reputacion if disp else 20.0
        
        # Registrar observación ETA
        self.motor_eta.registrar_desde_subida(
            usuario, linea, req.sentido, parada, parada,  # mismo origen/destino para demo
            int(time.time()), OrigenETA.USUARIO_GPS_CLARO, 1.0
        )
        
        return SubidaResponse(
            registrada=True,
            subida_id=subida_id,
            reputacion_actualizada=rep_nueva
        )
    
    def _registrar_subida_comunidad(self, usuario: UsuarioContexto, 
                                     intencion: IntencionSubida, motivo: str):
        """Registra subida en comunidad tras confirmación"""
        linea = self.get_linea(intencion.linea_id)
        parada = self.get_parada(intencion.parada_id)
        if linea and parada:
            self.comunidad.enviar_subida(
                dev=usuario.id,
                viaje=f"{linea.num}_{intencion.sentido}_{datetime.now().strftime('%Y-%m-%dT%H:%M')}",
                cluster=parada.id,
                linea=linea.num,
                sentido=intencion.sentido,
                variante=None,
                gps=True,
                pred_ts=intencion.ts_creacion,
                icio_ts=intencion.ts_creacion - 300,
            )


# === FastAPI App (para deploy en Render) ===

def create_fastapi_app():
    """Crea aplicación FastAPI lista para deploy"""
    from fastapi import FastAPI, HTTPException, Depends
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    from typing import Optional
    
    app = FastAPI(title="Guaguas FTV Subida API", version="1.0.0")
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    servicio = ServicioSubida()
    
    # Pydantic models para request/response
    class IntencionReq(BaseModel):
        usuario_id: str
        linea_id: str
        sentido: str
        parada_id: str
        gps_lat: float
        gps_lon: float
        gps_accuracy: float
        gps_speed: Optional[float] = None
        gps_heading: Optional[float] = None
    
    class IntencionResp(BaseModel):
        intencion_id: str
        habilitado: bool
        razones: List[str]
        ventana_min: Optional[float]
        estado: str
        mensaje: Optional[str] = None
    
    class UbicacionReq(BaseModel):
        usuario_id: str
        intencion_id: Optional[str]
        lat: float
        lon: float
        accuracy: float
        speed: Optional[float] = None
        heading: Optional[float] = None
        timestamp: Optional[int] = None
    
    class UbicacionResp(BaseModel):
        score: float
        umbral: str
        accion: str
        mensaje: Optional[str] = None
        edge_cases: Optional[List[str]] = None
    
    class EstaElBusResp(BaseModel):
        bus_salio: Optional[bool]
        proporcion_subidos: float
        total_usuarios: int
        mensaje: str
        accion: str
    
    class ETAReq(BaseModel):
        usuario_id: str
        linea_id: str
        sentido: str
        parada_origen_id: str
        parada_destino_id: str
    
    class ETAResp(BaseModel):
        eta_segundos: int
        eta_minutos: int
        confianza: float
        fuente: str
        advertencias: List[str]
    
    class SubidaReq(BaseModel):
        usuario_id: str
        intencion_id: Optional[str]
        linea_id: str
        sentido: str
        parada_id: str
        confirmada: bool
        origen: str
    
    class SubidaResp(BaseModel):
        registrada: bool
        subida_id: str
        reputacion_actualizada: float
    
    @app.post("/api/intencion", response_model=IntencionResp)
    def api_registrar_intencion(req: IntencionReq):
        return servicio.registrar_intencion(IntencionRequest(**req.dict()))
    
    @app.post("/api/ubicacion", response_model=UbicacionResp)
    def api_actualizar_ubicacion(req: UbicacionReq):
        return servicio.actualizar_ubicacion(UbicacionRequest(**req.dict()))
    
    @app.get("/api/esta_el_bus", response_model=EstaElBusResp)
    def api_esta_el_bus(linea_id: str, sentido: str, parada_id: str):
        return servicio.esta_el_bus(linea_id, sentido, parada_id)
    
    @app.get("/api/eta", response_model=ETAResp)
    def api_obtener_eta(
        usuario_id: str, linea_id: str, sentido: str,
        parada_origen_id: str, parada_destino_id: str
    ):
        return servicio.obtener_eta(ETARequest(
            usuario_id=usuario_id, linea_id=linea_id, sentido=sentido,
            parada_origen_id=parada_origen_id, parada_destino_id=parada_destino_id
        ))
    
    @app.post("/api/subida", response_model=SubidaResp)
    def api_confirmar_subida(req: SubidaReq):
        return servicio.confirmar_subida(SubidaRequest(**req.dict()))
    
    @app.get("/api/paradas")
    def api_paradas(linea_id: str = None):
        if linea_id and linea_id in servicio.lineas_cache:
            linea = servicio.lineas_cache[linea_id]
            return {
                'linea': linea.num,
                'paradas': [
                    {'id': p.id, 'nombre': p.n, 'lat': p.lat, 'lon': p.lon, 'km': p.km}
                    for p in linea.paradas
                ]
            }
        return {'paradas': {pid: {'id': p.id, 'nombre': p.n, 'lat': p.lat, 'lon': p.lon} 
                          for pid, p in servicio.paradas_cache.items()}}
    
    @app.get("/api/lineas")
    def api_lineas():
        return {
            'lineas': [
                {'num': l.num, 'nombre': l.nombre, 'velocidad_kmh': l.velocidad_kmh, 
                 'duracion_min': l.duracion_min, 'trazado_puntos': len(l.trazado[0]) if l.trazado else 0}
                for l in servicio.lineas_cache.values()
            ]
        }
    
    @app.get("/health")
    def health():
        return {"status": "ok", "timestamp": int(time.time())}
    
    return app


# === Flask App (alternativa) ===

def create_flask_app():
    """Crea aplicación Flask lista para deploy"""
    from flask import Flask, request, jsonify
    from flask_cors import CORS
    
    app = Flask(__name__)
    CORS(app)
    
    servicio = ServicioSubida()
    
    @app.route('/api/intencion', methods=['POST'])
    def api_registrar_intencion():
        data = request.get_json()
        req = IntencionRequest(**data)
        resp = servicio.registrar_intencion(req)
        return jsonify(asdict(resp))
    
    @app.route('/api/ubicacion', methods=['POST'])
    def api_actualizar_ubicacion():
        data = request.get_json()
        req = UbicacionRequest(**data)
        resp = servicio.actualizar_ubicacion(req)
        return jsonify(asdict(resp))
    
    @app.route('/api/esta_el_bus', methods=['GET'])
    def api_esta_el_bus():
        linea_id = request.args.get('linea_id')
        sentido = request.args.get('sentido')
        parada_id = request.args.get('parada_id')
        resp = servicio.esta_el_bus(linea_id, sentido, parada_id)
        return jsonify(asdict(resp))
    
    @app.route('/api/eta', methods=['GET'])
    def api_obtener_eta():
        req = ETARequest(
            usuario_id=request.args.get('usuario_id'),
            linea_id=request.args.get('linea_id'),
            sentido=request.args.get('sentido'),
            parada_origen_id=request.args.get('parada_origen_id'),
            parada_destino_id=request.args.get('parada_destino_id'),
        )
        resp = servicio.obtener_eta(req)
        return jsonify(asdict(resp))
    
    @app.route('/api/subida', methods=['POST'])
    def api_confirmar_subida():
        data = request.get_json()
        req = SubidaRequest(**data)
        resp = servicio.confirmar_subida(req)
        return jsonify(asdict(resp))
    
    @app.route('/api/paradas', methods=['GET'])
    def api_paradas():
        linea_id = request.args.get('linea_id')
        if linea_id and linea_id in servicio.lineas_cache:
            linea = servicio.lineas_cache[linea_id]
            return jsonify({
                'linea': linea.num,
                'paradas': [{'id': p.id, 'nombre': p.n, 'lat': p.lat, 'lon': p.lon, 'km': p.km} for p in linea.paradas]
            })
        return jsonify({pid: {'id': p.id, 'nombre': p.n, 'lat': p.lat, 'lon': p.lon} for pid, p in servicio.paradas_cache.items()})
    
    @app.route('/api/lineas', methods=['GET'])
    def api_lineas():
        return jsonify({
            'lineas': [{'num': l.num, 'nombre': l.nombre, 'velocidad_kmh': l.velocidad_kmh, 
                       'duracion_min': l.duracion_min} for l in servicio.lineas_cache.values()]
        })
    
    @app.route('/health', methods=['GET'])
    def health():
        return jsonify({"status": "ok", "timestamp": int(time.time())})
    
    return app


if __name__ == '__main__':
    # Para testing local
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'flask':
        app = create_flask_app()
        app.run(host='0.0.0.0', port=5000, debug=True)
    else:
        print("Uso: python api_sube.py flask")
        print("Para FastAPI: uvicorn api_sube:create_fastapi_app --factory --host 0.0.0.0 --port 5000")