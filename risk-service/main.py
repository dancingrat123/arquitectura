from __future__ import annotations

import hashlib
import hmac
import json
import os
import unicodedata
from datetime import datetime
from typing import Any

import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator

from shared.riesgo import (
    ALIAS_REGIONES,
    DIRECCIONES_MONOTONAS,
    ETIQUETAS_FEATURES,
    FEATURES_CLIMA,
    UMBRAL_ALTO,
    UMBRAL_MEDIO,
    UNIDADES_FEATURES,
    componer_explicacion,
    direcciones_desde_bundle,
    normalizar_region as _normalizar_region_compartida,
)

app = FastAPI(title="Risk Service - SIRIF IA Real")

MODEL_PATH = os.getenv("MODEL_PATH", "modelo_riesgo.pkl")
MODEL_VERSION = os.getenv("MODEL_VERSION", "rf-clima-region-v2")
MODELO_COMUNAL_PATH = os.getenv("MODELO_COMUNAL_PATH", "modelo_riesgo_comunal.pkl")
MODEL_VERSION_COMUNAL = os.getenv("MODEL_VERSION_COMUNAL", "rf-comunal-clima-v1")
# Región a la que pertenecen las comunas del modelo comunal (Valparaíso).
REGION_MODELO_COMUNAL = "valparaiso"
# Umbrales OPERATIVOS del modelo comunal: probabilidad ABSOLUTA diaria de ≥1
# incendio (no percentil). ALTO ≈ 5× la tasa base (~3 %); MEDIO ≈ 1.6×.
# Se separan de los percentiles entrenados del artefacto (0.07/0.031), que
# quedan como referencia en `model_metadata_comunal.json`.
UMBRAL_ALTO_COMUNAL = float(os.getenv("UMBRAL_ALTO_COMUNAL", "0.15"))
UMBRAL_MEDIO_COMUNAL = float(os.getenv("UMBRAL_MEDIO_COMUNAL", "0.05"))
MODEL_METADATA_PATH = os.getenv("MODEL_METADATA_PATH", "")
MODEL_METADATA_COMUNAL_PATH = os.getenv("MODEL_METADATA_COMUNAL_PATH", "")
CAMPOS_CHECKSUM = ("sha256_modelo", "sha256", "checksum_sha256", "checksum")

bundle: dict[str, Any] | None = None
model: Any = None
modelo_legacy: bool = False
checksum_verificado: bool | None = None
checksum_detalle: str = "sin verificar"

bundle_comunal: dict[str, Any] | None = None
modelo_comunal: Any = None
comunal_detalle: str = "sin cargar"


class VariablesZona(BaseModel):
    temperatura: float = 0.0
    humedad: float = 0.0
    viento: float = 0.0
    vegetacion: str | None = "media"
    incendios_historicos: int | None = 0
    precipitacion: float | None = 0.0
    features: dict[str, Any] | None = None
    fecha_medicion: str | None = None
    region: str | None = None
    comuna: str | None = None


def normalizar_region(valor: Any) -> str:
    return _normalizar_region_compartida(valor)


def normalizar_comuna(valor: Any) -> str:
    if valor is None:
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.lower().split())


def nivel_desde_probabilidad(
    p: float,
    umbral_alto: float | None = None,
    umbral_medio: float | None = None,
) -> str:
    alto = UMBRAL_ALTO if umbral_alto is None else umbral_alto
    medio = UMBRAL_MEDIO if umbral_medio is None else umbral_medio
    if p >= alto:
        return "ALTO"
    if p >= medio:
        return "MEDIO"
    return "BAJO"


def calcular_puntaje(p: float, umbral_alto: float | None = None) -> int:
    if umbral_alto is not None and umbral_alto > 0:
        # Normaliza el puntaje al umbral ALTO del propio modelo: en el umbral
        # ALTO vale 15. Se aplica solo al comunal; el regional conserva el
        # contrato `round(p*15)` del modelo honesto. Por eso las dos escalas NO
        # son comparables entre sí (ver `tipo_estimacion`).
        return int(round(min(1.0, float(p) / umbral_alto) * 15))
    return int(round(p * 15))


def umbrales_operativos(version_modelo: str | None) -> dict[str, float] | None:
    """Umbrales operativos por modelo (probabilidad absoluta).

    El comunal usa un criterio absoluto (ALTO ≥ 15 %, MEDIO ≥ 5 %) en vez del
    percentil entrenado (0.07/0.031), para que el nivel sea interpretable y no
    colapse a "todo ALTO" en un día seco. El regional conserva sus umbrales.
    """
    if str(version_modelo or "").startswith("rf-comunal"):
        return {"alto": UMBRAL_ALTO_COMUNAL, "medio": UMBRAL_MEDIO_COMUNAL}
    return None


def calcular_factores_predominantes(
    features: dict[str, Any],
    rango_features: dict[str, Any],
    direcciones: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Explica el riesgo con los 5 features que más se alejan del centro.

    Regla determinista y local del roadmap: `t = (valor - min) / (max - min)`
    recortado a [0, 1]; la contribución al riesgo es `t` para features con
    monotonía +1 y `1 - t` para -1; las features sin monotonía se excluyen.
    """
    if not isinstance(rango_features, dict) or not rango_features:
        return []
    direcciones = direcciones or DIRECCIONES_MONOTONAS
    factores: list[dict[str, Any]] = []
    for feature in FEATURES_CLIMA:
        limites = rango_features.get(feature)
        direccion_monotona = direcciones.get(feature, "0")
        if direccion_monotona == "0" or not isinstance(limites, (list, tuple)) or len(limites) != 2:
            continue
        minimo, maximo = float(limites[0]), float(limites[1])
        valor = float((features or {}).get(feature, 0.0))
        if maximo == minimo:
            t = 0.5
        else:
            t = (valor - minimo) / (maximo - minimo)
        t = max(0.0, min(1.0, t))
        riesgo_dir = t if direccion_monotona == "+1" else 1.0 - t
        # `direccion` refleja el efecto sobre el riesgo en el valor actual (no el
        # signo monótono): riesgo_dir >= 0.5 → el factor sube el riesgo; si no,
        # lo mitiga. `peso` es la magnitud (0.5..1.0).
        direccion = "aumenta" if riesgo_dir >= 0.5 else "reduce"
        factores.append(
            {
                "variable": feature,
                "etiqueta": ETIQUETAS_FEATURES.get(feature, feature),
                "valor": round(valor, 2),
                "unidad": UNIDADES_FEATURES.get(feature, ""),
                "direccion": direccion,
                "peso": round(max(riesgo_dir, 1.0 - riesgo_dir), 2),
                "monotonia": direccion_monotona,
                "_distancia": abs(riesgo_dir - 0.5),
            }
        )
    factores.sort(key=lambda factor: factor["_distancia"], reverse=True)
    seleccionados = factores[:5]
    for factor in seleccionados:
        factor.pop("_distancia", None)
    return seleccionados


def construir_fila(features: dict[str, Any], region: str | None, regiones: list[str]) -> tuple[pd.DataFrame, bool]:
    region_norm = normalizar_region(region) if region else ""
    conocida = region_norm in (regiones or [])
    columnas = list(FEATURES_CLIMA) + [f"reg_{r}" for r in (regiones or [])]
    fila: dict[str, float] = {f: float((features or {}).get(f, 0.0)) for f in FEATURES_CLIMA}
    for r in (regiones or []):
        fila[f"reg_{r}"] = 1.0 if region_norm == r else 0.0
    df = pd.DataFrame([fila], columns=columnas)
    return df, conocida


def construir_fila_comunal(
    features: dict[str, Any],
    comuna: str | None,
    fecha: datetime,
    bundle_comunal_activo: dict[str, Any],
) -> tuple[pd.DataFrame, bool]:
    comunas = bundle_comunal_activo.get("comunas") or []
    comuna_norm = normalizar_comuna(comuna) if comuna else ""
    conocida = comuna_norm in comunas
    columnas = bundle_comunal_activo.get("features") or []
    mes = fecha.month
    fila: dict[str, float] = {f: float((features or {}).get(f, 0.0)) for f in FEATURES_CLIMA}
    fila["mes"] = float(mes)
    fila["mes_sin"] = float(np.sin(2 * np.pi * mes / 12))
    fila["mes_cos"] = float(np.cos(2 * np.pi * mes / 12))
    for nombre in comunas:
        fila[f"com_{nombre}"] = 1.0 if comuna_norm == nombre else 0.0
    df = pd.DataFrame([fila], columns=columnas)
    return df, conocida


def _parse_fecha(valor: str | None) -> datetime:
    if valor:
        try:
            return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now()


def _derivar_features(variables: VariablesZona) -> dict[str, float]:
    temperatura = float(variables.temperatura)
    humedad = float(variables.humedad)
    viento = float(variables.viento)
    precipitacion = float(variables.precipitacion or 0.0)
    return {
        "TEMP_MED": temperatura,
        "TEMP_MAX": temperatura,
        "HUMEDAD_REL": humedad,
        "VEL_VIENTO": viento,
        "PRECIPITACION": precipitacion,
        "precip_7d": precipitacion,
        "temp_media_7d": temperatura,
        "humedad_7d": humedad,
        "precip_30d": precipitacion,
    }


def _bundle() -> dict[str, Any]:
    global bundle
    if bundle is None:
        return {
            "features": FEATURES_CLIMA,
            "regiones": [],
            "monotonic_cst": [],
            "umbrales": {"alto": UMBRAL_ALTO, "medio": UMBRAL_MEDIO},
            "meses_entrenamiento": [7, 8, 9],
            "rango_features": {},
            "metadata": {},
        }
    return bundle


def _bundle_comunal() -> dict[str, Any]:
    global bundle_comunal
    if bundle_comunal is None:
        return {
            "features": list(FEATURES_CLIMA) + ["mes", "mes_sin", "mes_cos"],
            "comunas": [],
            "monotonic_cst": [],
            "umbrales": {"alto": UMBRAL_ALTO_COMUNAL, "medio": UMBRAL_MEDIO_COMUNAL},
            "meses_entrenamiento": list(range(1, 13)),
            "rango_features": {},
            "metadata": {},
        }
    return bundle_comunal


def calcular_sha256(ruta: str) -> str:
    """SHA-256 del artefacto, leído por bloques para no cargarlo en memoria."""
    digest = hashlib.sha256()
    with open(ruta, "rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _candidatos_metadata() -> list[str]:
    candidatos: list[str] = []
    if MODEL_METADATA_PATH:
        candidatos.append(MODEL_METADATA_PATH)
    directorio_modelo = os.path.dirname(os.path.abspath(MODEL_PATH))
    directorio_servicio = os.path.dirname(os.path.abspath(__file__))
    candidatos.extend(
        [
            os.path.join(directorio_modelo, "model_metadata.json"),
            os.path.join(directorio_servicio, "model_metadata.json"),
            os.path.join(os.path.dirname(directorio_servicio), "ml-pipeline", "model_metadata.json"),
        ]
    )
    vistos: set[str] = set()
    unicos = []
    for ruta in candidatos:
        if ruta not in vistos:
            vistos.add(ruta)
            unicos.append(ruta)
    return unicos


def _ruta_metadata() -> str | None:
    for ruta in _candidatos_metadata():
        if os.path.exists(ruta):
            return ruta
    return None


def _candidatos_metadata_comunal() -> list[str]:
    candidatos: list[str] = []
    if MODEL_METADATA_COMUNAL_PATH:
        candidatos.append(MODEL_METADATA_COMUNAL_PATH)
    directorio_modelo = os.path.dirname(os.path.abspath(MODELO_COMUNAL_PATH))
    directorio_servicio = os.path.dirname(os.path.abspath(__file__))
    candidatos.extend(
        [
            os.path.join(directorio_modelo, "model_metadata_comunal.json"),
            os.path.join(directorio_servicio, "model_metadata_comunal.json"),
            os.path.join(os.path.dirname(directorio_servicio), "ml-pipeline", "model_metadata_comunal.json"),
        ]
    )
    vistos: set[str] = set()
    unicos = []
    for ruta in candidatos:
        if ruta not in vistos:
            vistos.add(ruta)
            unicos.append(ruta)
    return unicos


def _ruta_metadata_comunal() -> str | None:
    for ruta in _candidatos_metadata_comunal():
        if os.path.exists(ruta):
            return ruta
    return None


def verificar_checksum_modelo(
    ruta_modelo: str | None = None,
    ruta_metadata: str | None = None,
) -> tuple[bool, str]:
    """Verifica el SHA-256 del pickle contra `model_metadata.json` (S9).

    Devuelve `(ok, detalle)`. Es fail-closed: sin metadata o sin checksum el
    artefacto no se considera verificable.
    """
    ruta_modelo = ruta_modelo or MODEL_PATH
    if not os.path.exists(ruta_modelo):
        return False, f"artefacto no encontrado: {ruta_modelo}"

    ruta_metadata = ruta_metadata or _ruta_metadata()
    if not ruta_metadata or not os.path.exists(ruta_metadata):
        return False, "model_metadata.json no encontrado"

    try:
        with open(ruta_metadata, encoding="utf-8") as archivo:
            metadata = json.load(archivo)
    except (OSError, ValueError) as error:
        return False, f"model_metadata.json ilegible: {error}"

    esperado = ""
    for campo in CAMPOS_CHECKSUM:
        valor = metadata.get(campo) if isinstance(metadata, dict) else None
        if isinstance(valor, str) and valor.strip():
            esperado = valor.strip().lower()
            break
    if not esperado:
        return False, "model_metadata.json no declara checksum SHA-256"

    actual = calcular_sha256(ruta_modelo)
    if not hmac.compare_digest(actual, esperado):
        return False, (
            "checksum SHA-256 no coincide "
            f"(esperado {esperado[:12]}…, calculado {actual[:12]}…)"
        )
    return True, f"sha256 verificado: {actual[:12]}…"


def load_model() -> None:
    global bundle, model, modelo_legacy, checksum_verificado, checksum_detalle
    if os.path.exists(MODEL_PATH):
        checksum_verificado, checksum_detalle = verificar_checksum_modelo()
        if not checksum_verificado:
            bundle = None
            model = None
            modelo_legacy = False
            print(
                f"[IA] ALERTA: modelo no cargado, checksum no verificable: {checksum_detalle}",
                flush=True,
            )
            return
        print(f"[IA] {checksum_detalle}", flush=True)
        obj = joblib.load(MODEL_PATH)
        if isinstance(obj, dict):
            bundle = obj
            model = obj.get("modelo")
            modelo_legacy = False
        else:
            model = obj
            modelo_legacy = True
            bundle = None
        print("[IA] Modelo cargado exitosamente.", flush=True)
    else:
        checksum_verificado = False
        checksum_detalle = f"artefacto no encontrado: {MODEL_PATH}"
        print("[IA] ALERTA: No se encontró modelo_riesgo.pkl", flush=True)


def load_model_comunal() -> None:
    global bundle_comunal, modelo_comunal, comunal_detalle
    if not os.path.exists(MODELO_COMUNAL_PATH):
        comunal_detalle = f"artefacto no encontrado: {MODELO_COMUNAL_PATH}"
        print(f"[IA] {comunal_detalle}; se usará el modelo regional", flush=True)
        return
    # S9: verificación de integridad del artefacto comunal (fail-closed).
    ok, detalle = verificar_checksum_modelo(MODELO_COMUNAL_PATH, _ruta_metadata_comunal())
    if not ok:
        bundle_comunal = None
        modelo_comunal = None
        comunal_detalle = f"checksum no verificable: {detalle}; se usará el modelo regional"
        print(f"[IA] ALERTA: {comunal_detalle}", flush=True)
        return
    print(f"[IA] modelo comunal {detalle}", flush=True)
    try:
        obj = joblib.load(MODELO_COMUNAL_PATH)
        if isinstance(obj, dict):
            bundle_comunal = obj
            modelo_comunal = obj.get("modelo")
            comunal_detalle = f"cargado: {MODELO_COMUNAL_PATH}"
            print(
                f"[IA] Modelo comunal cargado ({len(obj.get('comunas', []))} comunas).",
                flush=True,
            )
        else:
            comunal_detalle = "artefacto comunal no es un bundle"
            print(f"[IA] ALERTA: {comunal_detalle}", flush=True)
    except Exception as error:  # noqa: BLE001
        comunal_detalle = f"error cargando modelo comunal: {error}"
        print(f"[IA] ALERTA: {comunal_detalle}", flush=True)


@app.on_event("startup")
def _startup() -> None:
    load_model()
    load_model_comunal()


@app.get("/")
def health() -> dict[str, Any]:
    b = _bundle()
    umbrales = (b.get("umbrales") if isinstance(b, dict) else None) or {"alto": UMBRAL_ALTO, "medio": UMBRAL_MEDIO}
    meses = (b.get("meses_entrenamiento") if isinstance(b, dict) else None) or [7, 8, 9]
    metadata = (b.get("metadata") if isinstance(b, dict) else None) or {}
    limitaciones = metadata.get("limitaciones") if isinstance(metadata, dict) else None
    if not limitaciones:
        limitaciones = [
            "Modelo regional: no predice por comuna ni por distrito.",
            "Ventana de entrenamiento jul-sep 2026; fuera de ella la salida es extrapolación.",
            "No generaliza a regiones no vistas sin reentrenar.",
        ]
    b_com = _bundle_comunal()
    return {
        "status": "ok",
        "servicio": "risk-service",
        "modelo_cargado": model is not None,
        "version_modelo": MODEL_VERSION,
        "checksum_verificado": checksum_verificado,
        "checksum_detalle": checksum_detalle,
        "features": FEATURES_CLIMA,
        "umbrales": umbrales,
        "meses_entrenamiento": meses,
        "limitaciones": limitaciones,
        "modelo_comunal_cargado": modelo_comunal is not None,
        "version_modelo_comunal": MODEL_VERSION_COMUNAL,
        "comunal_detalle": comunal_detalle,
        "comunas_comunal": b_com.get("comunas", []),
        "umbrales_comunal": b_com.get("umbrales", {}),
        "umbrales_operativos_comunal": {
            "alto": UMBRAL_ALTO_COMUNAL,
            "medio": UMBRAL_MEDIO_COMUNAL,
        },
    }


@app.post("/clasificar")
def clasificar_riesgo(variables: VariablesZona) -> dict[str, Any]:
    if model is None and modelo_comunal is None:
        return {"error": "El modelo no está cargado"}

    if variables.features and isinstance(variables.features, dict):
        features = {clave: float(valor) for clave, valor in variables.features.items()}
        for clave in FEATURES_CLIMA:
            features.setdefault(clave, 0.0)
    else:
        features = _derivar_features(variables)

    fecha = _parse_fecha(variables.fecha_medicion)

    usar_comunal = False
    comuna_conocida = False
    if modelo_comunal is not None and variables.comuna:
        b_com = _bundle_comunal()
        fila_com, comuna_conocida = construir_fila_comunal(
            features, variables.comuna, fecha, b_com
        )
        if comuna_conocida:
            usar_comunal = True
            modelo_activo = modelo_comunal
            b_activo = b_com
            fila = fila_com
            region_conocida = normalizar_region(variables.region) == REGION_MODELO_COMUNAL
        else:
            b = _bundle()
            regiones = (b.get("regiones") if isinstance(b, dict) else None) or []
            fila, region_conocida = construir_fila(features, variables.region, regiones)
            modelo_activo = model
            b_activo = b
    else:
        b = _bundle()
        regiones = (b.get("regiones") if isinstance(b, dict) else None) or []
        fila, region_conocida = construir_fila(features, variables.region, regiones)
        modelo_activo = model
        b_activo = b

    if modelo_activo is None:
        return {"error": "El modelo no está cargado"}

    umbrales = (b_activo.get("umbrales") if isinstance(b_activo, dict) else None) or {
        "alto": UMBRAL_ALTO,
        "medio": UMBRAL_MEDIO,
    }
    operativos = umbrales_operativos(MODEL_VERSION_COMUNAL if usar_comunal else None)
    if operativos:
        umbrales = operativos
    meses = (b_activo.get("meses_entrenamiento") if isinstance(b_activo, dict) else None) or (
        list(range(1, 13)) if usar_comunal else [7, 8, 9]
    )
    rango = (b_activo.get("rango_features") if isinstance(b_activo, dict) else None) or {}

    # Calcular probabilidad
    try:
        prob = float(modelo_activo.predict_proba(fila)[0, 1])
    except Exception:
        # Fallback para modelos legacy sin predict_proba
        prob = float(modelo_activo.predict(fila)[0])

    fuera_ventana = fecha.month not in meses

    fuera_de_rango = False
    for feature, valor in features.items():
        if feature in rango and isinstance(rango[feature], (list, tuple)) and len(rango[feature]) == 2:
            minimo, maximo = rango[feature]
            if valor < minimo or valor > maximo:
                fuera_de_rango = True
                break

    nivel = nivel_desde_probabilidad(
        prob,
        float(umbrales.get("alto", UMBRAL_ALTO)),
        float(umbrales.get("medio", UMBRAL_MEDIO)),
    )
    if usar_comunal:
        puntaje = calcular_puntaje(prob, float(umbrales.get("alto", 0.07)))
    else:
        puntaje = calcular_puntaje(prob)
    factores_predominantes = calcular_factores_predominantes(
        features, rango, direcciones_desde_bundle(b_activo)
    )
    explicacion = componer_explicacion(nivel, factores_predominantes)

    if usar_comunal:
        version_modelo = MODEL_VERSION_COMUNAL
        tipo_estimacion = "riesgo_meteorologico_comunal"
        advertencia = (
            "Estimación de riesgo meteorológico comunal; no es pronóstico de ocurrencia."
        )
    else:
        version_modelo = MODEL_VERSION
        tipo_estimacion = "riesgo_meteorologico_regional"
        advertencia = (
            "Estimación de riesgo meteorológico regional; no es pronóstico de ocurrencia."
        )

    return {
        "nivel_riesgo": nivel,
        "probabilidad": prob,
        "probabilidad_alto": prob,
        "umbral_alto": float(umbrales.get("alto", UMBRAL_ALTO)),
        "umbral_medio": float(umbrales.get("medio", UMBRAL_MEDIO)),
        "puntaje": puntaje,
        "puntaje_maximo": 15,
        "version_modelo": version_modelo,
        "tipo_estimacion": tipo_estimacion,
        "region": variables.region,
        "region_conocida": region_conocida,
        "comuna": variables.comuna,
        "comuna_conocida": comuna_conocida,
        "fuera_ventana_estacional": fuera_ventana,
        "fuera_de_rango": fuera_de_rango,
        "factores_considerados": features,
        "factores_predominantes": factores_predominantes,
        "explicacion": explicacion,
        "advertencia": advertencia,
    }


Instrumentator().instrument(app).expose(app)
