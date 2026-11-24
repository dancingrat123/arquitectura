"""Monitoring Service de SIRIF.

Además de disparar la ingesta batch y publicar `monitoreo.zona`, deja un
heartbeat persistido en `estado_servicio` para que la API pueda mostrar el
estado de los servicios sin hacer llamadas síncronas durante un request.
"""

import json
import os
import time
from datetime import datetime

import httpx
import pika
import psycopg2
from psycopg2.extras import RealDictCursor


def _env_requerido(nombre: str) -> str:
    valor = os.getenv(nombre)
    if not valor:
        raise RuntimeError(f"Falta la variable de entorno obligatoria: {nombre}")
    return valor


DATABASE_URL = _env_requerido("DATABASE_URL")
RABBITMQ_HOST = _env_requerido("RABBITMQ_HOST")
RABBITMQ_USER = _env_requerido("RABBITMQ_USER")
RABBITMQ_PASS = _env_requerido("RABBITMQ_PASS")
DATA_SERVICE_URL = os.getenv("DATA_SERVICE_URL", "http://data-service:8000")
API_SERVICE_URL = os.getenv("API_SERVICE_URL", "http://api-service:8000")
RISK_SERVICE_URL = os.getenv("RISK_SERVICE_URL", "http://risk-service:8001")
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")
NDVI_URL = os.getenv("NDVI_URL", "")
NDVI_FILE = os.getenv("NDVI_FILE", "/app/datos/ml/ndvi_ejemplo.json")

# 30 s en desarrollo, 3600 s en producción (configurable por .env).
INTERVALO = int(os.getenv("MONITORING_INTERVAL", os.getenv("INTERVALO", "3600")))
TIMEOUT_SYNC = float(os.getenv("SYNC_TIMEOUT", "120"))
TIMEOUT_HEALTH = float(os.getenv("HEALTH_TIMEOUT", "5"))

ZONAS_CONOCIDAS = ["quilpue", "vina_del_mar", "valparaiso"]


def conectar_con_reintentos(max_intentos=10, espera=5):
    """Intenta conectarse a RabbitMQ con reintentos."""
    for intento in range(1, max_intentos + 1):
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBITMQ_HOST,
                    credentials=pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS),
                    heartbeat=600,
                    blocked_connection_timeout=300,
                )
            )
            print("[Monitoring] Conectado a RabbitMQ", flush=True)
            return connection
        except pika.exceptions.AMQPConnectionError as error:
            print(f"[Monitoring] Intento {intento}/{max_intentos}: {error}", flush=True)
            if intento < max_intentos:
                time.sleep(espera)
    raise RuntimeError("No se pudo conectar a RabbitMQ")


def conectar_db_con_reintentos(max_intentos=10, espera=2):
    """Conexión PostgreSQL con reintentos idéntica a la de RabbitMQ."""
    for intento in range(1, max_intentos + 1):
        try:
            conexion = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            conexion.autocommit = True
            print("[Monitoring] Conectado a PostgreSQL", flush=True)
            return conexion
        except psycopg2.Error as error:
            print(f"[Monitoring] PostgreSQL intento {intento}/{max_intentos}: {error}", flush=True)
            if intento < max_intentos:
                time.sleep(espera)
    raise RuntimeError("No se pudo conectar a PostgreSQL")


def esperar_esquema_heartbeat(max_intentos=20, espera=3):
    """Espera a que data-service aplique la migración de `estado_servicio`.

    En un arranque en frío monitoring-service puede adelantarse al bootstrap
    de data-service; sin esta espera el primer heartbeat se perdería hasta el
    siguiente ciclo (una hora por defecto).
    """
    for intento in range(1, max_intentos + 1):
        conexion = None
        try:
            conexion = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            conexion.autocommit = True
            with conexion.cursor() as cur:
                cur.execute("SELECT 1 FROM estado_servicio LIMIT 1")
            return conexion
        except psycopg2.Error as error:
            print(
                f"[Monitoring] Esquema de heartbeat no disponible "
                f"({intento}/{max_intentos}): {error}",
                flush=True,
            )
            if conexion is not None:
                try:
                    conexion.close()
                except Exception:  # noqa: BLE001
                    pass
            if intento < max_intentos:
                time.sleep(espera)
    return None


def sincronizar_ingesta() -> dict:
    """Dispara la ingesta batch de Open-Meteo en data-service."""
    headers = {"X-Internal-Token": INTERNAL_TOKEN} if INTERNAL_TOKEN else {}
    with httpx.Client(timeout=TIMEOUT_SYNC) as client:
        respuesta = client.post(f"{DATA_SERVICE_URL}/datos/sincronizar", headers=headers)
        respuesta.raise_for_status()
        return respuesta.json()


def _leer_capas_ndvi() -> list[dict]:
    """Lee el lote NDVI configurado; vacío si no hay fuente disponible."""
    if NDVI_URL:
        with httpx.Client(timeout=TIMEOUT_SYNC) as client:
            respuesta = client.get(NDVI_URL)
            respuesta.raise_for_status()
            contenido = respuesta.json()
    elif NDVI_FILE and os.path.exists(NDVI_FILE):
        with open(NDVI_FILE, "r", encoding="utf-8") as archivo:
            contenido = json.load(archivo)
    else:
        return []
    if isinstance(contenido, dict):
        contenido = contenido.get("capas") or []
    return contenido if isinstance(contenido, list) else []


def ingestar_vegetacion() -> dict:
    """Ingesta batch de NDVI hacia data-service (nunca en un request de usuario)."""
    capas = _leer_capas_ndvi()
    if not capas:
        return {"ok": True, "registros": 0, "detalle": "sin fuente NDVI configurada"}
    with httpx.Client(timeout=TIMEOUT_SYNC) as client:
        respuesta = client.post(
            f"{DATA_SERVICE_URL}/datos/vegetacion",
            headers={"X-Internal-Token": INTERNAL_TOKEN},
            json={"capas": capas},
        )
        respuesta.raise_for_status()
        resultado = respuesta.json()
    print(f"[Monitoring] NDVI ingestado: {resultado.get('registros', 0)} capas", flush=True)
    return resultado


def publicar_evento(connection, routing_key: str, evento: dict):
    try:
        channel = connection.channel()
        channel.exchange_declare(exchange="sirif_eventos", exchange_type="topic", durable=True)
        channel.basic_publish(
            exchange="sirif_eventos",
            routing_key=routing_key,
            body=json.dumps(evento),
            properties=pika.BasicProperties(delivery_mode=2),
        )
        print(f"    Evento publicado: {routing_key} ({evento['zona']})", flush=True)
    except Exception as error:  # noqa: BLE001
        print(f"    Error publicando evento: {error}", flush=True)
        raise


# ---------------------------------------------------------------------------
# Heartbeat en `estado_servicio` (Fase 2)
# ---------------------------------------------------------------------------


def guardar_estado(
    cur,
    servicio: str,
    estado: str,
    latencia_ms: int | None = None,
    detalle: dict | str | None = None,
) -> None:
    """UPSERT idempotente del estado de un servicio."""
    detalle_texto = json.dumps(detalle, ensure_ascii=False) if isinstance(detalle, dict) else detalle
    cur.execute(
        """
        INSERT INTO estado_servicio (servicio, estado, detalle, ultima_comprobacion, latencia_ms)
        VALUES (%s, %s, %s, NOW(), %s)
        ON CONFLICT (servicio) DO UPDATE
            SET estado = EXCLUDED.estado,
                detalle = EXCLUDED.detalle,
                ultima_comprobacion = NOW(),
                latencia_ms = EXCLUDED.latencia_ms
        """,
        (servicio, estado, detalle_texto, latencia_ms),
    )


def medir_servicio(nombre: str, url: str) -> dict:
    """Mide latencia y estado de un servicio interno vía HTTP."""
    inicio = time.perf_counter()
    try:
        with httpx.Client(timeout=TIMEOUT_HEALTH) as client:
            respuesta = client.get(url)
        latencia = int(round((time.perf_counter() - inicio) * 1000))
        estado = "ok" if respuesta.status_code < 400 else "degradado"
        detalle = {"http_status": respuesta.status_code}
        if nombre == "risk-service":
            try:
                cuerpo = respuesta.json()
                detalle["modelo_cargado"] = bool(cuerpo.get("modelo_cargado"))
                detalle["version_modelo"] = cuerpo.get("version_modelo")
            except ValueError:
                pass
        return {"servicio": nombre, "estado": estado, "latencia_ms": latencia, "detalle": detalle}
    except Exception as error:  # noqa: BLE001
        latencia = int(round((time.perf_counter() - inicio) * 1000))
        return {
            "servicio": nombre,
            "estado": "caido",
            "latencia_ms": latencia,
            "detalle": {"error": str(error)},
        }


def estado_open_meteo(cur) -> dict:
    """Reutiliza el estado de la última ingesta registrada por data-service."""
    cur.execute(
        """
        SELECT estado, ultimo_exito, detalle
        FROM ingesta_control
        WHERE id_proceso = 'openmeteo'
        """
    )
    fila = cur.fetchone()
    if not fila:
        return {
            "servicio": "open-meteo",
            "estado": "degradado",
            "detalle": {"detalle": "sin registros de ingesta"},
        }
    return {
        "servicio": "open-meteo",
        "estado": fila.get("estado") or "degradado",
        "detalle": {
            "ultimo_exito": fila.get("ultimo_exito").isoformat() if fila.get("ultimo_exito") else None,
            "detalle": fila.get("detalle"),
        },
    }


def _medir_postgres(conn_db) -> dict:
    """Mide la latencia real de PostgreSQL con un `SELECT 1` (no un 'ok' fijo)."""
    inicio = time.perf_counter()
    try:
        with conn_db.cursor() as cur:
            cur.execute("SELECT 1")
        latencia = int(round((time.perf_counter() - inicio) * 1000))
        return {
            "servicio": "postgres",
            "estado": "ok",
            "latencia_ms": latencia,
            "detalle": None,
        }
    except Exception as error:  # noqa: BLE001
        return {
            "servicio": "postgres",
            "estado": "caido",
            "latencia_ms": None,
            "detalle": {"error": str(error)},
        }


def actualizar_estado_servicios(conn_db, connection) -> list[dict]:
    """Escribe el heartbeat de los servicios internos y de la ingesta."""
    estados = [
        medir_servicio("api-service", f"{API_SERVICE_URL}/health"),
        medir_servicio("data-service", f"{DATA_SERVICE_URL}/"),
        medir_servicio("risk-service", f"{RISK_SERVICE_URL}/"),
    ]
    with conn_db.cursor(cursor_factory=RealDictCursor) as cur:
        estados.append(estado_open_meteo(cur))
        estados.append(_medir_postgres(conn_db))
        estados.append(
            {
                "servicio": "rabbitmq",
                "estado": "ok"
                if connection is not None and not connection.is_closed
                else "caido",
                "latencia_ms": None,
            }
        )
        for estado in estados:
            guardar_estado(
                cur,
                estado["servicio"],
                estado["estado"],
                latencia_ms=estado.get("latencia_ms"),
                detalle=estado.get("detalle"),
            )
    print(
        "[Monitoring] Heartbeat: "
        + ", ".join(f"{e['servicio']}={e['estado']}" for e in estados),
        flush=True,
    )
    return estados


def monitorear():
    print(f"[{datetime.now().isoformat()}] Monitoring Service iniciado", flush=True)
    print(f"Data service: {DATA_SERVICE_URL}", flush=True)
    print(f"Intervalo: {INTERVALO} segundos\n", flush=True)

    connection = None
    conn_db = None
    if os.getenv("HEARTBEAT_ON_STARTUP", "true").lower() in ("1", "true", "yes"):
        conn_db = esperar_esquema_heartbeat()

    espera_ciclo = INTERVALO
    while True:
        print(f"[{datetime.now().isoformat()}] Ciclo de monitoreo...", flush=True)

        try:
            resultado = sincronizar_ingesta()
        except Exception as error:  # noqa: BLE001
            print(f"[Monitoring] Ingesta no disponible: {error}", flush=True)
            resultado = {"ok": False, "error": str(error)}

        try:
            ingestar_vegetacion()
        except Exception as error:  # noqa: BLE001
            print(f"[Monitoring] Ingesta NDVI no disponible: {error}", flush=True)

        zonas = (
            resultado.get("zonas_actualizadas")
            or resultado.get("zonas_conocidas")
            or ZONAS_CONOCIDAS
        )
        motivo = "ingesta_ok" if resultado.get("ok") else "fallback_ultimo_valor"
        if not resultado.get("ok"):
            print(f"[Monitoring] Usando último valor válido: {resultado.get('error')}", flush=True)

        try:
            if connection is None or connection.is_closed:
                connection = conectar_con_reintentos()
            for zona in zonas:
                evento = {
                    "tipo": "zona_requiere_evaluacion",
                    "zona": zona,
                    "motivo": motivo,
                    "fuente_datos": resultado.get("fuente"),
                    "timestamp": datetime.now().isoformat(),
                }
                publicar_evento(connection, "monitoreo.zona", evento)
        except Exception as error:  # noqa: BLE001
            print(f"[Monitoring] Error publicando, se reintentará: {error}", flush=True)
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass
            connection = None

        try:
            if conn_db is None or conn_db.closed:
                conn_db = conectar_db_con_reintentos()
            actualizar_estado_servicios(conn_db, connection)
            espera_ciclo = INTERVALO
        except Exception as error:  # noqa: BLE001
            print(f"[Monitoring] No se pudo escribir el heartbeat: {error}", flush=True)
            try:
                if conn_db is not None:
                    conn_db.close()
            except Exception:  # noqa: BLE001
                pass
            conn_db = None
            espera_ciclo = min(INTERVALO, 30)

        print(f"Esperando {espera_ciclo} segundos...\n", flush=True)
        time.sleep(espera_ciclo)


if __name__ == "__main__":
    monitorear()
