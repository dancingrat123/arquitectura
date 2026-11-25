from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.responses import Response
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel
import psycopg2
import psycopg2.pool
from psycopg2.extras import Json, RealDictCursor
import httpx
import pika
import csv
import io
import json
import math
import os
import re
import threading
import time
import unicodedata
from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import Depends
from fastapi.security import OAuth2PasswordRequestForm
import auth as auth_mod
from auth import (
    create_access_token, get_current_user, require_rol, ACCESS_TOKEN_EXPIRE_MINUTES,
)
from shared.riesgo import (
    ADVERTENCIA_RIESGO,
    ADVERTENCIA_VENTANA,
    FEATURES_CLIMA,
    componer_explicacion,
)
from datetime import timedelta
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

app = FastAPI(title="API Service - SIRIF")

DATABASE_URL = os.getenv("DATABASE_URL")


class SimulacionRequest(BaseModel):
    zona: str
    ajustes: dict[str, float] = {}


class EstadoAlertaRequest(BaseModel):
    estado: str


class UsuarioCreateRequest(BaseModel):
    username: str
    rol: str
    password: str
    nombre_completo: str | None = None


class UsuarioPatchRequest(BaseModel):
    rol: str | None = None
    password: str | None = None
    activo: bool | None = None
    nombre_completo: str | None = None


class InfraestructuraRequest(BaseModel):
    tipo: str
    nombre: str | None = None
    descripcion: str | None = None
    zona: str | None = None
    direccion: str | None = None
    latitud: float | None = None
    longitud: float | None = None
    expuestos: int | None = None
    fuente: str | None = None


class ObservacionRequest(BaseModel):
    zona: str
    tipo: str
    descripcion: str
    severidad: str = "media"
    latitud: float | None = None
    longitud: float | None = None


class AccionRequest(BaseModel):
    zona: str
    descripcion: str
    tipo: str | None = None
    responsable: str | None = None
    fecha_programada: str | None = None
    prioridad: str = "media"
    observacion_relacionada: int | None = None


class AccionPatchRequest(BaseModel):
    estado: str | None = None
    responsable: str | None = None
    prioridad: str | None = None
    fecha_ejecucion: str | None = None


class AsistenteRequest(BaseModel):
    pregunta: str


DATA_SERVICE_URL = os.getenv("DATA_SERVICE_URL")
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN")
RISK_SERVICE_URL = os.getenv("RISK_SERVICE_URL")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST")
RABBITMQ_USER = os.getenv("RABBITMQ_USER")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS")

GEOJSON_PATH = os.getenv("GEOJSON_PATH", "")
LLM_URL = os.getenv("LLM_URL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# Probabilidad a partir de la cual se genera una alerta de umbral (Fase 2).
# El modelo comunal calibra a una escala mucho menor (prevalencia ~3 %), por lo
# que su umbral de alerta es distinto al del regional.
UMBRAL_ALERTA = float(os.getenv("UMBRAL_ALERTA", "0.50"))
UMBRAL_ALERTA_COMUNAL = float(os.getenv("UMBRAL_ALERTA_COMUNAL", "0.10"))
ADVERTENCIA_SIMULACION = (
    "Simulación sobre el modelo regional; no modifica el historial real."
)

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Pool perezoso: importar la app (tests) no abre conexiones."""
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL)
    return _pool


def get_db():
    return get_pool().getconn()


def liberar_db(conn) -> None:
    try:
        get_pool().putconn(conn)
    except Exception as error:  # noqa: BLE001
        print(f"[API] No se pudo devolver la conexión al pool: {error}", flush=True)


@contextmanager
def cursor_db(commit: bool = False):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        liberar_db(conn)


def _json_seguro(valor, por_defecto):
    if valor is None:
        return por_defecto
    if isinstance(valor, str):
        try:
            return json.loads(valor)
        except (TypeError, ValueError):
            return por_defecto
    return valor


def _iso(valor):
    """Serializa fechas UTC de PostgreSQL en ISO-8601 con sufijo Z."""
    if isinstance(valor, datetime):
        return valor.isoformat() + "Z"
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    return None


def _tipo_estimacion(version_modelo) -> str:
    if str(version_modelo or "").startswith("rf-comunal"):
        return "riesgo_meteorologico_comunal"
    return "riesgo_meteorologico_regional"


def serializar_zona(fila) -> dict:
    if isinstance(fila, dict):
        fila = (
            fila.get("zona"), fila.get("nivel_riesgo"), fila.get("puntaje"),
            fila.get("puntaje_maximo"), fila.get("fecha_evaluacion"),
            fila.get("version_modelo"), fila.get("comuna"), fila.get("codigo_comuna"),
            fila.get("fuente"), fila.get("fecha_medicion"),
            fila.get("antiguedad_minutos"), fila.get("probabilidad"),
            fila.get("fuera_ventana_estacional"), fila.get("factores_predominantes"),
            fila.get("origen"), fila.get("usuario"),
        )
    (zona, nivel_riesgo, puntaje, puntaje_maximo, fecha_evaluacion, version_modelo,
     comuna, codigo_comuna, fuente, fecha_medicion, antiguedad_minutos,
     probabilidad, fuera_ventana_estacional) = fila[:13]
    factores = _json_seguro(fila[13], []) if len(fila) > 13 else []
    origen = fila[14] if len(fila) > 14 else None
    usuario = fila[15] if len(fila) > 15 else None
    return {
        "zona": zona,
        "nivel_riesgo": nivel_riesgo,
        "puntaje": puntaje,
        "puntaje_maximo": puntaje_maximo,
        "fecha_evaluacion": _iso(fecha_evaluacion),
        "version_modelo": version_modelo,
        "comuna": comuna,
        "codigo_comuna": codigo_comuna,
        "fuente": fuente or "desconocida",
        "fecha_medicion": _iso(fecha_medicion),
        "antiguedad_minutos": round(float(antiguedad_minutos), 1) if antiguedad_minutos is not None else None,
        "probabilidad": None if probabilidad is None else float(probabilidad),
        "fuera_ventana_estacional": bool(fuera_ventana_estacional) if fuera_ventana_estacional is not None else False,
        "factores_predominantes": factores if isinstance(factores, list) else [],
        "origen": origen or "automatico",
        "usuario": usuario,
        "tipo_estimacion": _tipo_estimacion(version_modelo),
    }


# ---------------------------------------------------------------------------
# Consultas analíticas (Fase 1)
# ---------------------------------------------------------------------------


def consultar_ultimas_zonas(
    limite_por_zona: int = 1,
    zonas: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Últimas `n` evaluaciones por zona, con datos comunales."""
    filtro = ""
    parametros: list[Any] = []
    if zonas:
        filtro = "WHERE r.zona = ANY(%s)"
        parametros.append(list(zonas))
    parametros.append(max(1, int(limite_por_zona)))
    with cursor_db() as cur:
        cur.execute(
            f"""
            SELECT * FROM (
                SELECT r.zona, r.nivel_riesgo, r.probabilidad, r.puntaje,
                       r.fecha_evaluacion, r.version_modelo, r.usuario, r.origen,
                       r.factores_predominantes, r.variables, r.fuera_ventana_estacional,
                       z.comuna, z.codigo_comuna,
                       row_number() OVER (
                           PARTITION BY r.zona
                           ORDER BY r.fecha_evaluacion DESC, r.id_resultado DESC
                       ) AS rn
                FROM resultado_riesgo r
                LEFT JOIN comuna_zona z ON z.slug = r.zona
                {filtro}
            ) sub
            WHERE rn <= %s
            ORDER BY zona, rn
            """,
            parametros,
        )
        return [dict(fila) for fila in cur.fetchall()]


def consultar_historial(
    zona: str,
    desde: str | None = None,
    hasta: str | None = None,
    limite: int = 100,
) -> list[dict[str, Any]]:
    condiciones = ["r.zona = %s"]
    parametros: list[Any] = [zona]
    if desde:
        condiciones.append("r.fecha_evaluacion >= %s")
        parametros.append(desde)
    if hasta:
        condiciones.append("r.fecha_evaluacion <= %s")
        parametros.append(hasta)
    parametros.append(max(1, min(int(limite or 100), 1000)))
    with cursor_db() as cur:
        cur.execute(
            f"""
            SELECT r.zona, r.nivel_riesgo, r.probabilidad, r.puntaje,
                   r.fecha_evaluacion, r.version_modelo, r.usuario, r.origen,
                   r.factores_predominantes
            FROM resultado_riesgo r
            WHERE {' AND '.join(condiciones)}
            ORDER BY r.fecha_evaluacion DESC, r.id_resultado DESC
            LIMIT %s
            """,
            parametros,
        )
        return [dict(fila) for fila in cur.fetchall()]


def _validar_fecha_param(valor: str | None, nombre: str) -> str | None:
    if not valor:
        return None
    try:
        datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail=f"'{nombre}' debe ser una fecha ISO-8601"
        ) from error
    return valor


def _serializar_historial(fila: dict[str, Any]) -> dict[str, Any]:
    factores = _json_seguro(fila.get("factores_predominantes"), [])
    return {
        "fecha_evaluacion": _iso(fila.get("fecha_evaluacion")),
        "nivel_riesgo": fila.get("nivel_riesgo"),
        "probabilidad": fila.get("probabilidad"),
        "puntaje": fila.get("puntaje"),
        "version_modelo": fila.get("version_modelo"),
        "usuario": fila.get("usuario"),
        "origen": fila.get("origen") or "automatico",
        "factores_predominantes": factores if isinstance(factores, list) else [],
    }


# ---------------------------------------------------------------------------
# Simulador aislado (Fase 2)
# ---------------------------------------------------------------------------


def _features_desde_variables(variables: dict[str, Any]) -> dict[str, float]:
    temperatura = float(variables.get("temperatura") or 0.0)
    humedad = float(variables.get("humedad") or 0.0)
    viento = float(variables.get("viento") or 0.0)
    precipitacion = float(variables.get("precipitacion") or 0.0)
    return {
        "TEMP_MED": temperatura,
        "TEMP_MAX": temperatura,
        "HUMEDAD_REL": humedad,
        "VEL_VIENTO": viento,
        "PRECIPITACION": precipitacion,
        "precip_7d": precipitacion,
        "temp_media_7d": temperatura,
        "humedad_7d": humedad,
        "precip_30d": precipitacion,
    }


def recalcular_features_simulacion(
    features_base: dict[str, Any] | None,
    ajustes: dict[str, Any] | None,
) -> dict[str, float]:
    """Aplica la regla de rodadura 1/7 sobre las features persistidas.

    Se sobrescriben las variables instantáneas y las medias móviles se mueven
    un séptimo hacia el escenario; la precipitación acumulada se ajusta por la
    diferencia. No se escribe nada en `resultado_riesgo`.
    """
    base = {
        feature: float((features_base or {}).get(feature) or 0.0)
        for feature in FEATURES_CLIMA
    }
    ajustes = ajustes or {}
    temperatura = float(ajustes.get("temperatura", base["TEMP_MED"]))
    humedad = float(ajustes.get("humedad", base["HUMEDAD_REL"]))
    viento = float(ajustes.get("viento", base["VEL_VIENTO"]))
    precipitacion = float(ajustes.get("precipitacion", base["PRECIPITACION"]))

    features = dict(base)
    features["TEMP_MED"] = temperatura
    features["TEMP_MAX"] = temperatura
    features["HUMEDAD_REL"] = humedad
    features["VEL_VIENTO"] = viento
    features["PRECIPITACION"] = precipitacion
    features["temp_media_7d"] = base["temp_media_7d"] + (temperatura - base["TEMP_MED"]) / 7.0
    features["humedad_7d"] = base["humedad_7d"] + (humedad - base["HUMEDAD_REL"]) / 7.0
    features["precip_7d"] = base["precip_7d"] + (precipitacion - base["PRECIPITACION"])
    features["precip_30d"] = base["precip_30d"] + (precipitacion - base["PRECIPITACION"])
    return {feature: round(float(valor), 4) for feature, valor in features.items()}


def _leer_evaluacion_para_simular(zona: str) -> dict[str, Any] | None:
    with cursor_db() as cur:
        cur.execute(
            """
            SELECT r.zona, r.nivel_riesgo, r.probabilidad, r.puntaje, r.features,
                   r.variables, r.factores_predominantes, r.fuera_ventana_estacional,
                   r.version_modelo, z.id_zona, z.region, z.comuna
            FROM resultado_riesgo r
            LEFT JOIN comuna_zona z ON z.slug = r.zona
            WHERE r.zona = %s
            ORDER BY r.fecha_evaluacion DESC, r.id_resultado DESC
            LIMIT 1
            """,
            (zona,),
        )
        fila = cur.fetchone()
    return dict(fila) if fila else None


def _clasificar_escenario(
    zona: str,
    features: dict[str, float],
    variables: dict[str, Any],
    region: str | None,
    comuna: str | None = None,
) -> dict[str, Any]:
    payload = {
        **variables,
        "features": features,
        "fecha_medicion": datetime.now(timezone.utc).isoformat(),
        "region": region,
        "comuna": comuna,
    }
    with httpx.Client(timeout=10.0) as client:
        resultado = client.post(f"{RISK_SERVICE_URL}/clasificar", json=payload).json()
    if "error" in resultado:
        raise HTTPException(status_code=502, detail=f"risk-service: {resultado['error']}")
    return resultado


def _persistir_simulacion(datos: dict[str, Any]) -> int:
    with cursor_db(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO simulacion
                (id_zona, zona, usuario, escenario, variables_base, features_base,
                 probabilidad_original, nivel_original, probabilidad_simulada,
                 nivel_simulado, delta_probabilidad, factores_predominantes,
                 fuera_ventana_estacional)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_simulacion
            """,
            (
                datos.get("id_zona"),
                datos["zona"],
                datos.get("usuario"),
                Json(datos["escenario"]),
                Json(datos.get("variables_base") or {}),
                Json(datos.get("features_base") or {}),
                datos.get("probabilidad_original"),
                datos.get("nivel_original"),
                datos.get("probabilidad_simulada"),
                datos.get("nivel_simulado"),
                datos.get("delta_probabilidad"),
                Json(datos.get("factores_predominantes") or []),
                datos.get("fuera_ventana_estacional"),
            ),
        )
        fila = cur.fetchone()
    return fila["id_simulacion"]


# ---------------------------------------------------------------------------
# Recomendaciones deterministas (Fase 2)
# ---------------------------------------------------------------------------


def construir_recomendaciones(
    nivel: str | None,
    factores: list[dict[str, Any]] | None,
    variables: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Reglas fijas en español; la prioridad se asigna por orden de importancia."""
    variables = variables or {}
    factores = factores or []
    variables_factor = {
        factor.get("variable") for factor in factores if isinstance(factor, dict)
    }
    try:
        viento = float(variables.get("viento") or 0.0)
    except (TypeError, ValueError):
        viento = 0.0
    try:
        humedad = float(variables.get("humedad") or 0.0)
    except (TypeError, ValueError):
        humedad = 0.0
    try:
        temperatura = float(variables.get("temperatura") or 0.0)
    except (TypeError, ValueError):
        temperatura = 0.0

    hay_viento = "VEL_VIENTO" in variables_factor or viento >= 25
    hay_temperatura = bool({"TEMP_MAX", "TEMP_MED"} & variables_factor) or temperatura >= 32
    hay_humedad = "HUMEDAD_REL" in variables_factor or (humedad > 0 and humedad <= 25)
    vegetacion_alta = str(variables.get("vegetacion") or "").lower() == "alta"

    candidatas: list[dict[str, str]] = []
    if nivel == "ALTO":
        if hay_viento:
            candidatas.append(
                {
                    "codigo": "ALTO_VIENTO",
                    "categoria": "prevencion",
                    "texto": "Restringir uso de fuego y maquinaria en zonas expuestas al viento.",
                }
            )
        if hay_temperatura:
            candidatas.append(
                {
                    "codigo": "ALTO_TEMP",
                    "categoria": "prevencion",
                    "texto": "Reforzar la vigilancia en horas de máxima temperatura y evitar actividades que generen chispas.",
                }
            )
        if vegetacion_alta:
            candidatas.append(
                {
                    "codigo": "ALTO_VEGETACION",
                    "categoria": "mitigacion",
                    "texto": "Programar limpieza de vegetación seca en quebradas y bordes de camino.",
                }
            )
    elif nivel == "MEDIO":
        if hay_viento or hay_humedad:
            candidatas.append(
                {
                    "codigo": "MEDIO_VIGILANCIA",
                    "categoria": "prevencion",
                    "texto": "Mantener vigilancia y monitorear cambios de viento y humedad durante la jornada.",
                }
            )
    elif nivel == "BAJO":
        candidatas.append(
            {
                "codigo": "BAJO_MANTENCION",
                "categoria": "mantencion",
                "texto": "Mantener las medidas preventivas y revisar la evolución del riesgo meteorológico.",
            }
        )

    return [
        {**candidata, "prioridad": indice + 1}
        for indice, candidata in enumerate(candidatas)
    ]


# ---------------------------------------------------------------------------
# Serializadores de alertas, notificaciones y estado (Fase 2)
# ---------------------------------------------------------------------------


def _serializar_alerta(fila: dict[str, Any]) -> dict[str, Any]:
    return {
        "id_alerta": fila.get("id_alerta"),
        "zona": fila.get("zona"),
        "tipo": fila.get("tipo"),
        "nivel_anterior": fila.get("nivel_anterior"),
        "nivel_nuevo": fila.get("nivel_nuevo"),
        "probabilidad": None if fila.get("probabilidad") is None else float(fila["probabilidad"]),
        "umbral": None if fila.get("umbral") is None else float(fila["umbral"]),
        "mensaje": fila.get("mensaje"),
        "estado": fila.get("estado") or "activa",
        "created_at": _iso(fila.get("created_at")),
    }


def _serializar_notificacion(fila: dict[str, Any]) -> dict[str, Any]:
    return {
        "id_notificacion": fila.get("id_notificacion"),
        "tipo": fila.get("tipo"),
        "titulo": fila.get("titulo"),
        "mensaje": fila.get("mensaje"),
        "zona": fila.get("zona"),
        "nivel": fila.get("nivel"),
        "leida": bool(fila.get("leida")),
        "created_at": _iso(fila.get("created_at")),
    }


def _serializar_estado_servicio(fila: dict[str, Any]) -> dict[str, Any]:
    detalle = _json_seguro(fila.get("detalle"), None)
    datos = {
        "servicio": fila.get("servicio"),
        "estado": fila.get("estado"),
        "latencia_ms": fila.get("latencia_ms"),
        "ultima_comprobacion": _iso(fila.get("ultima_comprobacion")),
    }
    if isinstance(detalle, dict):
        datos.update(detalle)
    elif detalle is not None:
        datos["detalle"] = detalle
    return datos


def conectar_con_reintentos(max_intentos=10, espera=5):
    for intento in range(1, max_intentos + 1):
        try:
            return pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBITMQ_HOST,
                    credentials=pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS),
                    heartbeat=600,
                    blocked_connection_timeout=300
                )
            )
        except pika.exceptions.AMQPConnectionError as e:
            print(f"[API] Intento {intento}/{max_intentos}: {e}", flush=True)
            if intento < max_intentos:
                time.sleep(espera)
            else:
                raise


def publicar_evento(connection, routing_key: str, evento: dict):
    try:
        channel = connection.channel()
        channel.exchange_declare(exchange="sirif_eventos", exchange_type="topic", durable=True)
        channel.basic_publish(
            exchange="sirif_eventos",
            routing_key=routing_key,
            body=json.dumps(evento),
            properties=pika.BasicProperties(delivery_mode=2)
        )
    except Exception as e:
        print(f"[API] Error publicando: {e}", flush=True)


def publicar_evento_seguro(routing_key: str, evento: dict) -> None:
    """Publica un evento abriendo (y cerrando) una conexión propia."""
    conn_mq = None
    try:
        conn_mq = conectar_con_reintentos()
        publicar_evento(conn_mq, routing_key, evento)
    except Exception as e:  # noqa: BLE001
        print(f"[API] No se pudo publicar {routing_key}: {e}", flush=True)
    finally:
        if conn_mq is not None:
            try:
                conn_mq.close()
            except Exception:  # noqa: BLE001
                pass


def consumir_monitoreo():
    """Escucha eventos 'monitoreo.zona' y dispara inferencia"""
    print("[API] Iniciando consumidor de monitoreo...", flush=True)

    connection = conectar_con_reintentos()
    channel = connection.channel()
    channel.exchange_declare(exchange="sirif_eventos", exchange_type="topic", durable=True)

    result = channel.queue_declare(queue="api_monitoreo_queue", durable=True)
    queue_name = result.method.queue
    channel.queue_bind(
        exchange="sirif_eventos",
        queue=queue_name,
        routing_key="monitoreo.zona"
    )

    def callback(ch, method, properties, body):
        try:
            evento = json.loads(body)
            zona = evento["zona"]
            print(f"[API] Evento monitoreo recibido: {zona}", flush=True)

            # Forzar inferencia (ignora caché)
            inferir_y_guardar(zona)

            ch.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as e:
            print(f"[API] Error procesando evento: {e}", flush=True)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=callback)
    channel.start_consuming()


def insertar_resultado_riesgo(cur, datos: dict[str, Any]) -> None:
    """Inserta un snapshot en `resultado_riesgo` (único punto de escritura)."""
    cur.execute(
        """
        INSERT INTO resultado_riesgo
            (id_zona, zona, nivel_riesgo, puntaje, puntaje_maximo, version_modelo,
             probabilidad, fuera_ventana_estacional, features, variables,
             factores_predominantes, origen, usuario)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            datos.get("id_zona"),
            datos["zona"],
            datos["nivel_riesgo"],
            datos["puntaje"],
            datos["puntaje_maximo"],
            datos.get("version_modelo", "v1.0"),
            datos.get("probabilidad"),
            datos.get("fuera_ventana_estacional", False),
            Json(datos.get("features") or {}),
            Json(datos.get("variables") or {}),
            Json(datos.get("factores_predominantes") or []),
            datos.get("origen", "automatico"),
            datos.get("usuario"),
        ),
    )


# ---------------------------------------------------------------------------
# Alertas inteligentes (Fase 2)
# ---------------------------------------------------------------------------


def umbral_alerta_para(version_modelo: str | None) -> float:
    """Umbral de alerta según la escala de probabilidad del modelo.

    El modelo comunal (`rf-comunal-*`) opera en una escala ~0.0–0.1, así que
    usar el umbral regional (0.50) lo dejaría sin alertas de tipo `umbral`.
    """
    if str(version_modelo or "").startswith("rf-comunal"):
        return UMBRAL_ALERTA_COMUNAL
    return UMBRAL_ALERTA


def construir_alertas(
    zona: str,
    nivel_previo: str | None,
    nivel_nuevo: str | None,
    prob_previa: float | None,
    prob_nueva: float | None,
    umbral: float | None = None,
) -> list[dict[str, Any]]:
    """Reglas deterministas de alerta: cambio de nivel y umbral de probabilidad."""
    umbral = UMBRAL_ALERTA if umbral is None else float(umbral)
    alertas: list[dict[str, Any]] = []
    if nivel_previo and nivel_nuevo and nivel_previo != nivel_nuevo:
        alertas.append(
            {
                "tipo": "cambio_nivel",
                "nivel_anterior": nivel_previo,
                "nivel_nuevo": nivel_nuevo,
                "probabilidad": prob_nueva,
                "umbral": None,
                "mensaje": (
                    f"{zona.capitalize()} cambió de {nivel_previo} a {nivel_nuevo}."
                ),
            }
        )
    if (
        nivel_previo is not None
        and prob_nueva is not None
        and float(prob_nueva) >= umbral
        and (prob_previa is None or float(prob_previa) < umbral)
    ):
        alertas.append(
            {
                "tipo": "umbral",
                "nivel_anterior": nivel_previo,
                "nivel_nuevo": nivel_nuevo,
                "probabilidad": float(prob_nueva),
                "umbral": umbral,
                "mensaje": (
                    f"{zona.capitalize()} superó el umbral de alerta "
                    f"({round(umbral * 100)} % de probabilidad)."
                ),
            }
        )
    return alertas


def _insertar_alerta(zona: str, id_zona: int | None, alerta: dict[str, Any]) -> dict[str, Any]:
    with cursor_db(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO alerta
                (id_zona, zona, tipo, nivel_anterior, nivel_nuevo, probabilidad,
                 umbral, mensaje)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_alerta, created_at
            """,
            (
                id_zona,
                zona,
                alerta["tipo"],
                alerta.get("nivel_anterior"),
                alerta.get("nivel_nuevo"),
                alerta.get("probabilidad"),
                alerta.get("umbral"),
                alerta["mensaje"],
            ),
        )
        fila = cur.fetchone()
    return {
        **alerta,
        "id_alerta": fila["id_alerta"],
        "zona": zona,
        "created_at": _iso(fila["created_at"]),
    }


def _publicar_alerta_generada(alerta: dict[str, Any]) -> None:
    evento = {
        "id_alerta": alerta.get("id_alerta"),
        "zona": alerta.get("zona"),
        "tipo": alerta.get("tipo"),
        "nivel_anterior": alerta.get("nivel_anterior"),
        "nivel_nuevo": alerta.get("nivel_nuevo"),
        "probabilidad": alerta.get("probabilidad"),
        "mensaje": alerta.get("mensaje"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    publicar_evento_seguro("alerta.generada", evento)


def detectar_alertas(
    zona: str,
    id_zona: int | None,
    nivel_previo: str | None,
    nivel_nuevo: str | None,
    prob_previa: float | None,
    prob_nueva: float | None,
    insertar=None,
    publicar=None,
    umbral: float | None = None,
) -> list[dict[str, Any]]:
    """Persiste las alertas deterministas y publica `alerta.generada`."""
    candidatas = construir_alertas(
        zona, nivel_previo, nivel_nuevo, prob_previa, prob_nueva, umbral=umbral
    )
    if not candidatas:
        return []
    insertar = insertar or _insertar_alerta
    publicar = publicar or _publicar_alerta_generada
    registradas = []
    for candidata in candidatas:
        registrada = insertar(zona, id_zona, candidata)
        registradas.append(registrada)
        try:
            publicar(registrada)
        except Exception as error:  # noqa: BLE001
            print(f"[API] No se pudo publicar alerta.generada: {error}", flush=True)
    return registradas


def inferir_y_guardar(
    zona: str,
    origen: str = "automatico",
    usuario: str | None = None,
):
    """Fuerza inferencia con datos reales y guarda un snapshot en el historial."""
    with httpx.Client(timeout=10.0) as client:
        datos = client.get(
            f"{DATA_SERVICE_URL}/datos/{zona}",
            headers={"X-Internal-Token": INTERNAL_TOKEN} if INTERNAL_TOKEN else {},
        ).json()
        if "variables" not in datos:
            raise RuntimeError(f"data-service no devolvió variables para '{zona}': {datos}")

        meta = datos.get("meta") or {}
        payload = {
            **datos["variables"],
            "features": datos.get("features"),
            "fecha_medicion": meta.get("fecha_medicion"),
            "region": datos.get("region"),
            "comuna": datos.get("comuna"),
        }
        resultado = client.post(f"{RISK_SERVICE_URL}/clasificar", json=payload).json()

    if "error" in resultado:
        raise RuntimeError(f"risk-service: {resultado['error']}")

    factores = resultado.get("factores_predominantes") or []

    # Snapshot completo del historial (9 features + variables + factores).
    with cursor_db(commit=True) as cur:
        cur.execute(
            "SELECT id_zona FROM comuna_zona WHERE slug = %s OR lower(comuna) = lower(%s) LIMIT 1",
            (zona, zona),
        )
        fila = cur.fetchone()
        id_zona = fila["id_zona"] if fila else None
        cur.execute(
            """
            SELECT nivel_riesgo, probabilidad
            FROM resultado_riesgo
            WHERE zona = %s
            ORDER BY fecha_evaluacion DESC, id_resultado DESC
            LIMIT 1
            """,
            (zona,),
        )
        previa = cur.fetchone()
        insertar_resultado_riesgo(
            cur,
            {
                "id_zona": id_zona,
                "zona": zona,
                "nivel_riesgo": resultado["nivel_riesgo"],
                "puntaje": resultado["puntaje"],
                "puntaje_maximo": resultado["puntaje_maximo"],
                "version_modelo": resultado.get("version_modelo", "v1.0"),
                "probabilidad": resultado.get("probabilidad"),
                "fuera_ventana_estacional": resultado.get("fuera_ventana_estacional", False),
                "features": resultado.get("factores_considerados") or {},
                "variables": datos.get("variables") or {},
                "factores_predominantes": factores,
                "origen": origen,
                "usuario": usuario,
            },
        )

    try:
        detectar_alertas(
            zona,
            id_zona,
            previa.get("nivel_riesgo") if previa else None,
            resultado["nivel_riesgo"],
            previa.get("probabilidad") if previa else None,
            resultado.get("probabilidad"),
            umbral=umbral_alerta_para(resultado.get("version_modelo")),
        )
    except Exception as error:  # noqa: BLE001
        print(f"[API] Error detectando alertas de {zona}: {error}", flush=True)

    # Publicar evento riesgo.calculado enriquecido (aditivo para consumidores).
    evento = {
        "zona": zona,
        "nivel_riesgo": resultado["nivel_riesgo"],
        "puntaje": resultado["puntaje"],
        "probabilidad": resultado.get("probabilidad"),
        "version_modelo": resultado.get("version_modelo", "v1.0"),
        "fuente_datos": meta.get("fuente"),
        "usuario": usuario,
        "origen": origen,
        "factores_predominantes": factores,
        "explicacion": resultado.get("explicacion"),
        "timestamp": datetime.now().isoformat(),
    }
    publicar_evento_seguro("riesgo.calculado", evento)


@app.on_event("startup")
def startup_event():
    """Inicia el consumidor en un hilo separado"""
    thread = threading.Thread(target=consumir_monitoreo, daemon=True)
    thread.start()


@app.get("/health")
def health():
    return {"status": "ok", "servicio": "api-service"}

@app.post("/token")
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    try:
        usuario = auth_mod.autenticar_usuario(form_data.username, form_data.password)
    except Exception as error:  # noqa: BLE001
        print(f"[API] No se pudo autenticar contra PostgreSQL: {error}", flush=True)
        usuario = None
    if not usuario:
        raise HTTPException(
            status_code=401,
            detail="Usuario o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": usuario["username"], "rol": usuario["rol"]},
        expires_delta=access_token_expires,
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "rol": usuario["rol"],
        "nombre_completo": usuario.get("nombre_completo") or usuario["username"],
    }

@app.get("/api/riesgo/todas", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def obtener_todas_las_zonas():
    """Devuelve el riesgo de todas las zonas (último resultado por zona)."""
    with cursor_db() as cur:
        cur.execute("""
            SELECT DISTINCT ON (r.zona)
                r.zona,
                r.nivel_riesgo,
                r.puntaje,
                r.puntaje_maximo,
                r.fecha_evaluacion,
                r.version_modelo,
                z.comuna,
                z.codigo_comuna,
                v.fuente,
                v.fecha_medicion,
                EXTRACT(EPOCH FROM (NOW() - v.fecha_medicion)) / 60.0 AS antiguedad_minutos,
                r.probabilidad,
                r.fuera_ventana_estacional,
                r.factores_predominantes,
                r.origen,
                r.usuario
            FROM resultado_riesgo r
            LEFT JOIN comuna_zona z ON z.slug = r.zona
            LEFT JOIN LATERAL (
                SELECT fuente, fecha_medicion, fecha_ingesta
                FROM variable_ambiental va
                WHERE va.id_zona = z.id_zona
                ORDER BY va.fecha_medicion DESC, va.id_variable DESC
                LIMIT 1
            ) v ON TRUE
            ORDER BY r.zona, r.fecha_evaluacion DESC
        """)
        resultados = cur.fetchall()

    return {
        "total": len(resultados),
        "zonas": [serializar_zona(r) for r in resultados],
    }


def contar_alertas_activas() -> int:
    """Número de alertas con estado 'activa' (Fase 2), para el panel ejecutivo."""
    with cursor_db() as cur:
        cur.execute("SELECT COUNT(*) AS total FROM alerta WHERE estado = 'activa'")
        fila = cur.fetchone()
    return int((fila or {}).get("total") or 0)


@app.get("/api/panel/resumen", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def panel_resumen():
    """Panel ejecutivo: distribución de niveles, variación y factores comunes."""
    filas = consultar_ultimas_zonas(limite_por_zona=2)
    por_zona: dict[str, list[dict]] = {}
    for fila in filas:
        por_zona.setdefault(fila["zona"], []).append(fila)

    resumen = {"ALTO": 0, "MEDIO": 0, "BAJO": 0}
    zonas_criticas = []
    variacion = []
    ocurrencias: dict[str, dict] = {}
    fecha_maxima = None

    for zona, evaluaciones in por_zona.items():
        actual = evaluaciones[0]
        nivel = actual.get("nivel_riesgo")
        if nivel in resumen:
            resumen[nivel] += 1
        if nivel == "ALTO":
            zonas_criticas.append({
                "zona": zona,
                "nivel_riesgo": nivel,
                "probabilidad": actual.get("probabilidad"),
            })
        if len(evaluaciones) > 1:
            anterior = evaluaciones[1]
            if anterior.get("nivel_riesgo") != nivel:
                prob_actual = float(actual.get("probabilidad") or 0.0)
                prob_anterior = float(anterior.get("probabilidad") or 0.0)
                variacion.append({
                    "zona": zona,
                    "anterior": anterior.get("nivel_riesgo"),
                    "actual": nivel,
                    "delta_probabilidad": round(prob_actual - prob_anterior, 4),
                })
        if actual.get("fecha_evaluacion") and (
            fecha_maxima is None or actual["fecha_evaluacion"] > fecha_maxima
        ):
            fecha_maxima = actual["fecha_evaluacion"]
        for factor in _json_seguro(actual.get("factores_predominantes"), []) or []:
            variable = factor.get("variable")
            if not variable:
                continue
            entrada = ocurrencias.setdefault(variable, {"variable": variable, "ocurrencias": 0, "zonas": []})
            entrada["ocurrencias"] += 1
            if zona not in entrada["zonas"]:
                entrada["zonas"].append(zona)

    variables_predominantes = sorted(
        ocurrencias.values(), key=lambda item: item["ocurrencias"], reverse=True
    )

    return {
        "fecha": _iso(fecha_maxima),
        "resumen_niveles": resumen,
        "zonas_criticas": zonas_criticas,
        "variacion_reciente": variacion,
        "alertas_activas": contar_alertas_activas(),
        "variables_predominantes": variables_predominantes,
        "total_zonas": len(por_zona),
    }


@app.get("/api/comparar")
def comparar_zonas(
    zonas: str,
    usuario_actual: dict = Depends(require_rol("admin", "analista", "visualizador")),
):
    """Compara hasta 3 zonas con su última evaluación real."""
    solicitadas = [zona.strip() for zona in zonas.split(",") if zona.strip()]
    if len(solicitadas) > 3:
        raise HTTPException(status_code=422, detail="máximo 3 zonas")
    if len(solicitadas) != len(set(solicitadas)):
        raise HTTPException(status_code=422, detail="zonas duplicadas")

    filas = consultar_ultimas_zonas(limite_por_zona=1, zonas=solicitadas)
    por_zona = {fila["zona"]: fila for fila in filas}
    faltantes = [zona for zona in solicitadas if zona not in por_zona]
    if faltantes:
        raise HTTPException(status_code=422, detail=f"zona '{faltantes[0]}' no encontrada")

    comparacion = []
    for zona in solicitadas:
        fila = por_zona[zona]
        variables = _json_seguro(fila.get("variables"), {}) or {}
        factores = _json_seguro(fila.get("factores_predominantes"), [])
        comparacion.append({
            "zona": zona,
            "comuna": fila.get("comuna"),
            "nivel_riesgo": fila.get("nivel_riesgo"),
            "probabilidad": fila.get("probabilidad"),
            "puntaje": fila.get("puntaje"),
            "variables": {
                "temperatura": variables.get("temperatura"),
                "humedad": variables.get("humedad"),
                "viento": variables.get("viento"),
                "vegetacion": variables.get("vegetacion"),
                "incendios_historicos": variables.get("incendios_historicos"),
            },
            "factores_predominantes": factores if isinstance(factores, list) else [],
            "ultima_evaluacion": _iso(fila.get("fecha_evaluacion")),
            "prioridad": None,
        })

    registrar_bitacora(
        usuario_actual.get("username"),
        None,
        "consulta",
        f"Comparó las zonas {', '.join(solicitadas)}",
        {"zonas": solicitadas},
    )
    return {"zonas": solicitadas, "comparacion": comparacion}


@app.post("/api/simular")
def simular_escenario(
    peticion: SimulacionRequest,
    usuario_actual: dict = Depends(require_rol("admin", "analista")),
):
    """Simula un escenario sobre las features reales sin tocar el historial."""
    zona = peticion.zona.strip()
    fila = _leer_evaluacion_para_simular(zona)
    if not fila:
        raise HTTPException(status_code=404, detail=f"zona '{zona}' sin evaluación previa")

    variables_base = _json_seguro(fila.get("variables"), {}) or {}
    features_base = _json_seguro(fila.get("features"), {})
    if not isinstance(features_base, dict) or not features_base:
        features_base = _features_desde_variables(variables_base)

    escenario = {
        clave: float(valor)
        for clave, valor in (peticion.ajustes or {}).items()
        if valor is not None
    }
    features_simuladas = recalcular_features_simulacion(features_base, escenario)
    variables_simuladas = {**variables_base, **escenario}
    resultado = _clasificar_escenario(
        zona, features_simuladas, variables_simuladas, fila.get("region"),
        comuna=fila.get("comuna"),
    )

    probabilidad_original = fila.get("probabilidad")
    probabilidad_simulada = resultado.get("probabilidad")
    delta = None
    if probabilidad_simulada is not None:
        delta = round(
            float(probabilidad_simulada) - float(probabilidad_original or 0.0), 4
        )
    factores = resultado.get("factores_predominantes") or []

    _persistir_simulacion(
        {
            "id_zona": fila.get("id_zona"),
            "zona": zona,
            "usuario": usuario_actual.get("username") if isinstance(usuario_actual, dict) else None,
            "escenario": escenario,
            "variables_base": variables_base,
            "features_base": features_base,
            "probabilidad_original": probabilidad_original,
            "nivel_original": fila.get("nivel_riesgo"),
            "probabilidad_simulada": probabilidad_simulada,
            "nivel_simulado": resultado.get("nivel_riesgo"),
            "delta_probabilidad": delta,
            "factores_predominantes": factores,
            "fuera_ventana_estacional": resultado.get("fuera_ventana_estacional"),
        }
    )

    registrar_bitacora(
        usuario_actual.get("username"),
        zona,
        "consulta",
        f"Ejecutó una simulación en {zona}",
        {"escenario": escenario, "delta_probabilidad": delta},
    )

    return {
        "zona": zona,
        "escenario": escenario,
        "original": {
            "nivel_riesgo": fila.get("nivel_riesgo"),
            "probabilidad": probabilidad_original,
            "puntaje": fila.get("puntaje"),
        },
        "simulado": {
            "nivel_riesgo": resultado.get("nivel_riesgo"),
            "probabilidad": probabilidad_simulada,
            "puntaje": resultado.get("puntaje"),
        },
        "delta_probabilidad": delta,
        "factores_predominantes": factores,
        "advertencia": resultado.get("advertencia") or ADVERTENCIA_SIMULACION,
        "tipo_estimacion": resultado.get("tipo_estimacion"),
        "fuera_ventana_estacional": bool(resultado.get("fuera_ventana_estacional")),
    }


@app.get("/api/riesgo/{zona}/recomendaciones", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def recomendaciones_zona(zona: str):
    filas = consultar_ultimas_zonas(limite_por_zona=1, zonas=[zona])
    if not filas:
        raise HTTPException(status_code=404, detail=f"zona '{zona}' no encontrada")
    fila = filas[0]
    variables = _json_seguro(fila.get("variables"), {}) or {}
    factores = _json_seguro(fila.get("factores_predominantes"), [])
    if not isinstance(factores, list):
        factores = []
    return {
        "zona": fila.get("zona", zona),
        "nivel_riesgo": fila.get("nivel_riesgo"),
        "recomendaciones": construir_recomendaciones(
            fila.get("nivel_riesgo"), factores, variables
        ),
    }


@app.get("/api/alertas", dependencies=[Depends(require_rol("admin", "analista"))])
def listar_alertas(estado: str | None = None):
    condiciones = []
    parametros: list[Any] = []
    if estado:
        condiciones.append("estado = %s")
        parametros.append(estado)
    filtro = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    with cursor_db() as cur:
        cur.execute(
            f"""
            SELECT id_alerta, zona, tipo, nivel_anterior, nivel_nuevo, probabilidad,
                   umbral, mensaje, estado, created_at
            FROM alerta
            {filtro}
            ORDER BY created_at DESC, id_alerta DESC
            LIMIT 200
            """,
            parametros,
        )
        filas = cur.fetchall()
    return {
        "total": len(filas),
        "alertas": [_serializar_alerta(dict(fila)) for fila in filas],
    }


@app.patch("/api/alertas/{id_alerta}", dependencies=[Depends(require_rol("admin", "analista"))])
def actualizar_alerta(id_alerta: int, peticion: EstadoAlertaRequest):
    if peticion.estado not in {"activa", "reconocida", "resuelta"}:
        raise HTTPException(
            status_code=422,
            detail="estado debe ser 'activa', 'reconocida' o 'resuelta'",
        )
    with cursor_db(commit=True) as cur:
        cur.execute(
            """
            UPDATE alerta SET estado = %s WHERE id_alerta = %s
            RETURNING id_alerta, zona, tipo, nivel_anterior, nivel_nuevo, probabilidad,
                      umbral, mensaje, estado, created_at
            """,
            (peticion.estado, id_alerta),
        )
        fila = cur.fetchone()
    if not fila:
        raise HTTPException(status_code=404, detail=f"alerta {id_alerta} no encontrada")
    return _serializar_alerta(dict(fila))


@app.get("/api/notificaciones", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def listar_notificaciones():
    with cursor_db() as cur:
        cur.execute(
            """
            SELECT id_notificacion, tipo, titulo, mensaje, zona, nivel, leida, created_at
            FROM notificacion
            ORDER BY created_at DESC, id_notificacion DESC
            LIMIT 100
            """
        )
        filas = cur.fetchall()
        cur.execute("SELECT COUNT(*) AS total FROM notificacion WHERE leida = FALSE")
        no_leidas = cur.fetchone()["total"]
    return {
        "total": len(filas),
        "no_leidas": int(no_leidas or 0),
        "notificaciones": [_serializar_notificacion(dict(fila)) for fila in filas],
    }


@app.post("/api/notificaciones/{id_notificacion}/leida", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def marcar_notificacion_leida(id_notificacion: int):
    with cursor_db(commit=True) as cur:
        cur.execute(
            """
            UPDATE notificacion SET leida = TRUE WHERE id_notificacion = %s
            RETURNING id_notificacion, tipo, titulo, mensaje, zona, nivel, leida, created_at
            """,
            (id_notificacion,),
        )
        fila = cur.fetchone()
    if not fila:
        raise HTTPException(
            status_code=404, detail=f"notificación {id_notificacion} no encontrada"
        )
    return _serializar_notificacion(dict(fila))


@app.get("/api/estado/servicios", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def estado_servicios():
    """Lee únicamente el heartbeat persistido en `estado_servicio`."""
    with cursor_db() as cur:
        cur.execute(
            """
            SELECT servicio, estado, detalle, ultima_comprobacion, latencia_ms
            FROM estado_servicio
            ORDER BY servicio
            """
        )
        filas = cur.fetchall()
    return {"servicios": [_serializar_estado_servicio(dict(fila)) for fila in filas]}


@app.get("/api/riesgo/{zona}/historial", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def historial_zona(
    zona: str,
    desde: str | None = None,
    hasta: str | None = None,
    limite: int = 100,
):
    filas = consultar_historial(
        zona,
        desde=_validar_fecha_param(desde, "desde"),
        hasta=_validar_fecha_param(hasta, "hasta"),
        limite=limite,
    )
    return {
        "zona": zona,
        "total": len(filas),
        "serie": [_serializar_historial(fila) for fila in filas],
    }


@app.get("/api/riesgo/{zona}/explicacion", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def explicacion_zona(zona: str):
    filas = consultar_ultimas_zonas(limite_por_zona=1, zonas=[zona])
    if not filas:
        raise HTTPException(status_code=404, detail=f"zona '{zona}' no encontrada")

    fila = filas[0]
    factores = _json_seguro(fila.get("factores_predominantes"), [])
    if not isinstance(factores, list):
        factores = []
    variables = _json_seguro(fila.get("variables"), {}) or {}
    nivel = fila.get("nivel_riesgo")
    return {
        "zona": fila.get("zona", zona),
        "nivel_riesgo": nivel,
        "probabilidad": fila.get("probabilidad"),
        "explicacion": componer_explicacion(nivel, factores),
        "factores_predominantes": factores,
        "variables": {
            "temperatura": variables.get("temperatura"),
            "humedad": variables.get("humedad"),
            "viento": variables.get("viento"),
            "precipitacion": variables.get("precipitacion"),
            "vegetacion": variables.get("vegetacion"),
            "incendios_historicos": variables.get("incendios_historicos"),
        },
        "advertencia": ADVERTENCIA_RIESGO,
        "fuera_ventana_estacional": bool(fila.get("fuera_ventana_estacional")) if fila.get("fuera_ventana_estacional") is not None else False,
    }


@app.get("/api/riesgo/{zona}")
def consultar_riesgo(zona: str, usuario_actual: dict = Depends(require_rol("admin", "analista", "visualizador"))):
    # 1. Buscar en caché
    with cursor_db() as cur:
        cur.execute("""
            SELECT r.nivel_riesgo, r.puntaje, r.puntaje_maximo, r.fecha_evaluacion,
                   r.version_modelo, v.fuente, v.fecha_medicion,
                   EXTRACT(EPOCH FROM (NOW() - v.fecha_medicion)) / 60.0 AS antiguedad_minutos,
                   r.probabilidad, r.fuera_ventana_estacional,
                   r.factores_predominantes, r.origen
            FROM resultado_riesgo r
            LEFT JOIN comuna_zona z ON z.slug = r.zona
            LEFT JOIN LATERAL (
                SELECT fuente, fecha_medicion, fecha_ingesta
                FROM variable_ambiental va
                WHERE va.id_zona = z.id_zona
                ORDER BY va.fecha_medicion DESC, va.id_variable DESC
                LIMIT 1
            ) v ON TRUE
            WHERE r.zona = %s AND r.fecha_evaluacion > NOW() - INTERVAL '6 hours'
            ORDER BY r.fecha_evaluacion DESC LIMIT 1
        """, (zona,))
        resultado = cur.fetchone()

    if resultado:
        factores = _json_seguro(resultado["factores_predominantes"], [])
        registrar_bitacora(
            usuario_actual.get("username"),
            zona,
            "consulta",
            f"Consultó el riesgo de {zona}",
            {"fuente": "cache"},
        )
        return {
            "zona": zona,
            "nivel_riesgo": resultado["nivel_riesgo"],
            "puntaje": resultado["puntaje"],
            "puntaje_maximo": resultado["puntaje_maximo"],
            "fuente": "cache",
            "fecha_evaluacion": _iso(resultado["fecha_evaluacion"]),
            "version_modelo": resultado["version_modelo"],
            "fuente_datos": resultado["fuente"],
            "fecha_medicion": _iso(resultado["fecha_medicion"]),
            "antiguedad_minutos": round(float(resultado["antiguedad_minutos"]), 1) if resultado["antiguedad_minutos"] is not None else None,
            "probabilidad": None if resultado["probabilidad"] is None else float(resultado["probabilidad"]),
            "fuera_ventana_estacional": bool(resultado["fuera_ventana_estacional"]) if resultado["fuera_ventana_estacional"] is not None else False,
            "factores_predominantes": factores if isinstance(factores, list) else [],
            "origen": resultado["origen"] or "automatico",
            "tipo_estimacion": _tipo_estimacion(resultado["version_modelo"]),
        }

    # 2. Inferir on-demand (la primera evaluación real rellena el historial).
    # Un visualizador (solo lectura) no debe disparar escrituras ni eventos.
    if usuario_actual.get("rol") not in ("admin", "analista"):
        raise HTTPException(
            status_code=404, detail=f"zona '{zona}' sin evaluación reciente"
        )
    inferir_y_guardar(
        zona,
        origen="on-demand",
        usuario=usuario_actual.get("username") if isinstance(usuario_actual, dict) else None,
    )

    with cursor_db() as cur:
        cur.execute("""
            SELECT nivel_riesgo, puntaje, puntaje_maximo, version_modelo, fecha_evaluacion,
                   probabilidad, fuera_ventana_estacional, factores_predominantes, origen
            FROM resultado_riesgo WHERE zona = %s
            ORDER BY fecha_evaluacion DESC, id_resultado DESC LIMIT 1
        """, (zona,))
        r = cur.fetchone()

    factores = _json_seguro(r["factores_predominantes"], [])
    registrar_bitacora(
        usuario_actual.get("username"),
        zona,
        "consulta",
        f"Consultó el riesgo de {zona}",
        {"fuente": "on-demand"},
    )
    return {
        "zona": zona,
        "nivel_riesgo": r["nivel_riesgo"],
        "puntaje": r["puntaje"],
        "puntaje_maximo": r["puntaje_maximo"],
        "version_modelo": r["version_modelo"],
        "fecha_evaluacion": _iso(r["fecha_evaluacion"]),
        "probabilidad": None if r["probabilidad"] is None else float(r["probabilidad"]),
        "fuera_ventana_estacional": bool(r["fuera_ventana_estacional"]) if r["fuera_ventana_estacional"] is not None else False,
        "factores_predominantes": factores if isinstance(factores, list) else [],
        "origen": r["origen"] or "on-demand",
        "tipo_estimacion": _tipo_estimacion(r["version_modelo"]),
        "fuente": "on-demand"
    }
    
# ---------------------------------------------------------------------------
# Fase 3: usuarios, priorización, gestión territorial y bitácora
# ---------------------------------------------------------------------------

PESOS_PRIORIDAD_ENV = {
    "riesgo": ("PESO_RIESGO", 0.5),
    "viviendas": ("PESO_VIVIENDAS", 0.2),
    "infraestructura": ("PESO_INFRA", 0.15),
    "vegetacion": ("PESO_VEGETACION", 0.15),
}

FORMULA_PRIORIDAD = (
    "puntaje_prioridad = round(100 * (W_RIESGO*probabilidad + "
    "W_VIVIENDAS*min(viviendas_expuestas/5000, 1) + "
    "W_INFRA*min((colegios+hospitales+hidrantes)/20, 1) + "
    "W_VEGETACION*vegetacion)), con vegetacion = {alta:1, media:0.5, baja:0}; "
    "los pesos se normalizan por su suma."
)


def pesos_prioridad() -> dict[str, float]:
    """Pesos configurables por entorno, normalizados para dejar el puntaje en [0, 100]."""
    crudos = {}
    for clave, (variable, defecto) in PESOS_PRIORIDAD_ENV.items():
        try:
            crudos[clave] = max(0.0, float(os.getenv(variable, defecto)))
        except (TypeError, ValueError):
            crudos[clave] = float(defecto)
    total = sum(crudos.values())
    if total <= 0:
        total = 1.0
    return {clave: valor / total for clave, valor in crudos.items()}


def valor_vegetacion(vegetacion: str | None) -> float:
    return {"alta": 1.0, "media": 0.5, "baja": 0.0}.get(
        str(vegetacion or "").strip().lower(), 0.0
    )


def calcular_puntaje_prioridad(
    probabilidad: float | None,
    viviendas_expuestas: int | None,
    colegios: int | None,
    hospitales: int | None,
    hidrantes: int | None,
    vegetacion: str | None,
    pesos: dict[str, float] | None = None,
) -> int:
    pesos = pesos or pesos_prioridad()
    infraestructura = int(colegios or 0) + int(hospitales or 0) + int(hidrantes or 0)
    valor = (
        pesos["riesgo"] * float(probabilidad or 0.0)
        + pesos["viviendas"] * min(float(viviendas_expuestas or 0) / 5000.0, 1.0)
        + pesos["infraestructura"] * min(infraestructura / 20.0, 1.0)
        + pesos["vegetacion"] * valor_vegetacion(vegetacion)
    )
    return int(round(100 * valor))


def _serializar_usuario(fila: dict[str, Any]) -> dict[str, Any]:
    return {
        "id_usuario": fila.get("id_usuario"),
        "username": fila.get("username"),
        "nombre_completo": fila.get("nombre_completo"),
        "rol": fila.get("rol"),
        "activo": bool(fila.get("activo", True)),
        "created_at": _iso(fila.get("created_at")),
    }


def _listar_usuarios_db() -> list[dict[str, Any]]:
    with cursor_db() as cur:
        cur.execute(
            """
            SELECT id_usuario, username, nombre_completo, rol, activo, created_at
            FROM usuario
            ORDER BY id_usuario
            """
        )
        return [dict(fila) for fila in cur.fetchall()]


@app.get("/api/usuarios", dependencies=[Depends(require_rol("admin"))])
def listar_usuarios():
    usuarios = [_serializar_usuario(fila) for fila in _listar_usuarios_db()]
    return {"total": len(usuarios), "usuarios": usuarios}


@app.post("/api/usuarios", status_code=201, dependencies=[Depends(require_rol("admin"))])
def crear_usuario(peticion: UsuarioCreateRequest):
    if peticion.rol not in auth_mod.ROLES_VALIDOS:
        raise HTTPException(
            status_code=422,
            detail="rol debe ser 'admin', 'analista' o 'visualizador'",
        )
    if not peticion.username.strip() or len(peticion.password) < 8:
        raise HTTPException(
            status_code=422,
            detail="username obligatorio y contraseña de al menos 8 caracteres",
        )
    with cursor_db(commit=True) as cur:
        try:
            cur.execute(
                """
                INSERT INTO usuario (username, nombre_completo, rol, hash_password)
                VALUES (%s, %s, %s, %s)
                RETURNING id_usuario, username, nombre_completo, rol, activo, created_at
                """,
                (
                    peticion.username.strip(),
                    peticion.nombre_completo or peticion.username.strip(),
                    peticion.rol,
                    auth_mod.hash_password(peticion.password),
                ),
            )
        except psycopg2.errors.UniqueViolation as error:
            raise HTTPException(status_code=409, detail="El usuario ya existe") from error
        fila = cur.fetchone()
    return _serializar_usuario(dict(fila))


@app.patch("/api/usuarios/{id_usuario}", dependencies=[Depends(require_rol("admin"))])
def actualizar_usuario(id_usuario: int, peticion: UsuarioPatchRequest):
    campos: list[str] = []
    parametros: list[Any] = []
    if peticion.rol is not None:
        if peticion.rol not in auth_mod.ROLES_VALIDOS:
            raise HTTPException(status_code=422, detail="rol inválido")
        campos.append("rol = %s")
        parametros.append(peticion.rol)
    if peticion.nombre_completo is not None:
        campos.append("nombre_completo = %s")
        parametros.append(peticion.nombre_completo)
    if peticion.activo is not None:
        campos.append("activo = %s")
        parametros.append(peticion.activo)
    if peticion.password is not None:
        if len(peticion.password) < 8:
            raise HTTPException(status_code=422, detail="contraseña demasiado corta")
        campos.append("hash_password = %s")
        parametros.append(auth_mod.hash_password(peticion.password))
    if not campos:
        raise HTTPException(status_code=422, detail="sin campos para actualizar")
    parametros.append(id_usuario)
    with cursor_db(commit=True) as cur:
        cur.execute(
            f"""
            UPDATE usuario SET {', '.join(campos)}
            WHERE id_usuario = %s
            RETURNING id_usuario, username, nombre_completo, rol, activo, created_at
            """,
            parametros,
        )
        fila = cur.fetchone()
    if not fila:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    return _serializar_usuario(dict(fila))


@app.delete("/api/usuarios/{id_usuario}", dependencies=[Depends(require_rol("admin"))])
def eliminar_usuario(id_usuario: int):
    with cursor_db(commit=True) as cur:
        cur.execute(
            "DELETE FROM usuario WHERE id_usuario = %s RETURNING id_usuario",
            (id_usuario,),
        )
        fila = cur.fetchone()
    if not fila:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    return {"eliminado": fila["id_usuario"]}


# --- Infraestructura crítica -------------------------------------------------


def _consultar_infraestructura(
    zona: str | None = None, tipo: str | None = None
) -> list[dict[str, Any]]:
    condiciones: list[str] = []
    parametros: list[Any] = []
    if zona:
        condiciones.append("z.slug = %s")
        parametros.append(zona)
    if tipo:
        condiciones.append("i.tipo = %s")
        parametros.append(tipo)
    filtro = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    with cursor_db() as cur:
        cur.execute(
            f"""
            SELECT i.id_infra, i.tipo, i.nombre, i.descripcion, i.direccion,
                   COALESCE(i.latitud, ST_Y(i.geom)) AS latitud,
                   COALESCE(i.longitud, ST_X(i.geom)) AS longitud,
                   i.expuestos, i.fuente, z.slug AS zona
            FROM infraestructura_critica i
            JOIN comuna_zona z ON z.id_zona = i.id_zona
            {filtro}
            ORDER BY i.tipo, i.id_infra
            """,
            parametros,
        )
        return [dict(fila) for fila in cur.fetchall()]


def _agrupar_infraestructura(elementos: list[dict[str, Any]]) -> dict[str, int]:
    por_tipo: dict[str, int] = {}
    for elemento in elementos:
        tipo = elemento.get("tipo") or "otro"
        por_tipo[tipo] = por_tipo.get(tipo, 0) + 1
    return por_tipo


@app.get("/api/infraestructura", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def listar_infraestructura(zona: str | None = None, tipo: str | None = None):
    elementos = _consultar_infraestructura(zona, tipo)
    return {
        "zona": zona,
        "total": len(elementos),
        "por_tipo": _agrupar_infraestructura(elementos),
        "elementos": elementos,
    }


@app.post("/api/infraestructura", status_code=201, dependencies=[Depends(require_rol("admin", "analista"))])
def crear_infraestructura(peticion: InfraestructuraRequest):
    if not peticion.zona:
        raise HTTPException(status_code=422, detail="zona es obligatoria")
    with cursor_db(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO infraestructura_critica
                (tipo, nombre, descripcion, id_zona, direccion, latitud, longitud, geom, expuestos, fuente)
            VALUES
                (%s, %s, %s,
                 (SELECT id_zona FROM comuna_zona WHERE slug = %s LIMIT 1),
                 %s, %s, %s,
                 CASE WHEN %s IS NOT NULL AND %s IS NOT NULL
                      THEN ST_SetSRID(ST_MakePoint(%s, %s), 4326) END,
                 %s, %s)
            RETURNING id_infra, tipo, nombre, descripcion, direccion, latitud, longitud, expuestos, fuente
            """,
            (
                peticion.tipo,
                peticion.nombre,
                peticion.descripcion,
                peticion.zona,
                peticion.direccion,
                peticion.latitud,
                peticion.longitud,
                peticion.latitud,
                peticion.longitud,
                peticion.longitud,
                peticion.latitud,
                peticion.expuestos,
                peticion.fuente,
            ),
        )
        fila = cur.fetchone()
    if not fila:
        raise HTTPException(status_code=404, detail=f"zona '{peticion.zona}' no encontrada")
    return dict(fila)


# --- Priorización determinista ----------------------------------------------


def _datos_prioridad() -> list[dict[str, Any]]:
    """Última evaluación + infraestructura + población por zona."""
    filas = consultar_ultimas_zonas(limite_por_zona=1)
    datos: list[dict[str, Any]] = []
    with cursor_db() as cur:
        for fila in filas:
            zona = fila["zona"]
            cur.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN i.tipo = 'vivienda' THEN i.expuestos ELSE 0 END), 0) AS viviendas,
                    COUNT(*) FILTER (WHERE i.tipo = 'colegio') AS colegios,
                    COUNT(*) FILTER (WHERE i.tipo = 'hospital') AS hospitales,
                    COUNT(*) FILTER (WHERE i.tipo = 'hidrante') AS hidrantes,
                    COUNT(*) AS total
                FROM infraestructura_critica i
                JOIN comuna_zona z ON z.id_zona = i.id_zona
                WHERE z.slug = %s
                """,
                (zona,),
            )
            agregados = dict(cur.fetchone() or {})
            cur.execute(
                """
                SELECT COALESCE(SUM(d.poblacion), 0) AS poblacion
                FROM distrito_censal d
                JOIN comuna_zona z ON z.codigo_comuna = d.codigo_comuna
                WHERE z.slug = %s
                """,
                (zona,),
            )
            poblacion = dict(cur.fetchone() or {}).get("poblacion", 0)
            variables = _json_seguro(fila.get("variables"), {}) or {}
            datos.append(
                {
                    "zona": zona,
                    "comuna": fila.get("comuna"),
                    "nivel_riesgo": fila.get("nivel_riesgo"),
                    "probabilidad": fila.get("probabilidad"),
                    "vegetacion": variables.get("vegetacion"),
                    "viviendas_expuestas": int(agregados.get("viviendas") or 0),
                    "colegios": int(agregados.get("colegios") or 0),
                    "hospitales": int(agregados.get("hospitales") or 0),
                    "hidrantes": int(agregados.get("hidrantes") or 0),
                    "infraestructura_total": int(agregados.get("total") or 0),
                    "poblacion": int(poblacion or 0),
                }
            )
    return datos


def construir_priorizacion(
    datos: list[dict[str, Any]], pesos: dict[str, float] | None = None
) -> list[dict[str, Any]]:
    pesos = pesos or pesos_prioridad()
    zonas = []
    for dato in datos:
        puntaje = calcular_puntaje_prioridad(
            dato.get("probabilidad"),
            dato.get("viviendas_expuestas"),
            dato.get("colegios"),
            dato.get("hospitales"),
            dato.get("hidrantes"),
            dato.get("vegetacion"),
            pesos,
        )
        zonas.append(
            {
                "zona": dato.get("zona"),
                "comuna": dato.get("comuna"),
                "nivel_riesgo": dato.get("nivel_riesgo"),
                "probabilidad": dato.get("probabilidad"),
                "puntaje_prioridad": puntaje,
                "factores": {
                    "riesgo": dato.get("probabilidad"),
                    "viviendas_expuestas": dato.get("viviendas_expuestas"),
                    "colegios": dato.get("colegios"),
                    "hospitales": dato.get("hospitales"),
                    "infraestructura_total": dato.get("infraestructura_total"),
                    "vegetacion": dato.get("vegetacion"),
                    "poblacion": dato.get("poblacion"),
                },
            }
        )
    zonas.sort(key=lambda item: item["puntaje_prioridad"], reverse=True)
    for posicion, zona in enumerate(zonas, start=1):
        zona["posicion"] = posicion
    return zonas


@app.get("/api/prioridad")
def prioridad_zonas(
    usuario_actual: dict = Depends(require_rol("admin", "analista", "visualizador")),
):
    pesos = pesos_prioridad()
    zonas = construir_priorizacion(_datos_prioridad(), pesos)
    registrar_bitacora(
        usuario_actual.get("username"),
        None,
        "consulta",
        "Consultó la priorización de zonas",
    )
    return {
        "total": len(zonas),
        "zonas": zonas,
        "formula": FORMULA_PRIORIDAD,
        "pesos": pesos,
    }


# --- Observaciones de terreno ------------------------------------------------


def _serializar_observacion(fila: dict[str, Any]) -> dict[str, Any]:
    return {
        "id_observacion": fila.get("id_observacion"),
        "zona": fila.get("zona"),
        "tipo": fila.get("tipo"),
        "descripcion": fila.get("descripcion"),
        "severidad": fila.get("severidad"),
        "estado": fila.get("estado"),
        "usuario": fila.get("usuario"),
        "latitud": fila.get("latitud"),
        "longitud": fila.get("longitud"),
        "created_at": _iso(fila.get("created_at")),
    }


def _listar_observaciones(
    zona: str | None = None, tipo: str | None = None, estado: str | None = None
) -> list[dict[str, Any]]:
    condiciones: list[str] = []
    parametros: list[Any] = []
    for columna, valor in (("zona", zona), ("tipo", tipo), ("estado", estado)):
        if valor:
            condiciones.append(f"{columna} = %s")
            parametros.append(valor)
    filtro = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    with cursor_db() as cur:
        cur.execute(
            f"""
            SELECT id_observacion, zona, tipo, descripcion, severidad, estado,
                   usuario, COALESCE(latitud, ST_Y(geom)) AS latitud,
                   COALESCE(longitud, ST_X(geom)) AS longitud, created_at
            FROM observacion_terreno
            {filtro}
            ORDER BY created_at DESC, id_observacion DESC
            LIMIT 300
            """,
            parametros,
        )
        return [dict(fila) for fila in cur.fetchall()]


@app.get("/api/observaciones", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def listar_observaciones(
    zona: str | None = None, tipo: str | None = None, estado: str | None = None
):
    filas = _listar_observaciones(zona, tipo, estado)
    return {"total": len(filas), "observaciones": [_serializar_observacion(f) for f in filas]}


@app.post("/api/observaciones", status_code=201)
def crear_observacion(
    peticion: ObservacionRequest,
    usuario_actual: dict = Depends(require_rol("admin", "analista")),
):
    if peticion.severidad not in {"alta", "media", "baja"}:
        raise HTTPException(status_code=422, detail="severidad debe ser 'alta', 'media' o 'baja'")
    with cursor_db(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO observacion_terreno
                (id_zona, zona, tipo, descripcion, severidad, estado, usuario,
                 latitud, longitud, geom)
            VALUES
                ((SELECT id_zona FROM comuna_zona WHERE slug = %s LIMIT 1),
                 %s, %s, %s, %s, 'abierta', %s, %s, %s,
                 CASE WHEN %s IS NOT NULL AND %s IS NOT NULL
                      THEN ST_SetSRID(ST_MakePoint(%s, %s), 4326) END)
            RETURNING id_observacion, zona, tipo, descripcion, severidad, estado,
                      usuario, latitud, longitud, created_at
            """,
            (
                peticion.zona,
                peticion.zona,
                peticion.tipo,
                peticion.descripcion,
                peticion.severidad,
                usuario_actual.get("username"),
                peticion.latitud,
                peticion.longitud,
                peticion.latitud,
                peticion.longitud,
                peticion.longitud,
                peticion.latitud,
            ),
        )
        fila = cur.fetchone()
    registrar_bitacora(
        usuario_actual.get("username"),
        peticion.zona,
        "observacion",
        f"Registró observación de {peticion.tipo} en {peticion.zona}",
        {"id_observacion": fila["id_observacion"], "severidad": peticion.severidad},
    )
    return _serializar_observacion(dict(fila))


# --- Acciones preventivas ----------------------------------------------------


def _serializar_accion(fila: dict[str, Any]) -> dict[str, Any]:
    return {
        "id_accion": fila.get("id_accion"),
        "zona": fila.get("zona"),
        "descripcion": fila.get("descripcion"),
        "tipo": fila.get("tipo"),
        "responsable": fila.get("responsable"),
        "estado": fila.get("estado"),
        "fecha_programada": _iso(fila.get("fecha_programada")),
        "fecha_ejecucion": _iso(fila.get("fecha_ejecucion")),
        "prioridad": fila.get("prioridad"),
        "observacion_relacionada": fila.get("observacion_relacionada"),
        "created_at": _iso(fila.get("created_at")),
    }


def _validar_fecha_accion(valor: str | None, nombre: str) -> str | None:
    if not valor:
        return None
    try:
        datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail=f"'{nombre}' debe ser una fecha ISO-8601"
        ) from error
    return valor


def _listar_acciones(zona: str | None = None, estado: str | None = None) -> list[dict[str, Any]]:
    condiciones: list[str] = []
    parametros: list[Any] = []
    for columna, valor in (("zona", zona), ("estado", estado)):
        if valor:
            condiciones.append(f"{columna} = %s")
            parametros.append(valor)
    filtro = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    with cursor_db() as cur:
        cur.execute(
            f"""
            SELECT id_accion, zona, descripcion, tipo, responsable, estado,
                   fecha_programada, fecha_ejecucion, prioridad,
                   observacion_relacionada, created_at
            FROM accion_preventiva
            {filtro}
            ORDER BY created_at DESC, id_accion DESC
            LIMIT 300
            """,
            parametros,
        )
        return [dict(fila) for fila in cur.fetchall()]


@app.get("/api/acciones", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def listar_acciones(zona: str | None = None, estado: str | None = None):
    filas = _listar_acciones(zona, estado)
    return {"total": len(filas), "acciones": [_serializar_accion(f) for f in filas]}


@app.post("/api/acciones", status_code=201)
def crear_accion(
    peticion: AccionRequest,
    usuario_actual: dict = Depends(require_rol("admin", "analista")),
):
    fecha_programada = _validar_fecha_accion(peticion.fecha_programada, "fecha_programada")
    with cursor_db(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO accion_preventiva
                (id_zona, zona, descripcion, tipo, responsable, estado,
                 fecha_programada, prioridad, observacion_relacionada)
            VALUES
                ((SELECT id_zona FROM comuna_zona WHERE slug = %s LIMIT 1),
                 %s, %s, %s, %s, 'pendiente', %s, %s, %s)
            RETURNING id_accion, zona, descripcion, tipo, responsable, estado,
                      fecha_programada, fecha_ejecucion, prioridad,
                      observacion_relacionada, created_at
            """,
            (
                peticion.zona,
                peticion.zona,
                peticion.descripcion,
                peticion.tipo,
                peticion.responsable or usuario_actual.get("username"),
                fecha_programada,
                peticion.prioridad,
                peticion.observacion_relacionada,
            ),
        )
        fila = cur.fetchone()
    registrar_bitacora(
        usuario_actual.get("username"),
        peticion.zona,
        "accion",
        "Acción preventiva creada",
        {"id_accion": fila["id_accion"], "tipo": peticion.tipo},
    )
    return _serializar_accion(dict(fila))


@app.patch("/api/acciones/{id_accion}")
def actualizar_accion(
    id_accion: int,
    peticion: AccionPatchRequest,
    usuario_actual: dict = Depends(require_rol("admin", "analista")),
):
    if peticion.estado is not None and peticion.estado not in {
        "pendiente", "en_progreso", "completada", "cancelada"
    }:
        raise HTTPException(
            status_code=422,
            detail="estado debe ser 'pendiente', 'en_progreso', 'completada' o 'cancelada'",
        )
    campos: list[str] = []
    parametros: list[Any] = []
    if peticion.estado is not None:
        campos.append("estado = %s")
        parametros.append(peticion.estado)
    if peticion.responsable is not None:
        campos.append("responsable = %s")
        parametros.append(peticion.responsable)
    if peticion.prioridad is not None:
        campos.append("prioridad = %s")
        parametros.append(peticion.prioridad)
    if peticion.fecha_ejecucion is not None:
        campos.append("fecha_ejecucion = %s")
        parametros.append(_validar_fecha_accion(peticion.fecha_ejecucion, "fecha_ejecucion"))
    if not campos:
        raise HTTPException(status_code=422, detail="sin campos para actualizar")
    parametros.append(id_accion)
    with cursor_db(commit=True) as cur:
        cur.execute(
            f"""
            UPDATE accion_preventiva SET {', '.join(campos)}
            WHERE id_accion = %s
            RETURNING id_accion, zona, descripcion, tipo, responsable, estado,
                      fecha_programada, fecha_ejecucion, prioridad,
                      observacion_relacionada, created_at
            """,
            parametros,
        )
        fila = cur.fetchone()
    if not fila:
        raise HTTPException(status_code=404, detail=f"acción {id_accion} no encontrada")
    registrar_bitacora(
        usuario_actual.get("username"),
        fila["zona"],
        "accion",
        f"Acción preventiva actualizada a {fila['estado']}",
        {"id_accion": id_accion},
    )
    return _serializar_accion(dict(fila))


# --- Bitácora de decisiones --------------------------------------------------


def registrar_bitacora(
    usuario: str | None,
    zona: str | None,
    tipo_evento: str,
    detalle: str,
    metadata: dict[str, Any] | None = None,
) -> int | None:
    """Inserta la entrada y publica `bitacora.nueva` (nunca rompe el request)."""
    try:
        with cursor_db(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO bitacora_decision
                    (usuario, id_zona, zona, tipo_evento, detalle, metadata)
                VALUES
                    (%s, (SELECT id_zona FROM comuna_zona WHERE slug = %s LIMIT 1),
                     %s, %s, %s, %s)
                RETURNING id_bitacora
                """,
                (usuario, zona, zona, tipo_evento, detalle, Json(metadata or {})),
            )
            fila = cur.fetchone()
        id_bitacora = fila["id_bitacora"] if fila else None
    except Exception as error:  # noqa: BLE001
        print(f"[API] No se pudo registrar en bitácora: {error}", flush=True)
        return None

    if id_bitacora is not None:
        publicar_evento_seguro(
            "bitacora.nueva",
            {
                "id_bitacora": id_bitacora,
                "usuario": usuario,
                "zona": zona,
                "tipo_evento": tipo_evento,
                "detalle": detalle,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    return id_bitacora


def _consultar_bitacora(
    zona: str | None = None,
    usuario: str | None = None,
    tipo_evento: str | None = None,
) -> list[dict[str, Any]]:
    condiciones: list[str] = []
    parametros: list[Any] = []
    for columna, valor in (("zona", zona), ("usuario", usuario), ("tipo_evento", tipo_evento)):
        if valor:
            condiciones.append(f"{columna} = %s")
            parametros.append(valor)
    filtro = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    with cursor_db() as cur:
        cur.execute(
            f"""
            SELECT id_bitacora, usuario, zona, tipo_evento, detalle, metadata, created_at
            FROM bitacora_decision
            {filtro}
            ORDER BY created_at DESC, id_bitacora DESC
            LIMIT 300
            """,
            parametros,
        )
        return [dict(fila) for fila in cur.fetchall()]


@app.get("/api/bitacora")
def listar_bitacora(
    zona: str | None = None,
    usuario: str | None = None,
    tipo_evento: str | None = None,
    usuario_actual: dict = Depends(require_rol("admin", "analista")),
):
    filas = _consultar_bitacora(zona, usuario, tipo_evento)
    return {
        "total": len(filas),
        "entradas": [
            {
                "id_bitacora": fila.get("id_bitacora"),
                "usuario": fila.get("usuario"),
                "zona": fila.get("zona"),
                "tipo_evento": fila.get("tipo_evento"),
                "detalle": fila.get("detalle"),
                "metadata": _json_seguro(fila.get("metadata"), {}),
                "created_at": _iso(fila.get("created_at")),
            }
            for fila in filas
        ],
    }


# --- Proxy autenticado hacia data-service (S6) -------------------------------


def _data_service_get(ruta: str) -> dict[str, Any]:
    if not INTERNAL_TOKEN:
        raise HTTPException(status_code=503, detail="INTERNAL_TOKEN no configurado")
    try:
        with httpx.Client(timeout=10.0) as client:
            respuesta = client.get(
                f"{DATA_SERVICE_URL}{ruta}",
                headers={"X-Internal-Token": INTERNAL_TOKEN},
            )
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail=f"data-service no disponible: {error}") from error
    if respuesta.status_code >= 400:
        detalle = respuesta.text
        try:
            detalle = respuesta.json().get("detail", detalle)
        except ValueError:
            pass
        raise HTTPException(status_code=respuesta.status_code, detail=detalle)
    return respuesta.json()


@app.get("/api/datos/distritos", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def datos_distritos():
    return _data_service_get("/datos/distritos")


@app.get("/api/datos/{zona}/infraestructura", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def datos_infraestructura(zona: str, tipo: str | None = None):
    sufijo = f"?tipo={tipo}" if tipo else ""
    return _data_service_get(f"/datos/{zona}/infraestructura{sufijo}")


@app.get("/api/datos/{zona}/vegetacion", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def datos_vegetacion(zona: str):
    return _data_service_get(f"/datos/{zona}/vegetacion")


@app.get("/api/datos/{zona}/historico", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def datos_historico(
    zona: str, desde: str | None = None, hasta: str | None = None, limite: int = 100
):
    parametros = [f"limite={limite}"]
    if desde:
        parametros.append(f"desde={desde}")
    if hasta:
        parametros.append(f"hasta={hasta}")
    return _data_service_get(f"/datos/{zona}/historico?{'&'.join(parametros)}")


@app.get("/api/datos/{zona}", dependencies=[Depends(require_rol("admin", "analista", "visualizador"))])
def datos_zona(zona: str):
    return _data_service_get(f"/datos/{zona}")


# ---------------------------------------------------------------------------
# Fase 4: reportes PDF, exportaciones CSV/PDF y asistente explicativo
# ---------------------------------------------------------------------------

COLUMNAS_CSV = [
    "zona",
    "comuna",
    "nivel_riesgo",
    "probabilidad",
    "puntaje",
    "fecha_evaluacion",
    "version_modelo",
    "fuera_ventana_estacional",
    "temperatura",
    "humedad",
    "viento",
    "vegetacion",
]

COLORES_MAPA = {
    "ALTO": "#fca5a5",
    "MEDIO": "#fdba74",
    "BAJO": "#86efac",
    "SIN": "#e2e8f0",
}

COLORES_NIVEL = {
    "ALTO": "#b91c1c",
    "MEDIO": "#c2410c",
    "BAJO": "#15803d",
}

_GEOJSON_CACHE: dict[str, Any] | None = None

ZONAS_BASE = {
    "quilpue": "Quilpué",
    "valparaiso": "Valparaíso",
    "vina_del_mar": "Viña del Mar",
}

RESPUESTA_FUERA_DOMINIO = (
    "Solo puedo responder sobre riesgo meteorológico, factores, historial y "
    "recomendaciones de las zonas del sistema."
)

PALABRAS_NO_ZONA = {
    "por", "que", "porque", "cual", "cuales", "como", "cuando", "donde",
    "es", "son", "ser", "esta", "estan", "hay", "tiene", "tienen", "hace",
    "el", "la", "los", "las", "un", "una", "unos", "unas", "del", "de",
    "al", "en", "para", "con", "sin", "sobre", "mi", "mis", "tu", "su",
    "riesgo", "nivel", "factor", "factores", "probabilidad", "porcentaje",
    "historial", "evolucion", "recomendacion", "recomendaciones", "comparar",
    "compara", "comparacion", "explica", "explicar", "explicacion", "dime",
    "dame", "muestra", "mostrar", "saber", "consultar", "consulta", "zona",
    "zonas", "comuna", "comunas", "meteorologico", "meteorologica", "incendio",
    "incendios", "alerta", "alertas", "peligro", "alto", "media", "medio",
    "bajo", "actual", "hoy", "ayer", "semana", "mes", "ano", "anos",
    "cuanto", "cuanta", "cuantos", "cuantas", "cual", "seria", "podria",
    "mayor", "menor", "maximo", "minimo", "peor", "top", "mejor", "mas",
}


def _normalizar_texto(valor: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(caracter for caracter in texto if not unicodedata.combining(caracter))
    return " ".join(texto.lower().replace("_", " ").split())


def _ruta_geojson() -> str | None:
    candidatos: list[str] = []
    if GEOJSON_PATH:
        candidatos.append(GEOJSON_PATH)
    candidatos.extend(
        [
            "/app/datos/geo/distritos_filtrados.geojson",
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..",
                "frontend",
                "data",
                "distritos_filtrados.geojson",
            ),
            os.path.join("frontend", "data", "distritos_filtrados.geojson"),
        ]
    )
    for ruta in candidatos:
        if os.path.exists(ruta):
            return ruta
    return None


def _cargar_geojson() -> dict[str, Any]:
    """Única fuente del mapa: `frontend/data/distritos_filtrados.geojson`."""
    global _GEOJSON_CACHE
    if _GEOJSON_CACHE is not None:
        return _GEOJSON_CACHE
    ruta = _ruta_geojson()
    if not ruta:
        _GEOJSON_CACHE = {"type": "FeatureCollection", "features": []}
        return _GEOJSON_CACHE
    try:
        with open(ruta, encoding="utf-8") as archivo:
            _GEOJSON_CACHE = json.load(archivo)
    except (OSError, ValueError) as error:
        print(f"[API] No se pudo leer el GeoJSON local: {error}", flush=True)
        _GEOJSON_CACHE = {"type": "FeatureCollection", "features": []}
    return _GEOJSON_CACHE


def _poligonos(geometria: dict[str, Any]):
    tipo = (geometria or {}).get("type")
    coordenadas = (geometria or {}).get("coordinates") or []
    if tipo == "Polygon":
        yield coordenadas
    elif tipo == "MultiPolygon":
        for poligono in coordenadas:
            yield poligono


def _indice_zonas_por_comuna(filas: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indice: dict[str, dict[str, Any]] = {}
    for fila in filas:
        for clave in (fila.get("zona"), fila.get("comuna")):
            if clave:
                indice[_normalizar_texto(clave)] = fila
    return indice


# Mismo encuadre regional que usa el frontend (frontend/app.js → fitMapToData).
LIMITES_REGION = {
    "lat_min": -35.0,
    "lat_max": -31.5,
    "lon_min": -73.5,
    "lon_max": -69.5,
}

RADIO_TIERRA = 6378137.0


def _punto_wgs84(punto: Any) -> tuple[float, float] | None:
    """Devuelve (lon, lat) aceptando coordenadas en grados o en Web Mercator."""
    if not isinstance(punto, (list, tuple)) or len(punto) < 2:
        return None
    try:
        x = float(punto[0])
        y = float(punto[1])
    except (TypeError, ValueError):
        return None
    if abs(x) <= 180.0 and abs(y) <= 90.0:
        return x, y
    lon = math.degrees(x / RADIO_TIERRA)
    lat = math.degrees(2.0 * math.atan(math.exp(y / RADIO_TIERRA)) - math.pi / 2.0)
    return lon, lat


def _dentro_region(lon: float, lat: float) -> bool:
    return (
        LIMITES_REGION["lon_min"] < lon < LIMITES_REGION["lon_max"]
        and LIMITES_REGION["lat_min"] < lat < LIMITES_REGION["lat_max"]
    )


def _anillos_regionales(feature: dict[str, Any]) -> list[list[tuple[float, float]]]:
    """Anillos del feature que caen en el encuadre regional.

    El GeoJSON incluye distritos con coordenadas erróneas muy alejadas de la
    región (p. ej. 510124); si entraran al cálculo del bounding box, el mapa
    quedaría reducido a unos pocos píxeles.
    """
    anillos: list[list[tuple[float, float]]] = []
    for poligono in _poligonos(feature.get("geometry") or {}):
        for anillo in poligono:
            vertices: list[tuple[float, float]] = []
            lons: list[float] = []
            lats: list[float] = []
            for punto in anillo:
                coordenada = _punto_wgs84(punto)
                if not coordenada:
                    continue
                lons.append(coordenada[0])
                lats.append(coordenada[1])
                vertices.append((float(punto[0]), float(punto[1])))
            if len(vertices) < 3 or not lons:
                continue
            centro_lon = (min(lons) + max(lons)) / 2
            centro_lat = (min(lats) + max(lats)) / 2
            if _dentro_region(centro_lon, centro_lat):
                anillos.append(vertices)
    return anillos


def _dibujar_mapa_local(
    pdf: canvas.Canvas,
    filas: list[dict[str, Any]],
    x: float,
    y: float,
    ancho: float,
    alto: float,
) -> None:
    """Dibuja los distritos del GeoJSON local; no usa tiles ni red externa."""
    pdf.setStrokeColor(colors.HexColor("#94a3b8"))
    pdf.setLineWidth(0.8)
    pdf.rect(x, y, ancho, alto)

    features: list[tuple[dict[str, Any], list[list[tuple[float, float]]]]] = []
    puntos: list[tuple[float, float]] = []
    for feature in _cargar_geojson().get("features") or []:
        anillos = _anillos_regionales(feature)
        if not anillos:
            continue
        features.append((feature, anillos))
        for anillo in anillos:
            puntos.extend(anillo)

    if not puntos:
        pdf.setFont("Helvetica", 8)
        pdf.setFillColor(colors.HexColor("#64748b"))
        pdf.drawString(x + 8, y + alto / 2, "Mapa local no disponible (GeoJSON sin geometrías).")
        return

    min_x = min(p[0] for p in puntos)
    max_x = max(p[0] for p in puntos)
    min_y = min(p[1] for p in puntos)
    max_y = max(p[1] for p in puntos)
    delta_x = max(max_x - min_x, 1.0)
    delta_y = max(max_y - min_y, 1.0)
    margen = 8.0
    escala = min((ancho - 2 * margen) / delta_x, (alto - 2 * margen) / delta_y)
    offset_x = x + (ancho - delta_x * escala) / 2 - min_x * escala
    offset_y = y + (alto - delta_y * escala) / 2 - min_y * escala

    def proyectar(punto: tuple[float, float]) -> tuple[float, float]:
        return (punto[0] * escala + offset_x, punto[1] * escala + offset_y)

    indice = _indice_zonas_por_comuna(filas)
    for feature, anillos in features:
        propiedades = feature.get("properties") or {}
        comuna = propiedades.get("NOM_COMUNA") or propiedades.get("COMUNA")
        fila = indice.get(_normalizar_texto(comuna)) or {}
        nivel = fila.get("nivel_riesgo") or "SIN"
        for anillo in anillos:
            vertices = [proyectar(p) for p in anillo]
            trazo = pdf.beginPath()
            trazo.moveTo(*vertices[0])
            for vertice in vertices[1:]:
                trazo.lineTo(*vertice)
            trazo.close()
            pdf.setFillColor(colors.HexColor(COLORES_MAPA.get(nivel, COLORES_MAPA["SIN"])))
            pdf.setStrokeColor(colors.white)
            pdf.setLineWidth(0.5)
            pdf.drawPath(trazo, fill=1, stroke=1)

    nivel_ficha = next((f.get("nivel_riesgo") for f in filas if f.get("nivel_riesgo")), None)
    leyenda: list[tuple[str, str]] = []
    if nivel_ficha:
        leyenda.append(
            (COLORES_MAPA.get(nivel_ficha, COLORES_MAPA["SIN"]), f"{nivel_ficha} · comuna del informe")
        )
    leyenda.append((COLORES_MAPA["SIN"], "Resto de la región"))

    pdf.setFont("Helvetica", 6.5)
    cursor_y = y + alto - 10
    for color_leyenda, texto_leyenda in leyenda:
        ancho_texto = pdf.stringWidth(texto_leyenda, "Helvetica", 6.5)
        pdf.setFillColor(colors.HexColor(color_leyenda))
        pdf.rect(x + ancho - 8 - ancho_texto - 10, cursor_y - 1.5, 5, 5, fill=1, stroke=0)
        pdf.setFillColor(colors.HexColor("#475569"))
        pdf.drawString(x + ancho - 8 - ancho_texto, cursor_y, texto_leyenda)
        cursor_y -= 9

    pdf.setFont("Helvetica", 6.5)
    pdf.setFillColor(colors.HexColor("#475569"))
    pdf.drawString(
        x + 6,
        y + 4,
        "Mapa estático local · distritos_filtrados.geojson · sin tiles externos",
    )


def _lineas(texto: str, ancho: float, fuente: str = "Helvetica", tamano: float = 9.0):
    return simpleSplit(str(texto or ""), fuente, tamano, ancho)


def _fecha_generacion() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _porcentaje(probabilidad: Any) -> str:
    if probabilidad is None:
        return "sin probabilidad"
    return f"{round(float(probabilidad) * 100)} %"


def _pagina_ficha(pdf: canvas.Canvas, fila: dict[str, Any]) -> None:
    ancho_pagina, alto_pagina = A4
    margen = 42
    ancho_util = ancho_pagina - 2 * margen

    zona = fila.get("zona") or "sin zona"
    comuna = fila.get("comuna") or zona.replace("_", " ").title()
    nivel = fila.get("nivel_riesgo") or "SIN DATOS"
    probabilidad = fila.get("probabilidad")
    variables = _json_seguro(fila.get("variables"), {}) or {}
    factores = _json_seguro(fila.get("factores_predominantes"), [])
    if not isinstance(factores, list):
        factores = []
    recomendaciones = construir_recomendaciones(nivel, factores, variables)

    pdf.setFillColor(colors.HexColor("#0f172a"))
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(margen, alto_pagina - 48, "Ficha de Riesgo Meteorológico")
    pdf.setFont("Helvetica", 8.5)
    pdf.setFillColor(colors.HexColor("#475569"))
    pdf.drawString(margen, alto_pagina - 62, f"Generada el {_fecha_generacion()}")
    pdf.setStrokeColor(colors.HexColor("#cbd5e1"))
    pdf.line(margen, alto_pagina - 70, ancho_pagina - margen, alto_pagina - 70)

    pdf.setFillColor(colors.HexColor("#0f172a"))
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(margen, alto_pagina - 92, f"{comuna} ({zona})")
    pdf.setFillColor(colors.HexColor(COLORES_NIVEL.get(nivel, "#334155")))
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(
        margen,
        alto_pagina - 112,
        f"Riesgo meteorológico: {nivel} · {_porcentaje(probabilidad)}",
    )
    pdf.setFillColor(colors.HexColor("#334155"))
    pdf.setFont("Helvetica", 8.5)
    pdf.drawString(
        margen,
        alto_pagina - 127,
        (
            f"Puntaje: {fila.get('puntaje') if fila.get('puntaje') is not None else '—'}/15 · "
            f"Versión del modelo: {fila.get('version_modelo') or 'sin dato'} · "
            f"Evaluado: {_iso(fila.get('fecha_evaluacion')) or 'sin fecha'}"
        ),
    )

    y = alto_pagina - 147
    explicacion = componer_explicacion(nivel, factores)
    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(colors.HexColor("#1e293b"))
    for linea in _lineas(explicacion, ancho_util)[:2]:
        pdf.drawString(margen, y, linea)
        y -= 11

    y -= 8
    pdf.setFont("Helvetica-Bold", 9.5)
    pdf.drawString(margen, y, "Causas (factores predominantes)")
    y -= 14
    pdf.setFont("Helvetica", 8.5)
    if not factores:
        pdf.drawString(margen, y, "Sin factores detallados para esta evaluación.")
        y -= 12
    else:
        for factor in factores[:5]:
            etiqueta = factor.get("etiqueta") or factor.get("variable") or "variable"
            valor = factor.get("valor")
            unidad = factor.get("unidad") or ""
            direccion = factor.get("direccion") or ""
            peso = factor.get("peso")
            texto = (
                f"• {etiqueta}: {valor if valor is not None else 'sin dato'} {unidad} · "
                f"{direccion} (peso {peso if peso is not None else '—'})"
            )
            for linea in _lineas(texto, ancho_util)[:2]:
                if y < alto_pagina - 300:
                    break
                pdf.drawString(margen, y, linea)
                y -= 11
            y -= 1

    pdf.setFont("Helvetica-Bold", 9.5)
    pdf.drawString(margen, 520, "Mapa de distritos (fuente local)")
    _dibujar_mapa_local(pdf, [fila], margen, 302, ancho_util, 205)

    y = 282
    pdf.setFont("Helvetica-Bold", 9.5)
    pdf.drawString(margen, y, "Recomendaciones")
    y -= 14
    pdf.setFont("Helvetica", 8.5)
    if not recomendaciones:
        pdf.drawString(margen, y, "Mantener el monitoreo del riesgo meteorológico de la zona.")
        y -= 12
    else:
        for recomendacion in recomendaciones[:5]:
            texto = f"{recomendacion.get('prioridad')}. {recomendacion.get('texto')}"
            for linea in _lineas(texto, ancho_util)[:2]:
                pdf.drawString(margen, y, linea)
                y -= 11
            y -= 1

    pdf.setStrokeColor(colors.HexColor("#cbd5e1"))
    pdf.line(margen, 158, ancho_pagina - margen, 158)
    pdf.setFont("Helvetica-Bold", 8.5)
    pdf.setFillColor(colors.HexColor("#7f1d1d"))
    pdf.drawString(margen, 143, ADVERTENCIA_RIESGO)
    if fila.get("fuera_ventana_estacional"):
        pdf.drawString(margen, 130, ADVERTENCIA_VENTANA)
    pdf.setFont("Helvetica", 7)
    pdf.setFillColor(colors.HexColor("#64748b"))
    pdf.drawString(margen, 40, "SIRIF · Sistema Inteligente de Riesgo Meteorológico")


def _pagina_resumen(pdf: canvas.Canvas, filas: list[dict[str, Any]]) -> None:
    ancho_pagina, alto_pagina = A4
    margen = 42
    pdf.setFillColor(colors.HexColor("#0f172a"))
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(margen, alto_pagina - 48, "Resumen multi-zona · Riesgo Meteorológico")
    pdf.setFont("Helvetica", 8.5)
    pdf.setFillColor(colors.HexColor("#475569"))
    pdf.drawString(margen, alto_pagina - 62, f"Generado el {_fecha_generacion()}")

    resumen = {"ALTO": 0, "MEDIO": 0, "BAJO": 0}
    for fila in filas:
        nivel = fila.get("nivel_riesgo")
        if nivel in resumen:
            resumen[nivel] += 1
    pdf.setFont("Helvetica-Bold", 10)
    pdf.setFillColor(colors.HexColor("#0f172a"))
    pdf.drawString(
        margen,
        alto_pagina - 88,
        (
            f"Total de zonas: {len(filas)} · ALTO {resumen['ALTO']} · "
            f"MEDIO {resumen['MEDIO']} · BAJO {resumen['BAJO']}"
        ),
    )

    columnas = [margen, 150, 235, 305, 365, 445]
    encabezados = ["Zona", "Nivel", "Probabilidad", "Puntaje", "Vegetación"]
    y = alto_pagina - 115
    pdf.setFont("Helvetica-Bold", 8.5)
    for posicion, encabezado in zip(columnas, encabezados):
        pdf.drawString(posicion, y, encabezado)
    pdf.setStrokeColor(colors.HexColor("#cbd5e1"))
    pdf.line(margen, y - 4, ancho_pagina - margen, y - 4)
    y -= 18

    pdf.setFont("Helvetica", 8.5)
    for fila in filas:
        variables = _json_seguro(fila.get("variables"), {}) or {}
        valores = [
            fila.get("comuna") or fila.get("zona"),
            fila.get("nivel_riesgo") or "—",
            _porcentaje(fila.get("probabilidad")),
            fila.get("puntaje") if fila.get("puntaje") is not None else "—",
            variables.get("vegetacion") or "sin dato",
        ]
        if y < 220:
            pdf.showPage()
            pdf.setFont("Helvetica", 8.5)
            y = alto_pagina - 60
        for posicion, valor in zip(columnas, valores):
            pdf.drawString(posicion, y, str(valor))
        y -= 16

    pdf.setStrokeColor(colors.HexColor("#cbd5e1"))
    pdf.line(margen, 158, ancho_pagina - margen, 158)
    pdf.setFont("Helvetica-Bold", 8.5)
    pdf.setFillColor(colors.HexColor("#7f1d1d"))
    pdf.drawString(margen, 143, ADVERTENCIA_RIESGO)
    if any(fila.get("fuera_ventana_estacional") for fila in filas):
        pdf.drawString(margen, 130, ADVERTENCIA_VENTANA)
    pdf.setFont("Helvetica", 7)
    pdf.setFillColor(colors.HexColor("#64748b"))
    pdf.drawString(margen, 40, "SIRIF · Sistema Inteligente de Riesgo Meteorológico")


def _construir_pdf(filas: list[dict[str, Any]], incluir_resumen: bool = False) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, pageCompression=0)
    if incluir_resumen:
        _pagina_resumen(pdf, filas)
        pdf.showPage()
    for fila in filas:
        _pagina_ficha(pdf, fila)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _filas_para_salida(zonas_param: str | None) -> list[dict[str, Any]]:
    solicitadas = [zona.strip() for zona in (zonas_param or "").split(",") if zona.strip()]
    if not solicitadas:
        return consultar_ultimas_zonas(limite_por_zona=1)
    filas = consultar_ultimas_zonas(limite_por_zona=1, zonas=solicitadas)
    por_zona = {fila["zona"]: fila for fila in filas}
    faltantes = [zona for zona in solicitadas if zona not in por_zona]
    if faltantes:
        raise HTTPException(status_code=422, detail=f"zona '{faltantes[0]}' no encontrada")
    return [por_zona[zona] for zona in solicitadas]


def _fila_csv(fila: dict[str, Any]) -> list[str]:
    variables = _json_seguro(fila.get("variables"), {}) or {}
    probabilidad = fila.get("probabilidad")
    return [
        str(fila.get("zona") or ""),
        str(fila.get("comuna") or ""),
        str(fila.get("nivel_riesgo") or ""),
        "" if probabilidad is None else str(round(float(probabilidad), 4)),
        "" if fila.get("puntaje") is None else str(fila.get("puntaje")),
        _iso(fila.get("fecha_evaluacion")) or "",
        str(fila.get("version_modelo") or ""),
        "true" if fila.get("fuera_ventana_estacional") else "false",
        "" if variables.get("temperatura") is None else str(variables.get("temperatura")),
        "" if variables.get("humedad") is None else str(variables.get("humedad")),
        "" if variables.get("viento") is None else str(variables.get("viento")),
        "" if variables.get("vegetacion") is None else str(variables.get("vegetacion")),
    ]


@app.get("/api/reportes/{zona}/pdf")
def reporte_zona_pdf(
    zona: str,
    usuario_actual: dict = Depends(require_rol("admin", "analista", "visualizador")),
):
    """Ficha PDF en español con causas, mapa local, recomendaciones y advertencias."""
    filas = consultar_ultimas_zonas(limite_por_zona=1, zonas=[zona])
    if not filas:
        raise HTTPException(status_code=404, detail=f"zona '{zona}' no encontrada")
    fila = filas[0]
    registrar_bitacora(
        usuario_actual.get("username"),
        zona,
        "reporte",
        f"Descargó el reporte PDF de {zona}",
        {"formato": "pdf", "tipo": "ficha_zona", "version_modelo": fila.get("version_modelo")},
    )
    contenido = _construir_pdf([fila])
    nombre = f"reporte_riesgo_{zona}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.pdf"
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@app.get("/api/exportar/csv")
def exportar_csv(
    zonas: str | None = None,
    usuario_actual: dict = Depends(require_rol("admin", "analista", "visualizador")),
):
    """Exporta las 12 columnas fijas del contrato de Fase 4."""
    filas = _filas_para_salida(zonas)
    buffer = io.StringIO()
    escritor = csv.writer(buffer, lineterminator="\n")
    escritor.writerow(COLUMNAS_CSV)
    for fila in filas:
        escritor.writerow(_fila_csv(fila))
    registrar_bitacora(
        usuario_actual.get("username"),
        None,
        "exportacion",
        f"Exportó {len(filas)} zonas a CSV",
        {"formato": "csv", "zonas": [fila.get("zona") for fila in filas]},
    )
    nombre = f"riesgo_meteorologico_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@app.get("/api/exportar/pdf")
def exportar_pdf(
    zonas: str | None = None,
    usuario_actual: dict = Depends(require_rol("admin", "analista", "visualizador")),
):
    """PDF multi-zona: resumen del panel + una ficha por zona."""
    filas = _filas_para_salida(zonas)
    registrar_bitacora(
        usuario_actual.get("username"),
        None,
        "exportacion",
        f"Exportó {len(filas)} zonas a PDF",
        {"formato": "pdf", "zonas": [fila.get("zona") for fila in filas]},
    )
    contenido = _construir_pdf(filas, incluir_resumen=True)
    nombre = f"riesgo_meteorologico_{datetime.now(timezone.utc).strftime('%Y%m%d')}.pdf"
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


# --- Asistente explicativo determinista ------------------------------------


def _catalogo_zonas() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Índice alias→zona. Tolera la ausencia de PostgreSQL en tests/arranque."""
    try:
        filas = consultar_ultimas_zonas(limite_por_zona=1)
    except Exception as error:  # noqa: BLE001
        print(f"[API] Asistente sin datos de zonas: {error}", flush=True)
        filas = []

    indice: dict[str, dict[str, Any]] = {}
    for fila in filas:
        zona = fila.get("zona")
        if not zona:
            continue
        entrada = {
            "zona": zona,
            "comuna": fila.get("comuna") or zona.replace("_", " ").title(),
            "fila": fila,
        }
        for alias in (zona, fila.get("comuna")):
            if alias:
                indice[_normalizar_texto(alias)] = entrada

    for zona, comuna in ZONAS_BASE.items():
        entrada = {"zona": zona, "comuna": comuna, "fila": None}
        for alias in (zona, comuna):
            indice.setdefault(_normalizar_texto(alias), entrada)
    return indice, filas


def _es_fuera_de_dominio(texto: str) -> bool:
    claves = (
        "riesgo", "nivel", "factor", "explica", "explicacion", "por que",
        "compar", "historial", "evolucion", "recomend", "meteorolog",
        "incendio", "peligro", "alerta", "zona", "comuna", "probabilidad",
    )
    return not any(clave in texto for clave in claves)


def _detectar_intencion(texto: str) -> str:
    if "compar" in texto:
        return "comparar"
    if any(clave in texto for clave in ("historial", "evolucion", "evoluciona", "cambio", "cambios")):
        return "historial"
    if "recomend" in texto:
        return "recomendaciones"
    if any(clave in texto for clave in ("factor", "factores", "causa", "causas", "explica", "por que", "porque")):
        return "explicacion"
    return "nivel"


def _zonas_en_texto(texto: str, indice: dict[str, dict[str, Any]]) -> list[str]:
    encontradas: list[str] = []
    for alias in sorted(indice, key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", texto):
            zona = indice[alias]["zona"]
            if zona not in encontradas:
                encontradas.append(zona)
    return encontradas


def _extraer_zona_desconocida(texto: str) -> str | None:
    for palabra in re.findall(r"[a-z0-9]+", texto):
        if len(palabra) < 3 or palabra.isdigit() or palabra in PALABRAS_NO_ZONA:
            continue
        return palabra
    return None


def _intencion_extrema(texto: str) -> str | None:
    """Detecta preguntas de extremo ("mayor"/"menor riesgo") sin zona explícita."""
    if any(clave in texto for clave in ("mayor", "maximo", "mas alto", "mas alta", "peor", "top")):
        return "mayor"
    if any(clave in texto for clave in ("menor", "minimo", "mas bajo", "mas baja", "mas seguro")):
        return "menor"
    return None


def _respuesta_extrema(sentido: str, filas: list[dict[str, Any]]) -> dict[str, Any]:
    con_prob = [fila for fila in filas if fila.get("probabilidad") is not None]
    if not con_prob:
        return {
            "respuesta": "No tengo probabilidades para comparar las zonas.",
            "datos_utilizados": {},
            "zona": None,
            "intencion": "extrema",
            "fuera_ventana_estacional": False,
        }
    reverse = sentido == "mayor"
    con_prob.sort(key=lambda fila: float(fila.get("probabilidad") or 0.0), reverse=reverse)
    top = con_prob[0]
    comuna = top.get("comuna") or str(top.get("zona") or "").replace("_", " ").title()
    adjetivo = "mayor" if sentido == "mayor" else "menor"
    return {
        "respuesta": (
            f"La zona con {adjetivo} riesgo meteorológico es {comuna} "
            f"(nivel {top.get('nivel_riesgo') or 'sin nivel'}, probabilidad "
            f"{_porcentaje(top.get('probabilidad'))}). {ADVERTENCIA_RIESGO}"
        ),
        "datos_utilizados": {
            "zona": top.get("zona"),
            "probabilidad": top.get("probabilidad"),
        },
        "zona": top.get("zona"),
        "intencion": "extrema",
        "fuera_ventana_estacional": bool(top.get("fuera_ventana_estacional")),
    }


def _datos_utilizados(fila: dict[str, Any]) -> dict[str, Any]:
    return {
        "zona": fila.get("zona"),
        "nivel_riesgo": fila.get("nivel_riesgo"),
        "probabilidad": fila.get("probabilidad"),
        "factores_predominantes": _json_seguro(fila.get("factores_predominantes"), []) or [],
    }


def _frase_base(fila: dict[str, Any]) -> str:
    comuna = fila.get("comuna") or str(fila.get("zona") or "").replace("_", " ").title()
    nivel = fila.get("nivel_riesgo") or "sin nivel"
    return (
        f"{comuna} presenta riesgo meteorológico {nivel} "
        f"(probabilidad {_porcentaje(fila.get('probabilidad'))})."
    )


def _respuesta_detalle(zona: str, indice: dict[str, dict[str, Any]], intencion: str) -> dict[str, Any]:
    entrada = next(
        (valor for valor in indice.values() if valor.get("zona") == zona),
        None,
    )
    fila = (entrada or {}).get("fila")
    if not fila:
        return {
            "respuesta": f"No tengo datos para la zona '{zona}'.",
            "datos_utilizados": {},
            "zona": zona,
            "intencion": intencion,
            "fuera_ventana_estacional": False,
        }

    factores = _json_seguro(fila.get("factores_predominantes"), [])
    if not isinstance(factores, list):
        factores = []
    variables = _json_seguro(fila.get("variables"), {}) or {}
    nivel = fila.get("nivel_riesgo")
    partes = [_frase_base(fila)]

    if intencion == "historial":
        try:
            serie = consultar_historial(zona, limite=10)
        except Exception:  # noqa: BLE001
            serie = []
        if len(serie) >= 2:
            actual, anterior = serie[0], serie[1]
            partes.append(
                f"El historial registra {len(serie)} evaluaciones; la anterior fue "
                f"{anterior.get('nivel_riesgo')} ({_porcentaje(anterior.get('probabilidad'))})."
            )
        else:
            partes.append("No hay historial suficiente para mostrar una evolución.")
    elif intencion == "recomendaciones":
        recomendaciones = construir_recomendaciones(nivel, factores, variables)
        if recomendaciones:
            partes.append(
                "Recomendaciones: "
                + " ".join(
                    f"{recomendacion['prioridad']}. {recomendacion['texto']}"
                    for recomendacion in recomendaciones[:5]
                )
            )
        else:
            partes.append("No hay recomendaciones específicas para el nivel actual.")
    else:
        explicacion = componer_explicacion(nivel or "", factores)
        partes.append(explicacion)
        if not factores:
            partes.append(
                "No hay factores predominantes detallados para esta evaluación."
            )

    partes.append(
        "Estimación para condiciones similares a jul–sep 2026; no es pronóstico de ocurrencia."
    )
    if fila.get("fuera_ventana_estacional"):
        partes.append(f"{ADVERTENCIA_VENTANA}.")

    return {
        "respuesta": " ".join(partes),
        "datos_utilizados": _datos_utilizados(fila),
        "zona": zona,
        "intencion": intencion,
        "fuera_ventana_estacional": bool(fila.get("fuera_ventana_estacional")),
    }


def _respuesta_comparacion(zonas: list[str], indice: dict[str, dict[str, Any]]) -> dict[str, Any]:
    entradas = {}
    for zona in zonas[:3]:
        entrada = next((valor for valor in indice.values() if valor.get("zona") == zona), None)
        if entrada and entrada.get("fila"):
            entradas[zona] = entrada
    if len(entradas) >= 2:
        detalle = " ".join(
            f"{entrada['comuna']}: riesgo meteorológico {entrada['fila'].get('nivel_riesgo')} "
            f"({_porcentaje(entrada['fila'].get('probabilidad'))})."
            for entrada in entradas.values()
        )
        return {
            "respuesta": (
                f"Comparación de zonas: {detalle} "
                f"{ADVERTENCIA_RIESGO}"
            ),
            "datos_utilizados": {
                "zonas": [entrada["fila"].get("zona") for entrada in entradas.values()],
                "niveles": {
                    entrada["fila"].get("zona"): entrada["fila"].get("nivel_riesgo")
                    for entrada in entradas.values()
                },
            },
            "zona": next(iter(entradas)),
            "intencion": "comparar",
            "fuera_ventana_estacional": any(
                entrada["fila"].get("fuera_ventana_estacional")
                for entrada in entradas.values()
            ),
        }
    return _respuesta_detalle(zonas[0], indice, "explicacion")


def responder_asistente(pregunta: str) -> dict[str, Any]:
    """Flujo determinista local; nunca depende de un LLM ni de red externa."""
    texto = _normalizar_texto(pregunta)
    indice, filas = _catalogo_zonas()

    if _es_fuera_de_dominio(texto):
        return {
            "respuesta": RESPUESTA_FUERA_DOMINIO,
            "datos_utilizados": {},
            "zona": None,
            "intencion": "fuera_dominio",
            "fuera_ventana_estacional": False,
        }

    intencion = _detectar_intencion(texto)
    zonas = _zonas_en_texto(texto, indice)
    if not zonas:
        extrema = _intencion_extrema(texto)
        if extrema and filas and intencion != "explicacion":
            return _respuesta_extrema(extrema, filas)
        candidata = _extraer_zona_desconocida(texto)
        if candidata:
            return {
                "respuesta": f"No tengo datos para la zona '{candidata}'.",
                "datos_utilizados": {},
                "zona": candidata,
                "intencion": intencion,
                "fuera_ventana_estacional": False,
            }
        if not filas:
            return {
                "respuesta": "No tengo datos de zonas para responder esa consulta.",
                "datos_utilizados": {},
                "zona": None,
                "intencion": intencion,
                "fuera_ventana_estacional": False,
            }
        resumen = " ".join(_frase_base(fila) for fila in filas[:5])
        return {
            "respuesta": f"Estado actual de las zonas: {resumen} {ADVERTENCIA_RIESGO}",
            "datos_utilizados": {"zonas": [fila.get("zona") for fila in filas]},
            "zona": None,
            "intencion": "resumen",
            "fuera_ventana_estacional": any(
                fila.get("fuera_ventana_estacional") for fila in filas
            ),
        }

    if intencion == "comparar":
        return _respuesta_comparacion(zonas, indice)
    return _respuesta_detalle(zonas[0], indice, intencion)


def _reformular_con_llm(id_bitacora: int | None, pregunta: str, respuesta_local: str) -> None:
    """Reformulación opcional en segundo plano; jamás bloquea ni reemplaza el fallback."""
    if not (LLM_URL and LLM_API_KEY):
        return
    try:
        with httpx.Client(timeout=20.0) as cliente:
            respuesta = cliente.post(
                LLM_URL,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": LLM_MODEL,
                    "temperature": 0.2,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Reformula en español, sin agregar datos nuevos, esta respuesta "
                                "sobre riesgo meteorológico. No cambies cifras ni niveles."
                            ),
                        },
                        {"role": "user", "content": respuesta_local},
                    ],
                },
            )
            respuesta.raise_for_status()
            datos = respuesta.json()
        texto = (
            datos.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        if not texto or id_bitacora is None:
            return
        with cursor_db(commit=True) as cur:
            cur.execute(
                """
                UPDATE bitacora_decision
                SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                WHERE id_bitacora = %s
                """,
                (
                    Json(
                        {
                            "reformulacion_llm": texto,
                            "llm_modelo": LLM_MODEL,
                            "llm_estado": "completado",
                        }
                    ),
                    id_bitacora,
                ),
            )
    except Exception as error:  # noqa: BLE001
        print(f"[API] Reformulación LLM omitida: {error}", flush=True)


@app.post("/api/asistente/preguntar")
def preguntar_asistente(
    peticion: AsistenteRequest,
    background_tasks: BackgroundTasks,
    usuario_actual: dict = Depends(require_rol("admin", "analista", "visualizador")),
):
    """Responde siempre desde datos reales; el LLM solo reformula en background."""
    pregunta = peticion.pregunta.strip()
    if not pregunta:
        raise HTTPException(status_code=422, detail="la pregunta no puede estar vacía")

    resultado = responder_asistente(pregunta)
    llm_configurado = bool(LLM_URL and LLM_API_KEY)
    id_bitacora = registrar_bitacora(
        usuario_actual.get("username"),
        resultado.get("zona"),
        "consulta",
        f"Consultó al asistente: {pregunta[:140]}",
        {
            "pregunta": pregunta,
            "fuente": "local-determinista",
            "intencion": resultado.get("intencion"),
            "respuesta": resultado.get("respuesta"),
            "llm": "pendiente" if llm_configurado else "no_configurado",
        },
    )
    if llm_configurado:
        background_tasks.add_task(
            _reformular_con_llm,
            id_bitacora,
            pregunta,
            resultado.get("respuesta") or "",
        )

    return {
        "pregunta": pregunta,
        "respuesta": resultado.get("respuesta"),
        "fuente": "local-determinista",
        "datos_utilizados": resultado.get("datos_utilizados") or {},
        "advertencia": ADVERTENCIA_RIESGO,
        "fuera_ventana_estacional": bool(resultado.get("fuera_ventana_estacional")),
    }


Instrumentator().instrument(app).expose(app)
