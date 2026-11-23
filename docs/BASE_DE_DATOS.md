# Base de datos (Supabase)

La comunidad ("me he subido a esta guagua") se guarda en PostgreSQL en **Supabase**, proyecto
`akmlkautrbrxpppggwim`. El servidor de Render se conecta con la variable `DATABASE_URL` y crea/actualiza las tablas
solo al arrancar (`app/db.py`). Los horarios y las rutas NO están aquí: son archivos de `datos/`.

## Cómo verla y gestionarla

1. Entra en <https://supabase.com/dashboard> y abre el proyecto.
2. **Table Editor** (icono de tabla, menú izquierdo): cada tabla como una hoja de cálculo. Puedes filtrar, ordenar
   y ver filas. Para mirar, perfecto; para cambiar datos, mejor con cuidado (no hay "deshacer").
3. **SQL Editor**: para consultas. Abajo tienes algunas listas para copiar.
4. **Advisors → Security Advisor**: Supabase avisa de problemas de seguridad. Tras el despliegue de
   `seguridad` no debería quejarse de "RLS disabled": el servidor activa Row Level Security en todas las tablas.
5. **Database → Backups**: en el plan gratuito no hay copias que puedas descargar. Para una copia manual:
   SQL Editor → ejecuta un `SELECT * FROM <tabla>` → botón *Export* (CSV).

> Ojo: el plan gratuito **pausa el proyecto tras 7 días sin actividad**. Mientras la web tenga visitas no pasa, pero
> si alguna vez la web dice que no puede conectar, mira en el panel si el proyecto está pausado y pulsa *Restore*.

## Las tablas

| Tabla | Qué guarda | Cuánto dura |
|---|---|---|
| `dispositivos` | Un móvil/navegador que usa la app: su clave pública, reputación (`rep` 0–100), avisos hechos (`subidas`), confirmados por otros (`confirmadas`), discrepantes, bloqueos por avisos imposibles, token de notificaciones (`fcm_token`) | Siempre |
| `subidas` | Cada aviso "me he subido": dispositivo, viaje (`fecha\|línea\|sentido\|hora\|variante`), parada (`cluster`), hora del aviso (`ts`) y la prevista (`pred_ts`), peso del aviso, si tenía GPS, estado (`pendiente`; luego `confirmada` si coinciden varias personas o `sin_confirmar` a las 2 h) | 90 días |
| `observaciones` | Pasos de guagua **confirmados** por varias personas (lo que corrige los horarios en vivo y con lo que se aprende) | Siempre |
| `registros` | Altas de dispositivos por red, para limitar abusos. Guarda una **huella** de la IP (no la IP) | 2 días |
| `nonces` | Firmas ya usadas (impide repetir una petición capturada) | 10 minutos |
| `ubicaciones_activas` | Última posición de quien está esperando una guagua | 5 minutos |

Las horas `ts`, `pred_ts`, `inicio_ts` son segundos Unix: en SQL se ven con `to_timestamp(ts)`.

## Consultas útiles (SQL Editor)

```sql
-- Resumen
SELECT (SELECT count(*) FROM dispositivos)  AS dispositivos,
       (SELECT count(*) FROM subidas)       AS avisos_90_dias,
       (SELECT count(*) FROM observaciones) AS pasos_confirmados;

-- Altas de dispositivos por día
SELECT date(creado) AS dia, count(*) FROM dispositivos GROUP BY 1 ORDER BY 1 DESC;

-- Últimos avisos, con hora legible y retraso respecto al horario (min)
SELECT to_timestamp(ts) AS cuando, linea, sentido, cluster AS parada, estado, gps,
       round((ts - pred_ts) / 60.0) AS retraso_min
FROM subidas ORDER BY ts DESC LIMIT 50;

-- Retraso medio confirmado por línea
SELECT linea, count(*) AS pasos, round(avg(ts - pred_ts) / 60.0, 1) AS retraso_medio_min
FROM observaciones GROUP BY linea ORDER BY pasos DESC;

-- Usuarios más fiables
SELECT left(id, 8) AS dispositivo, round(rep) AS reputacion, subidas, confirmadas, discrepantes
FROM dispositivos ORDER BY rep DESC LIMIT 20;

-- Dispositivos pausados por avisos imposibles
SELECT left(id, 8) AS dispositivo, strikes, bloqueado FROM dispositivos WHERE bloqueado > now();
```

## Cosas que NO hacer

- No borres `dispositivos` a mano: sus avisos dependen de ellos (clave foránea) y el usuario perdería su reputación.
- No desactives Row Level Security ni crees políticas "públicas": la app no las necesita (todo pasa por el servidor).
- No pongas la clave *secret*/*service_role* en la web ni en la app: da acceso total a la base de datos.
