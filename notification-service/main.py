"""Notification Service de SIRIF.

Consume `riesgo.calculado`, `alerta.generada` y `notificacion.nueva`, persiste
las notificaciones en PostgreSQL y publica `notificacion.nueva` con el id
generado. También deja heartbeat en `estado_servicio`.
"""

import json
import os
import time
from datetime import datetime

import pika
import psycopg2
from psycopg2.extras import Json, RealDictCursor


def _env_requerido(nombre: str) -> str:
    valor = os.getenv(nombre)
    if not valor:
        raise RuntimeError(f"Falta la variable de entorno obligatoria: {nombre}")
    return valor


DATABASE_URL = _env_requerido("DATABASE_URL")
RABBITMQ_HOST = _env_requerido("RABBITMQ_HOST")
RABBITMQ_USER = _env_requerido("RABBITMQ_USER")
RABBITMQ_PASS = _env_requerido("RABBITMQ_PASS")

SERVICIO = "notification-service"
ROUTING_KEYS = (
    "riesgo.calculado",
    "alerta.generada",
    "bitacora.nueva",
)


def conectar_con_reintentos(max_intentos=10, espera=5):
    for intento in range(1, max_intentos + 1):
        try:
            return pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBITMQ_HOST,
                    credentials=pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS),
                    heartbeat=600,
                    blocked_connection_timeout=300,
                )
            )
        except pika.exceptions.AMQPConnectionError as e:
            print(f"[Notification] Intento {intento}/{max_intentos}: {e}", flush=True)
            if intento < max_intentos:
                time.sleep(espera)
            else:
                raise


def conectar_db_con_reintentos(max_intentos=10, espera=2):
    for intento in range(1, max_intentos + 1):
        try:
            conexion = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            conexion.autocommit = False
            print("[Notification] Conectado a PostgreSQL", flush=True)
            return conexion
        except psycopg2.Error as error:
            print(
                f"[Notification] PostgreSQL intento {intento}/{max_intentos}: {error}",
                flush=True,
            )
            if intento < max_intentos:
                time.sleep(espera)
    raise RuntimeError("No se pudo conectar a PostgreSQL")


def _nombre_zona(zona: str | None) -> str:
    if not zona:
        return "una zona"
    return str(zona).replace("_", " ").capitalize()


def notificacion_desde_evento(routing_key: str, evento: dict) -> dict | None:
    """Traduce un evento de dominio al contenido de una notificación."""
    zona = evento.get("zona")
    if routing_key == "alerta.generada":
        nivel = evento.get("nivel_nuevo")
        tipo_alerta = evento.get("tipo") or "alerta"
        if tipo_alerta == "umbral":
            titulo = f"Umbral de alerta superado en {_nombre_zona(zona)}"
        else:
            titulo = f"Cambio de nivel en {_nombre_zona(zona)}"
        return {
            "tipo": "alerta",
            "titulo": titulo,
            "mensaje": evento.get("mensaje") or f"Alerta generada para {_nombre_zona(zona)}.",
            "zona": zona,
            "nivel": nivel,
            "metadata": {
                "id_alerta": evento.get("id_alerta"),
                "tipo_alerta": tipo_alerta,
                "nivel_anterior": evento.get("nivel_anterior"),
                "probabilidad": evento.get("probabilidad"),
            },
            "usuario": None,
        }
    if routing_key == "riesgo.calculado":
        nivel = evento.get("nivel_riesgo")
        return {
            "tipo": "cambio_riesgo",
            "titulo": f"Riesgo {nivel or 'actualizado'} en {_nombre_zona(zona)}",
            "mensaje": (
                f"Se actualizó el riesgo meteorológico de {_nombre_zona(zona)}: "
                f"{nivel or 'sin nivel'}."
            ),
            "zona": zona,
            "nivel": nivel,
            "metadata": {
                "probabilidad": evento.get("probabilidad"),
                "version_modelo": evento.get("version_modelo"),
                "origen": evento.get("origen"),
            },
            "usuario": evento.get("usuario"),
        }
    if routing_key == "bitacora.nueva":
        tipo_evento = evento.get("tipo_evento") or "evento"
        return {
            "tipo": "evento",
            "titulo": f"Bitácora: {tipo_evento} en {_nombre_zona(zona)}",
            "mensaje": evento.get("detalle") or "Nueva entrada en la bitácora de decisiones.",
            "zona": zona,
            "nivel": None,
            "metadata": {
                "id_bitacora": evento.get("id_bitacora"),
                "tipo_evento": tipo_evento,
                "usuario": evento.get("usuario"),
            },
            "usuario": evento.get("usuario"),
        }
    # `notificacion.nueva` ya está persistida: se consume para no reencolar.
    return None


def insertar_notificacion(cur, datos: dict) -> dict:
    cur.execute(
        """
        INSERT INTO notificacion
            (tipo, titulo, mensaje, zona, nivel, metadata, usuario)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id_notificacion, leida, created_at
        """,
        (
            datos["tipo"],
            datos["titulo"],
            datos["mensaje"],
            datos.get("zona"),
            datos.get("nivel"),
            Json(datos.get("metadata") or {}),
            datos.get("usuario"),
        ),
    )
    fila = cur.fetchone()
    return {
        **datos,
        "id_notificacion": fila["id_notificacion"],
        "leida": bool(fila.get("leida")),
        "created_at": fila.get("created_at"),
    }


def persistir_evento(routing_key: str, evento: dict, conexion) -> dict | None:
    """Persiste la notificación derivada del evento; `None` si no aplica."""
    datos = notificacion_desde_evento(routing_key, evento)
    if datos is None:
        return None
    with conexion.cursor(cursor_factory=RealDictCursor) as cur:
        fila = insertar_notificacion(cur, datos)
    conexion.commit()
    return fila


def registrar_estado_servicio(estado: str = "ok", latencia_ms: int | None = None, detalle=None, conexion=None) -> None:
    """Heartbeat del consumidor en `estado_servicio`.

    Reutiliza una conexión abierta si se pasa `conexion`; si no, abre (y cierra)
    una propia. Evita abrir dos conexiones por evento.
    """
    propia = conexion is None
    try:
        if conexion is None:
            conexion = conectar_db_con_reintentos(max_intentos=3, espera=1)
        with conexion.cursor() as cur:
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
                (
                    SERVICIO,
                    estado,
                    json.dumps(detalle, ensure_ascii=False) if isinstance(detalle, dict) else detalle,
                    latencia_ms,
                ),
            )
        conexion.commit()
    except Exception as error:  # noqa: BLE001
        print(f"[Notification] No se pudo registrar heartbeat: {error}", flush=True)
    finally:
        if propia and conexion is not None:
            try:
                conexion.close()
            except Exception:  # noqa: BLE001
                pass


def publicar_notificacion_nueva(canal, fila: dict) -> None:
    evento = {
        "id_notificacion": fila.get("id_notificacion"),
        "tipo": fila.get("tipo"),
        "titulo": fila.get("titulo"),
        "mensaje": fila.get("mensaje"),
        "zona": fila.get("zona"),
        "nivel": fila.get("nivel"),
        "timestamp": datetime.now().isoformat(),
    }
    canal.basic_publish(
        exchange="sirif_eventos",
        routing_key="notificacion.nueva",
        body=json.dumps(evento),
        properties=pika.BasicProperties(delivery_mode=2),
    )


def callback(ch, method, properties, body):
    inicio = time.perf_counter()
    conexion = None
    try:
        evento = json.loads(body)
        routing_key = method.routing_key
        print(
            f"[{datetime.now().isoformat()}] Evento recibido: {routing_key} "
            f"({evento.get('zona', '?')})",
            flush=True,
        )

        conexion = conectar_db_con_reintentos()
        fila = persistir_evento(routing_key, evento, conexion)
        # Reutiliza la misma conexión para el heartbeat (una sola por evento).
        registrar_estado_servicio(
            "ok",
            latencia_ms=int(round((time.perf_counter() - inicio) * 1000)),
            conexion=conexion,
        )

        if fila is not None:
            publicar_notificacion_nueva(ch, fila)
            print(
                f"   Notificación #{fila['id_notificacion']} persistida",
                flush=True,
            )

        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        print(f"   Error: {e}", flush=True)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    finally:
        if conexion is not None:
            try:
                conexion.close()
            except Exception:  # noqa: BLE001
                pass


def consumir():
    print("[Notification Service] Iniciando...", flush=True)
    registrar_estado_servicio("ok")
    connection = conectar_con_reintentos()
    channel = connection.channel()
    channel.exchange_declare(exchange="sirif_eventos", exchange_type="topic", durable=True)

    result = channel.queue_declare(queue="notification_queue", durable=True)
    queue_name = result.method.queue
    for routing_key in ROUTING_KEYS:
        channel.queue_bind(
            exchange="sirif_eventos",
            queue=queue_name,
            routing_key=routing_key,
        )

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=callback)

    print("[Notification Service] Esperando eventos...", flush=True)
    channel.start_consuming()


if __name__ == "__main__":
    consumir()
