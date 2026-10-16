from fastapi import FastAPI
from datetime import datetime
import psycopg2
import httpx
import pika
import json
import os
import threading
import time
from prometheus_fastapi_instrumentator import Instrumentator
app = FastAPI(title="API Service - SIRIF")

DATABASE_URL = os.getenv("DATABASE_URL")
DATA_SERVICE_URL = os.getenv("DATA_SERVICE_URL")
RISK_SERVICE_URL = os.getenv("RISK_SERVICE_URL")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST")
RABBITMQ_USER = os.getenv("RABBITMQ_USER")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS")


def get_db():
    return psycopg2.connect(DATABASE_URL)


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


def inferir_y_guardar(zona: str):
    """Fuerza inferencia y guarda en caché, publica evento riesgo.calculado"""
    with httpx.Client(timeout=5.0) as client:
        datos = client.get(f"{DATA_SERVICE_URL}/datos/{zona}").json()
        resultado = client.post(
            f"{RISK_SERVICE_URL}/clasificar",
            json=datos["variables"]
        ).json()

    # Guardar en BD
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO resultado_riesgo (zona, nivel_riesgo, puntaje, puntaje_maximo)
        VALUES (%s, %s, %s, %s)
    """, (zona, resultado["nivel_riesgo"], resultado["puntaje"], resultado["puntaje_maximo"]))
    conn.commit()
    cur.close()
    conn.close()

    # Publicar evento riesgo.calculado
    conn_mq = conectar_con_reintentos()
    evento = {
        "zona": zona,
        "nivel_riesgo": resultado["nivel_riesgo"],
        "puntaje": resultado["puntaje"],
        "timestamp": datetime.now().isoformat()
    }
    publicar_evento(conn_mq, "riesgo.calculado", evento)
    conn_mq.close()


@app.on_event("startup")
def startup_event():
    """Inicia el consumidor en un hilo separado"""
    thread = threading.Thread(target=consumir_monitoreo, daemon=True)
    thread.start()


@app.get("/health")
def health():
    return {"status": "ok", "servicio": "api-service"}

@app.get("/api/riesgo/todas")
def obtener_todas_las_zonas():
    """Devuelve el riesgo de todas las zonas (último resultado por zona)"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (zona)
            zona,
            nivel_riesgo,
            puntaje,
            puntaje_maximo,
            fecha_evaluacion
        FROM resultado_riesgo
        ORDER BY zona, fecha_evaluacion DESC
    """)
    resultados = cur.fetchall()
    cur.close()
    conn.close()
    
    return {
        "total": len(resultados),
        "zonas": [
            {
                "zona": r[0],
                "nivel_riesgo": r[1],
                "puntaje": r[2],
                "puntaje_maximo": r[3],
                "fecha_evaluacion": r[4].isoformat()
            }
            for r in resultados
        ]
    }
    
@app.get("/api/riesgo/{zona}")
def consultar_riesgo(zona: str):
    # 1. Buscar en caché
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT nivel_riesgo, puntaje, puntaje_maximo, fecha_evaluacion
        FROM resultado_riesgo
        WHERE zona = %s AND fecha_evaluacion > NOW() - INTERVAL '6 hours'
        ORDER BY fecha_evaluacion DESC LIMIT 1
    """, (zona,))
    resultado = cur.fetchone()
    cur.close()
    conn.close()

    if resultado:
        return {
            "zona": zona,
            "nivel_riesgo": resultado[0],
            "puntaje": resultado[1],
            "puntaje_maximo": resultado[2],
            "fuente": "cache",
            "fecha_evaluacion": resultado[3].isoformat()
        }

    # 2. Inferir on-demand
    inferir_y_guardar(zona)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT nivel_riesgo, puntaje, puntaje_maximo
        FROM resultado_riesgo WHERE zona = %s
        ORDER BY fecha_evaluacion DESC LIMIT 1
    """, (zona,))
    r = cur.fetchone()
    cur.close()
    conn.close()

    return {
        "zona": zona,
        "nivel_riesgo": r[0],
        "puntaje": r[1],
        "puntaje_maximo": r[2],
        "fuente": "on-demand"
    }
    
Instrumentator().instrument(app).expose(app)
