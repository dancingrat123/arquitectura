"""Parsers puros de los datasets reales de SIRIF.

Este módulo no importa psycopg2 ni FastAPI, de modo que puede probarse sin
infraestructura. Todos los archivos se leen como UTF-8 (con o sin BOM).
"""

from __future__ import annotations

import csv
import json
import math
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable

REGION_OBJETIVO = "Valparaíso"

VARIABLES_REQUERIDAS = ("TEMP_MED", "HUMEDAD_REL", "VEL_VIENTO")
VARIABLES_CLIMA = VARIABLES_REQUERIDAS + ("PRECIPITACION",)

# Debe coincidir con `ml-pipeline/train_honesto.py`.
FEATURES_CLIMA = (
    "TEMP_MED",
    "TEMP_MAX",
    "HUMEDAD_REL",
    "VEL_VIENTO",
    "PRECIPITACION",
    "precip_7d",
    "temp_media_7d",
    "humedad_7d",
    "precip_30d",
)


def normalizar_texto(valor: Any) -> str:
    """Minúsculas y sin acentos, para comparar nombres de región/comuna."""
    if valor is None:
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto.strip().lower()


def slugify(valor: Any) -> str:
    texto = normalizar_texto(valor)
    limpio = []
    for caracter in texto:
        if caracter.isalnum():
            limpio.append(caracter)
        elif caracter in (" ", "-", "_", "."):
            limpio.append("_")
    slug = "".join(limpio)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


def _a_float(valor: Any, defecto: float = 0.0) -> float:
    if valor is None or valor == "":
        return defecto
    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return defecto


def _a_int(valor: Any, defecto: int = 0) -> int:
    return int(round(_a_float(valor, defecto)))


def _leer_csv(ruta: str) -> list[dict[str, str]]:
    with open(ruta, "r", encoding="utf-8-sig", newline="") as archivo:
        return list(csv.DictReader(archivo))


def _parse_fecha(valor: Any) -> datetime | None:
    """Admite ISO y el formato de EMAS ('2026-09-28 09:00:00.000 -0300').

    Si la fuente trae zona horaria se normaliza a UTC (naive) para que todas
    las fechas de la base queden en la misma escala.
    """
    if not valor:
        return None
    texto = str(valor).strip()
    for formato in ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z"):
        try:
            return datetime.strptime(texto, formato).astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            pass
    try:
        fecha = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        if fecha.tzinfo is not None:
            fecha = fecha.astimezone(timezone.utc).replace(tzinfo=None)
        return fecha
    except ValueError:
        try:
            return datetime.strptime(texto[:10], "%Y-%m-%d")
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# CONAF
# ---------------------------------------------------------------------------


def leer_conaf(ruta: str) -> list[dict[str, Any]]:
    """Devuelve la serie histórica de CONAF lista para persistir.

    Cada fila del CSV es un periodo mensual por región. Se conservan las
    columnas que alimentan las features del modelo.
    """
    registros: list[dict[str, Any]] = []
    for fila in _leer_csv(ruta):
        region = (fila.get("region") or "").strip()
        periodo = _parse_fecha(fila.get("fecha") or fila.get("fecha_creacion"))
        if not region or periodo is None:
            continue
        registros.append(
            {
                "region": region,
                "periodo": periodo.date(),
                "nro_incendios": _a_int(fila.get("nro_inc_act")),
                "nro_incendios_ant": _a_int(fila.get("nro_inc_ant")),
                "nro_incendios_prom_quint": _a_int(fila.get("nro_inc_prom_quint")),
                "superficie_afectada": _a_float(fila.get("sup_afec_act")),
                "superficie_afectada_ant": _a_float(fila.get("sup_afec_ant")),
            }
        )
    return registros


def filtrar_region(registros: Iterable[dict[str, Any]], region: str = REGION_OBJETIVO):
    objetivo = normalizar_texto(region)
    return [r for r in registros if normalizar_texto(r.get("region")) == objetivo]


# ---------------------------------------------------------------------------
# EMAS / clima
# ---------------------------------------------------------------------------


def leer_emas(ruta: str) -> list[dict[str, Any]]:
    estaciones: list[dict[str, Any]] = []
    for fila in _leer_csv(ruta):
        estacion_id = fila.get("ema_ia_id")
        lat = _a_float(fila.get("ema_f_latitudwgs84"), math.nan)
        lon = _a_float(fila.get("ema_f_longitudwgs84"), math.nan)
        if not estacion_id or math.isnan(lat) or math.isnan(lon):
            continue
        estaciones.append(
            {
                "ema_ia_id": str(estacion_id).strip(),
                "nombre": (fila.get("ema_c_nombre") or "").strip(),
                "region": (fila.get("region_nombre") or "").strip(),
                "latitud": lat,
                "longitud": lon,
                "activa": normalizar_texto(fila.get("ema_l_estacionactiva")) in ("true", "1", "t"),
            }
        )
    return estaciones


def leer_clima(ruta: str) -> tuple[dict[str, dict[str, float]], datetime | None]:
    """Agrupa el snapshot de clima por estación y variable.

    Devuelve (lecturas, fecha_snapshot). Si hay varias lecturas por variable se
    promedia, igual que hace el pipeline de entrenamiento.
    """
    acumulado: dict[str, dict[str, list[float]]] = {}
    fechas: list[datetime] = []
    for fila in _leer_csv(ruta):
        estacion_id = (fila.get("ema_ia_id") or "").strip()
        variable = (fila.get("rtd_c_variableid") or "").strip().upper()
        if not estacion_id or variable not in VARIABLES_CLIMA:
            continue
        valor = _a_float(fila.get("rtd_f_valor"), math.nan)
        if math.isnan(valor):
            continue
        acumulado.setdefault(estacion_id, {}).setdefault(variable, []).append(valor)
        fecha = _parse_fecha(fila.get("rtd_dt_marcatiempo"))
        if fecha is not None:
            fechas.append(fecha)

    lecturas: dict[str, dict[str, float]] = {}
    for estacion_id, variables in acumulado.items():
        lecturas[estacion_id] = {
            variable: sum(valores) / len(valores)
            for variable, valores in variables.items()
            if valores
        }
    fecha_snapshot = max(fechas) if fechas else None
    return lecturas, fecha_snapshot


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radio = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radio * math.asin(math.sqrt(a))


def estacion_mas_cercana(
    latitud: float,
    longitud: float,
    estaciones: Iterable[dict[str, Any]],
    lecturas: dict[str, dict[str, float]],
    region: str = REGION_OBJETIVO,
) -> dict[str, Any] | None:
    """Elige la estación EMAS más cercana con TEMP/HUMEDAD/VIENTO.

    Prefiere estaciones con las cuatro variables (incluida precipitación);
    si no hay, acepta tres y asume precipitación 0.
    """
    objetivo = normalizar_texto(region)
    candidatas: list[tuple[int, float, dict[str, Any]]] = []
    for estacion in estaciones:
        if normalizar_texto(estacion.get("region")) != objetivo:
            continue
        valores = lecturas.get(estacion["ema_ia_id"])
        if not valores:
            continue
        if any(variable not in valores for variable in VARIABLES_REQUERIDAS):
            continue
        faltantes = [v for v in VARIABLES_CLIMA if v not in valores]
        distancia = distancia_km(latitud, longitud, estacion["latitud"], estacion["longitud"])
        candidatas.append((len(faltantes), distancia, estacion))

    if not candidatas:
        return None

    _, distancia, estacion = min(candidatas, key=lambda c: (c[0], c[1]))
    valores = lecturas[estacion["ema_ia_id"]]
    return {
        "estacion": estacion,
        "distancia_km": distancia,
        "temperatura": valores["TEMP_MED"],
        "humedad": valores["HUMEDAD_REL"],
        "viento": valores["VEL_VIENTO"],
        "precipitacion": valores.get("PRECIPITACION", 0.0),
        "variables_faltantes": [v for v in VARIABLES_CLIMA if v not in valores],
    }


# ---------------------------------------------------------------------------
# IDE Chile / INE
# ---------------------------------------------------------------------------


def leer_geojson(ruta: str) -> list[dict[str, Any]]:
    with open(ruta, "r", encoding="utf-8-sig") as archivo:
        contenido = json.load(archivo)
    features = contenido.get("features") or []
    return [f for f in features if f.get("geometry") and f.get("properties")]


def distrito_desde_feature(feature: dict[str, Any]) -> dict[str, Any] | None:
    props = feature.get("properties") or {}
    codigo = props.get("CODIGO_C17") or props.get("COD_DISTRI")
    if not codigo:
        return None
    return {
        "codigo_distrito": str(codigo).strip(),
        "codigo_comuna": str(props.get("COMUNA") or "").strip(),
        "nombre_comuna": str(props.get("NOM_COMUNA") or "").strip(),
        "tipo": str(props.get("TIPO_DISTR") or "").strip(),
        "poblacion": _a_int(props.get("T_POB")),
        "superficie": _a_float(props.get("SUPERFICIE")),
        "geom_geojson": json.dumps(feature["geometry"]),
    }


# ---------------------------------------------------------------------------
# Open-Meteo
# ---------------------------------------------------------------------------


VARIABLES_DIARIAS_OPEN_METEO = {
    "temperature_2m_mean": "TEMP_MED",
    "temperature_2m_max": "TEMP_MAX",
    "relative_humidity_2m_mean": "HUMEDAD_REL",
    "wind_speed_10m_max": "VEL_VIENTO",
    "precipitation_sum": "PRECIPITACION",
}


def parse_openmeteo_diario(payload: Any) -> list[dict[str, Any]]:
    """Normaliza el bloque `daily` de Open-Meteo (objeto o lista).

    Cada día con algún valor nulo se descarta completo: es preferible una serie
    más corta que una fila inventada. `valido` indica si quedó al menos un día.
    """
    items = payload if isinstance(payload, list) else [payload]
    resultados: list[dict[str, Any]] = []
    for item in items:
        item = item or {}
        daily = item.get("daily") or {}
        tiempos = daily.get("time") or []
        serie: list[dict[str, Any]] = []
        for indice, marca in enumerate(tiempos):
            valores: dict[str, float] = {}
            completo = True
            for clave, nombre in VARIABLES_DIARIAS_OPEN_METEO.items():
                columna = daily.get(clave) or []
                valor = columna[indice] if indice < len(columna) else None
                if valor is None:
                    completo = False
                    break
                valores[nombre] = float(valor)
            fecha = _parse_fecha(marca) if completo else None
            if not completo or fecha is None:
                continue
            serie.append({"fecha": fecha, **valores})
        resultados.append(
            {
                "latitud": item.get("latitude"),
                "longitud": item.get("longitude"),
                "serie": serie,
                "valido": bool(serie),
            }
        )
    return resultados


def _clave_fecha(registro: dict[str, Any]) -> datetime:
    fecha = registro.get("fecha")
    if isinstance(fecha, datetime):
        return fecha
    return _parse_fecha(fecha) or datetime.min


def construir_features_clima(serie: list[dict[str, Any]]) -> dict[str, float]:
    """Calcula las 9 features del último día de la serie diaria.

    Usa el mismo criterio que `ml-pipeline/train_honesto.py`: rodadura de 7 y 30
    días con la ventana completa disponible y sin devolver nunca `NaN`.
    """
    filas = sorted(serie, key=_clave_fecha) if serie else []
    if not filas:
        return {clave: 0.0 for clave in FEATURES_CLIMA}

    def ultimos(ventana: int) -> list[dict[str, Any]]:
        return filas[-min(ventana, len(filas)):]

    def suma(campo: str, ventana: int) -> float:
        return sum(_a_float(fila.get(campo)) for fila in ultimos(ventana))

    def media(campo: str, ventana: int) -> float:
        valores = [_a_float(fila.get(campo)) for fila in ultimos(ventana)]
        return sum(valores) / len(valores)

    ultimo = filas[-1]
    return {
        "TEMP_MED": _a_float(ultimo.get("TEMP_MED")),
        "TEMP_MAX": _a_float(ultimo.get("TEMP_MAX")),
        "HUMEDAD_REL": _a_float(ultimo.get("HUMEDAD_REL")),
        "VEL_VIENTO": _a_float(ultimo.get("VEL_VIENTO")),
        "PRECIPITACION": _a_float(ultimo.get("PRECIPITACION")),
        "precip_7d": suma("PRECIPITACION", 7),
        "temp_media_7d": media("TEMP_MED", 7),
        "humedad_7d": media("HUMEDAD_REL", 7),
        "precip_30d": suma("PRECIPITACION", 30),
    }


