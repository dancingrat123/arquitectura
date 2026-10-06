from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Risk Service - SIRIF")

class VariablesZona(BaseModel):
    temperatura: float
    humedad: float
    viento: float
    vegetacion: str
    incendios_historicos: int

@app.get("/")
def health():
    return {"status": "ok", "servicio": "risk-service"}

@app.post("/clasificar")
def clasificar_riesgo(variables: VariablesZona):
    # Reglas simples (en producción sería el modelo Random Forest)
    puntaje = 0
    
    if variables.temperatura > 30: puntaje += 3
    elif variables.temperatura > 25: puntaje += 2
    else: puntaje += 1
    
    if variables.humedad < 30: puntaje += 3
    elif variables.humedad < 50: puntaje += 2
    else: puntaje += 1
    
    if variables.viento > 20: puntaje += 3
    elif variables.viento > 10: puntaje += 2
    else: puntaje += 1
    
    if variables.vegetacion == "alta": puntaje += 3
    elif variables.vegetacion == "media": puntaje += 2
    else: puntaje += 1
    
    if variables.incendios_historicos > 10: puntaje += 3
    elif variables.incendios_historicos > 5: puntaje += 2
    else: puntaje += 1
    
    # Clasificación final
    if puntaje >= 12:
        nivel = "ALTO"
    elif puntaje >= 8:
        nivel = "MEDIO"
    else:
        nivel = "BAJO"
    
    return {
        "nivel_riesgo": nivel,
        "puntaje": puntaje,
        "puntaje_maximo": 15,
        "factores_considerados": {
            "temperatura": variables.temperatura,
            "humedad": variables.humedad,
            "viento": variables.viento,
            "vegetacion": variables.vegetacion,
            "incendios_historicos": variables.incendios_historicos
        }
    }