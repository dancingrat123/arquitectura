"""Contrato compartido del riesgo meteorológico de SIRIF.

Fuente única de las 9 features del modelo `rf-clima-region-v2`, sus alias de
región, la dirección monótona de cada feature y los umbrales publicados. Lo
importan `risk-service`, `api-service` y `ml-pipeline` para evitar drift entre
entrenamiento e inferencia.
"""

from __future__ import annotations

import os
import unicodedata
from typing import Any

FEATURES_CLIMA: list[str] = [
    "TEMP_MED",
    "TEMP_MAX",
    "HUMEDAD_REL",
    "VEL_VIENTO",
    "PRECIPITACION",
    "precip_7d",
    "temp_media_7d",
    "humedad_7d",
    "precip_30d",
]

# Orden alineado con FEATURES_CLIMA (spec del modelo honesto).
MONOTONIC_CST_CLIMA: list[int] = [1, 1, -1, 0, -1, -1, 1, -1, -1]

# Representación textual usada en el contrato JSON de explicabilidad.
DIRECCIONES_MONOTONAS: dict[str, str] = {
    feature: {1: "+1", -1: "-1", 0: "0"}[cst]
    for feature, cst in zip(FEATURES_CLIMA, MONOTONIC_CST_CLIMA)
}

ALIAS_REGIONES: dict[str, str] = {
    "la araucania": "araucania",
    "aysen del general carlos ibanez del campo": "aysen",
    "magallanes y de la antartica chilena": "magallanes",
    "metropolitana de santiago": "metropolitana",
    "libertador general bernardo o'higgins": "o'higgins",
}

UMBRAL_ALTO: float = float(os.getenv("UMBRAL_ALTO", "0.20"))
UMBRAL_MEDIO: float = float(os.getenv("UMBRAL_MEDIO", "0.08"))

ETIQUETAS_FEATURES: dict[str, str] = {
    "TEMP_MED": "Temperatura media",
    "TEMP_MAX": "Temperatura máxima",
    "HUMEDAD_REL": "Humedad relativa",
    "VEL_VIENTO": "Velocidad del viento",
    "PRECIPITACION": "Precipitación",
    "precip_7d": "Precipitación acumulada 7 días",
    "temp_media_7d": "Temperatura media 7 días",
    "humedad_7d": "Humedad relativa 7 días",
    "precip_30d": "Precipitación acumulada 30 días",
}

UNIDADES_FEATURES: dict[str, str] = {
    "TEMP_MED": "°C",
    "TEMP_MAX": "°C",
    "HUMEDAD_REL": "%",
    "VEL_VIENTO": "km/h",
    "PRECIPITACION": "mm",
    "precip_7d": "mm",
    "temp_media_7d": "°C",
    "humedad_7d": "%",
    "precip_30d": "mm",
}

ADVERTENCIA_RIESGO = (
    "Estimación de riesgo meteorológico regional; no es pronóstico de ocurrencia."
)
ADVERTENCIA_VENTANA = (
    "Fuera de la ventana estacional de entrenamiento (jul–sep 2026)"
)


def normalizar_region(valor: Any) -> str:
    """Minúsculas, sin acentos y con los alias de EMAS/CONAF aplicados."""
    if valor is None:
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = " ".join(texto.lower().split())
    return ALIAS_REGIONES.get(texto, texto)


def nivel_desde_probabilidad(p: float) -> str:
    if p >= UMBRAL_ALTO:
        return "ALTO"
    if p >= UMBRAL_MEDIO:
        return "MEDIO"
    return "BAJO"


def direcciones_desde_bundle(bundle: dict[str, Any] | None) -> dict[str, str]:
    """Lee `monotonic_cst` del bundle; si falta, usa el contrato del spec."""
    if not isinstance(bundle, dict):
        return dict(DIRECCIONES_MONOTONAS)
    crudo = bundle.get("monotonic_cst")
    if isinstance(crudo, dict):
        direcciones = {
            feature: {1: "+1", -1: "-1", 0: "0"}.get(int(crudo.get(feature, 0)), "0")
            for feature in FEATURES_CLIMA
        }
    elif isinstance(crudo, (list, tuple)) and len(crudo) >= len(FEATURES_CLIMA):
        direcciones = {
            feature: {1: "+1", -1: "-1", 0: "0"}.get(int(crudo[i]), "0")
            for i, feature in enumerate(FEATURES_CLIMA)
        }
    else:
        return dict(DIRECCIONES_MONOTONAS)
    return direcciones


def _frase_factor(factor: dict[str, Any]) -> str:
    etiqueta = factor.get("etiqueta") or factor.get("variable", "variable")
    valor = factor.get("valor")
    unidad = factor.get("unidad") or ""
    direccion = factor.get("direccion", "aumenta")
    sentido = factor.get("monotonia", "+1")
    # El adjetivo describe el VALOR de la variable (no su dirección de efecto):
    # monotonía +1 (temperatura) que aumenta el riesgo → valor elevado; monotonía
    # -1 (humedad/precipitación) que aumenta el riesgo → valor bajo.
    if direccion == "aumenta":
        adjetivo = "elevada" if sentido == "+1" else "baja"
    else:
        adjetivo = "baja" if sentido == "+1" else "elevada"
    if valor is None:
        return f"{etiqueta} {adjetivo}"
    return f"{etiqueta} {adjetivo} ({valor} {unidad})".replace(" )", ")")


def componer_explicacion(nivel: str, factores: list[dict[str, Any]]) -> str:
    """Plantilla fija en español a partir de los factores predominantes."""
    if not factores:
        return "Explicación no disponible para esta versión del modelo."
    principales = [f for f in factores if f.get("direccion") == "aumenta"][:2]
    if not principales:
        principales = factores[:2]
    detalle = " y ".join(_frase_factor(f) for f in principales)
    return f"El nivel {nivel} se explica principalmente por {detalle}."
