from fastapi import FastAPI

app = FastAPI(title="Data Service - SIRIF")

# Datos simulados (en producción vendrían de PostGIS + fuentes externas)
DATOS_SIMULADOS = {
    "quilpue": {
        "temperatura": 32.5,
        "humedad": 25.0,
        "viento": 28.0,
        "vegetacion": "alta",
        "incendios_historicos": 12
    },
    "vina_del_mar": {
        "temperatura": 24.0,
        "humedad": 55.0,
        "viento": 12.0,
        "vegetacion": "media",
        "incendios_historicos": 5
    },
    "valparaiso": {
        "temperatura": 18.0,
        "humedad": 70.0,
        "viento": 6.0,
        "vegetacion": "baja",
        "incendios_historicos": 2
    }
}

@app.get("/")
def health():
    return {"status": "ok", "servicio": "data-service"}

@app.get("/datos/{zona}")
def obtener_datos(zona: str):
    zona_key = zona.lower().replace(" ", "_")
    if zona_key not in DATOS_SIMULADOS:
        return {"error": f"Zona '{zona}' no encontrada", "zonas_disponibles": list(DATOS_SIMULADOS.keys())}
    return {"zona": zona, "variables": DATOS_SIMULADOS[zona_key]}