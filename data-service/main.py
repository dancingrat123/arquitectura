from fastapi import FastAPI
import json
from prometheus_fastapi_instrumentator import Instrumentator
app = FastAPI(title="Data Service - SIRIF")

# Cargar mapping de riesgo por distrito (simulado)
RIESGO_DISTRITOS = {}
try:
    with open('/app/distritos_riesgo.json', 'r', encoding='utf-8') as f:
        RIESGO_DISTRITOS = json.load(f)
    print(f"[Data] Cargados {len(RIESGO_DISTRITOS)} distritos con riesgo simulado", flush=True)
except Exception as e:
    print(f"[Data] Error cargando distritos_riesgo.json: {e}", flush=True)

# Datos simulados por zona (comuna) para la inferencia
DATOS_SIMULADOS = {
    "quilpue": {
        "temperatura": 32.5, "humedad": 25.0, "viento": 28.0,
        "vegetacion": "alta", "incendios_historicos": 12
    },
    "vina_del_mar": {
        "temperatura": 24.0, "humedad": 55.0, "viento": 12.0,
        "vegetacion": "media", "incendios_historicos": 5
    },
    "valparaiso": {
        "temperatura": 18.0, "humedad": 70.0, "viento": 6.0,
        "vegetacion": "baja", "incendios_historicos": 2
    }
}

@app.get("/")
def health():
    return {"status": "ok", "servicio": "data-service"}

# IMPORTANTE: esta ruta debe ir ANTES de /datos/{zona}
@app.get("/datos/distritos")
def obtener_riesgo_distritos():
    """Devuelve el mapping codigo de distrito -> nivel de riesgo simulado."""
    return {"total": len(RIESGO_DISTRITOS), "distritos": RIESGO_DISTRITOS}

@app.get("/datos/{zona}")
def obtener_datos(zona: str):
    zona_key = zona.lower().replace(" ", "_")
    if zona_key not in DATOS_SIMULADOS:
        return {"error": f"Zona '{zona}' no encontrada", "zonas_disponibles": list(DATOS_SIMULADOS.keys())}
    return {"zona": zona, "variables": DATOS_SIMULADOS[zona_key]}

Instrumentator().instrument(app).expose(app)