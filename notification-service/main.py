import pika
import json
import os
import time
from datetime import datetime

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "sirif")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "sirif2026")


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
            print(f"[Notification] Intento {intento}/{max_intentos}: {e}", flush=True)
            if intento < max_intentos:
                time.sleep(espera)
            else:
                raise


def callback(ch, method, properties, body):
    try:
        evento = json.loads(body)
        zona = evento.get("zona", "?")
        nivel = evento.get("nivel_riesgo", "?")

        print(f"[{datetime.now().isoformat()}] Evento recibido: {zona}", flush=True)

        if nivel == "ALTO":
            print(f"   ALERTA: Riesgo ALTO en {zona}", flush=True)
        elif nivel == "MEDIO":
            print(f"    Riesgo MEDIO en {zona}", flush=True)
        else:
            print(f"   Riesgo BAJO en {zona}", flush=True)

        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        print(f"   Error: {e}", flush=True)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)


def consumir():
    print("[Notification Service] Iniciando...", flush=True)
    connection = conectar_con_reintentos()
    channel = connection.channel()
    channel.exchange_declare(exchange="sirif_eventos", exchange_type="topic", durable=True)

    result = channel.queue_declare(queue="notification_queue", durable=True)
    queue_name = result.method.queue
    channel.queue_bind(
        exchange="sirif_eventos",
        queue=queue_name,
        routing_key="riesgo.calculado"
    )

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=callback)

    print("[Notification Service] Esperando eventos...", flush=True)
    channel.start_consuming()


if __name__ == "__main__":
    consumir()