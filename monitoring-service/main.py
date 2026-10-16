import pika
import json
import time
import os
from datetime import datetime

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "sirif")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "sirif2026")
INTERVALO = int(os.getenv("INTERVALO", "30"))

ZONAS = ["quilpue", "vina_del_mar", "valparaiso"]


def conectar_con_reintentos(max_intentos=10, espera=5):
    """Intenta conectarse a RabbitMQ con reintentos"""
    for intento in range(1, max_intentos + 1):
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBITMQ_HOST,
                    credentials=pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS),
                    heartbeat=600,
                    blocked_connection_timeout=300
                )
            )
            print(f"[Monitoring]  Conectado a RabbitMQ", flush=True)
            return connection
        except pika.exceptions.AMQPConnectionError as e:
            print(f"[Monitoring] Intento {intento}/{max_intentos}: {e}", flush=True)
            if intento < max_intentos:
                time.sleep(espera)
            else:
                raise Exception("No se pudo conectar a RabbitMQ")


def publicar_evento(connection, routing_key: str, evento: dict):
    """Publica un evento en RabbitMQ"""
    try:
        channel = connection.channel()
        channel.exchange_declare(
            exchange="sirif_eventos",
            exchange_type="topic",
            durable=True
        )
        channel.basic_publish(
            exchange="sirif_eventos",
            routing_key=routing_key,
            body=json.dumps(evento),
            properties=pika.BasicProperties(delivery_mode=2)
        )
        print(f"    Evento publicado: {routing_key}", flush=True)
    except Exception as e:
        print(f"    Error publicando evento: {e}", flush=True)


def monitorear():
    print(f"[{datetime.now().isoformat()}] Monitoring Service iniciado", flush=True)
    print(f"Zonas: {ZONAS}", flush=True)
    print(f"Intervalo: {INTERVALO} segundos\n", flush=True)

    connection = conectar_con_reintentos()

    while True:
        print(f"[{datetime.now().isoformat()}] Ciclo de monitoreo...", flush=True)

        for zona in ZONAS:
            evento = {
                "tipo": "zona_requiere_evaluacion",
                "zona": zona,
                "timestamp": datetime.now().isoformat()
            }
            publicar_evento(connection, "monitoreo.zona", evento)

        print(f"Esperando {INTERVALO} segundos...\n", flush=True)
        time.sleep(INTERVALO)


if __name__ == "__main__":
    monitorear()