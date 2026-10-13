from fastapi import FastAPI
from datetime import datetime, timedelta
import psycopg2
import httpx
import os

app = FastAPI(title="API Service - SIRIF")

DATABASE_URL = os.getenv("DATABASE_URL")
DATA_SERVICE_URL = os.getenv("DATA_SERVICE_URL")
RISK_SERVICE_URL = os.getenv("RISK_SERVICE_URL")

def get_db():
    return psycopg2.connect(DATABASE_URL)

@app.get("/health")
def health():
    return {"status": "ok", "servicio": "api-service"}

@app.get("/api/riesgo/{zona}")
def consultar_riesgo(zona: str):
    # 1. Buscar resultado reciente en caché (< 6 horas)
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
    
    # 2. Si no hay caché, inferir on-demand
    return inferir_on_demand(zona)

def inferir_on_demand(zona: str):
    # Obtener datos del Data Service
    with httpx.Client(timeout=5.0) as client:
        datos = client.get(f"{DATA_SERVICE_URL}/datos/{zona}").json()
        
        # Clasificar en el Risk Service
        resultado = client.post(
            f"{RISK_SERVICE_URL}/clasificar",
            json=datos["variables"]
        ).json()
    
    # Guardar en caché para futuras consultas
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO resultado_riesgo (zona, nivel_riesgo, puntaje, puntaje_maximo)
        VALUES (%s, %s, %s, %s)
    """, (zona, resultado["nivel_riesgo"], resultado["puntaje"], resultado["puntaje_maximo"]))
    conn.commit()
    cur.close()
    conn.close()
    
    return {
        "zona": zona,
        "nivel_riesgo": resultado["nivel_riesgo"],
        "puntaje": resultado["puntaje"],
        "puntaje_maximo": resultado["puntaje_maximo"],
        "fuente": "on-demand"
    }