from fastapi import FastAPI
from pydantic import BaseModel
import joblib
import pandas as pd
from prometheus_fastapi_instrumentator import Instrumentator
import os

app = FastAPI(title="Risk Service - SIRIF IA Real")

MODEL_PATH = "modelo_riesgo.pkl"
model = None

class VariablesZona(BaseModel):
    temperatura: float
    humedad: float
    viento: float
    vegetacion: str
    incendios_historicos: int

@app.on_event("startup")
def load_model():
    global model
    if os.path.exists(MODEL_PATH):
        model = joblib.load(MODEL_PATH)
        print("[IA] Modelo Random Forest cargado exitosamente.", flush=True)
    else:
        print("[IA] ALERTA: No se encontró modelo_riesgo.pkl", flush=True)

@app.get("/")
def health():
    return {"status": "ok", "servicio": "risk-service", "modelo_cargado": model is not None}

@app.post("/clasificar")
def clasificar_riesgo(variables: VariablesZona):
    if model is None:
        return {"error": "El modelo no está cargado"}

    # Mapear los datos del data-service a las variables que espera el modelo
    features = {
        'mes': 1, # Simulamos mes de verano
        'severidad_ant': variables.incendios_historicos * 10,
        'delta_incendios': 0,
        'nro_inc_ant': variables.incendios_historicos,
        'sup_afec_ant': variables.incendios_historicos * 50,
        'TEMP_MED': variables.temperatura,
        'HUMEDAD_REL': variables.humedad,
        'VEL_VIENTO': variables.viento,
        'PRECIPITACION': 0.0
    }

    df_features = pd.DataFrame([features])
    prediccion = model.predict(df_features)[0]
    
    # 0 = BAJO, 1 = MEDIO, 2 = ALTO
    mapeo_riesgo = {0: "BAJO", 1: "MEDIO", 2: "ALTO"}
    nivel_riesgo = mapeo_riesgo.get(prediccion, "BAJO")
    puntaje = 15 if nivel_riesgo == "ALTO" else (7 if nivel_riesgo == "MEDIO" else 3)

    return {
        "nivel_riesgo": nivel_riesgo,
        "puntaje": puntaje,
        "puntaje_maximo": 15,
        "factores_considerados": features
    }

Instrumentator().instrument(app).expose(app)