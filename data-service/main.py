"""Data Service de SIRIF: lee la última ingesta real desde PostgreSQL/PostGIS.

La carga inicial (IDE/INE, CONAF, EMAS) corre en un hilo de arranque y la
sincronización meteorológica se expone como endpoint interno para que
`monitoring-service` la programe cada hora.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator

import db
import ingestion
import parsers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("data-service")

app = FastAPI(title="Data Service - SIRIF")

RUTA_DISTRITOS_FALLBACK = os.getenv("DISTRITOS_RIESGO", "/app/distritos_riesgo.json")

VEGETACION_POR_ZONA = {
    "quilpue": "alta",
    "vina_del_mar": "media",
    "valparaiso": "baja",
}

# Último recurso si PostgreSQL aún no está disponible. Se marca explícitamente
# como degradado: no pretende ser un dato real.
FALLBACK_LOCAL: dict[str, dict[str, Any]] = {
    "quilpue": {
        "temperatura": 32.5,
        "humedad": 25.0,
        "viento": 28.0,
        "precipitacion": 0.0,
        "vegetacion": "alta",
        "incendios_historicos": 12,
    },
    "vina_del_mar": {
        "temperatura": 24.0,
        "humedad": 55.0,
        "viento": 12.0,
        "precipitacion": 0.0,
        "vegetacion": "media",
        "incendios_historicos": 5,
    },
    "valparaiso": {
        "temperatura": 18.0,
        "humedad": 70.0,
        "viento": 6.0,
        "precipitacion": 0.0,
        "vegetacion": "baja",
        "incendios_historicos": 2,
    },
}

ESTADO: dict[str, Any] = {"db": False, "bootstrap": None, "ultima_sincronizacion": None}


class CapaVegetacion(BaseModel):
    zona: str
    indice: str = "NDVI"
    valor: float | None = None
    categoria: str | None = None
    fecha_captura: str | None = None
    fuente: str | None = None


class IngestaVegetacionRequest(BaseModel):
    capas: list[CapaVegetacion]


def verificar_token_interno(x_internal_token: str | None = Header(default=None)) -> None:
    """Todo endpoint de datos exige `X-Internal-Token` (S3)."""
    esperado = os.getenv("INTERNAL_TOKEN")
    if not esperado or x_internal_token != esperado:
        raise HTTPException(status_code=401, detail="Token interno inválido o ausente")

RIESGO_DISTRITOS: dict[str, str] = {}
try:
    with open(RUTA_DISTRITOS_FALLBACK, "r", encoding="utf-8") as archivo:
        RIESGO_DISTRITOS = json.load(archivo)
except Exception as error:  # noqa: BLE001
    logger.warning("No se pudo cargar %s: %s", RUTA_DISTRITOS_FALLBACK, error)


# ---------------------------------------------------------------------------
# Arranque: carga inicial idempotente
# ---------------------------------------------------------------------------


def _arranque() -> None:
    if not db.database_url():
        logger.warning("DATABASE_URL no definida: se usará el fallback local")
        return
    if not db.esperar_db():
        logger.error("PostgreSQL no respondió: se usará el fallback local")
        return
    ESTADO["db"] = True
    try:
        ESTADO["bootstrap"] = ingestion.bootstrap()
        logger.info("Carga inicial completada: %s", ESTADO["bootstrap"])
    except Exception as error:  # noqa: BLE001
        logger.exception("La carga inicial falló: %s", error)


@app.on_event("startup")
def startup_event() -> None:
    # Fail-fast: sin estos secretos el servicio no debe operar.
    if not os.getenv("INTERNAL_TOKEN"):
        raise RuntimeError("Falta INTERNAL_TOKEN: data-service exige X-Internal-Token")
    if not os.getenv("ADMIN_PASSWORD_HASH"):
        raise RuntimeError("Falta ADMIN_PASSWORD_HASH: no se puede sembrar el usuario admin")
    if os.getenv("BOOTSTRAP_ON_STARTUP", "true").lower() in ("1", "true", "yes"):
        threading.Thread(target=_arranque, daemon=True).start()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(valor: Any) -> str | None:
    if isinstance(valor, datetime):
        return valor.isoformat() + "Z"
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    return None


def _features_desde_medicion(medicion: dict[str, Any]) -> dict[str, float]:
    features = medicion.get("features")
    if isinstance(features, str):
        try:
            features = json.loads(features)
        except (TypeError, ValueError):
            features = None
    if not isinstance(features, dict):
        serie = [
            {
                "fecha": medicion.get("fecha_medicion") or datetime.now(),
                "TEMP_MED": medicion.get("temperatura"),
                "TEMP_MAX": medicion.get("temperatura"),
                "HUMEDAD_REL": medicion.get("humedad"),
                "VEL_VIENTO": medicion.get("velocidad_viento"),
                "PRECIPITACION": medicion.get("precipitacion") or 0.0,
            }
        ]
        features = parsers.construir_features_clima(serie)
    return features


def _respuesta_fallback(zona: str, detalle: str) -> dict[str, Any]:
    clave = parsers.slugify(zona)
    datos = FALLBACK_LOCAL.get(clave)
    if not datos:
        return {
            "error": f"Zona '{zona}' no encontrada en la ingesta real ni en el fallback",
            "zonas_disponibles": sorted(FALLBACK_LOCAL),
        }
    return {
        "zona": clave,
        "id_zona": None,
        "comuna": None,
        "variables": datos,
        "features": parsers.construir_features_clima(
            [
                {
                    "fecha": datetime.now(),
                    "TEMP_MED": datos["temperatura"],
                    "TEMP_MAX": datos["temperatura"],
                    "HUMEDAD_REL": datos["humedad"],
                    "VEL_VIENTO": datos["viento"],
                    "PRECIPITACION": datos["precipitacion"],
                }
            ]
        ),
        "meta": {
            "fuente": "fallback-local",
            "calidad": "degradado",
            "detalle": detalle,
            "fecha_medicion": None,
            "fecha_ingesta": None,
            "antiguedad_minutos": None,
            "historico_fuente": "no disponible",
            "historico_periodo": None,
            "trazabilidad_id": None,
        },
    }


def _leer_zona(zona: str) -> dict[str, Any]:
    conn = db.get_conn()
    try:
        fila_zona = db.zona_por_clave(conn, parsers.slugify(zona)) or db.zona_por_clave(conn, zona)
        if not fila_zona:
            return {
                "error": f"Zona '{zona}' no encontrada",
                "zonas_disponibles": db.zonas_conocidas(conn),
            }
        medicion = db.ultima_medicion(conn, fila_zona["id_zona"])
        if not medicion:
            return _respuesta_fallback(fila_zona["slug"], "Sin mediciones almacenadas")
        conaf = db.ultimo_conaf(conn)
        trazabilidad = db.ultima_trazabilidad(conn, fila_zona["id_zona"])

        features_raw = medicion.get("features")
        calidad = medicion["calidad"] or "ok"
        if features_raw is None:
            calidad = "degradado"
        features = _features_desde_medicion(medicion)

        variables = {
            "temperatura": medicion["temperatura"],
            "humedad": medicion["humedad"],
            "viento": medicion["velocidad_viento"],
            "precipitacion": medicion["precipitacion"] or 0.0,
            "vegetacion": medicion["cobertura_vegetal"]
            or VEGETACION_POR_ZONA.get(fila_zona["slug"], "media"),
            "incendios_historicos": int(conaf["nro_incendios"]) if conaf else 0,
        }
        return {
            "zona": fila_zona["slug"],
            "id_zona": fila_zona["id_zona"],
            "comuna": fila_zona["comuna"],
            "region": fila_zona["region"],
            "variables": variables,
            "features": features,
            "meta": {
                "fuente": medicion["fuente"] or "desconocida",
                "calidad": calidad,
                "fecha_medicion": _iso(medicion["fecha_medicion"]),
                "fecha_ingesta": _iso(medicion["fecha_ingesta"]),
                "antiguedad_minutos": round(float(medicion["antiguedad_minutos"] or 0), 1),
                "historico_fuente": "CONAF" if conaf else "no disponible",
                "historico_periodo": _iso(conaf["periodo"]) if conaf else None,
                "trazabilidad_id": trazabilidad,
            },
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


@app.get("/")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "servicio": "data-service",
        "db_conectada": ESTADO["db"],
        "bootstrap": ESTADO["bootstrap"],
        "ultima_sincronizacion": ESTADO["ultima_sincronizacion"],
    }


@app.get("/datos/distritos", dependencies=[Depends(verificar_token_interno)])
def obtener_riesgo_distritos() -> dict[str, Any]:
    """Riesgo vigente por distrito, derivado del riesgo comunal real."""
    try:
        conn = db.get_conn()
        try:
            distritos = db.distritos_con_riesgo(conn)
            meta = db.meta_ingesta(conn)
            if meta:
                meta = {
                    "fuente": meta["fuente"],
                    "calidad": meta["calidad"],
                    "fecha_medicion": _iso(meta["fecha_medicion"]),
                    "fecha_ingesta": _iso(meta["fecha_ingesta"]),
                    "antiguedad_minutos": round(float(meta["antiguedad_minutos"] or 0), 1),
                }
        finally:
            conn.close()
        if distritos:
            return {
                "total": len(distritos),
                "distritos": distritos,
                "fuente": "resultado_riesgo + distrito_censal",
                "meta": meta,
            }
    except Exception as error:  # noqa: BLE001
        logger.warning("No se pudo leer distritos desde PostgreSQL: %s", error)

    return {
        "total": len(RIESGO_DISTRITOS),
        "distritos": RIESGO_DISTRITOS,
        "fuente": "fallback-local",
        "meta": None,
    }


@app.post("/datos/sincronizar", dependencies=[Depends(verificar_token_interno)])
def sincronizar() -> dict[str, Any]:
    """Descarga Open-Meteo en batch y persiste las mediciones (uso interno)."""
    resultado = ingestion.sincronizar()
    ESTADO["ultima_sincronizacion"] = {
        "fecha": datetime.now().isoformat(),
        "ok": resultado.get("ok"),
        "registros": resultado.get("registros", 0),
        "error": resultado.get("error"),
    }
    return resultado


def _historico_zona(
    zona: str,
    desde: str | None = None,
    hasta: str | None = None,
    limite: int = 100,
) -> list[dict[str, Any]] | None:
    conn = db.get_conn()
    try:
        fila_zona = db.zona_por_clave(conn, parsers.slugify(zona)) or db.zona_por_clave(conn, zona)
        if not fila_zona:
            return None
        return db.historico_variable(conn, fila_zona["id_zona"], desde, hasta, limite)
    finally:
        conn.close()


def _validar_fecha_param(valor: str | None, nombre: str) -> str | None:
    if not valor:
        return None
    try:
        datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail=f"'{nombre}' debe ser una fecha ISO-8601"
        ) from error
    return valor


def _serializar_medicion(medicion: dict[str, Any]) -> dict[str, Any]:
    features = medicion.get("features")
    if isinstance(features, str):
        try:
            features = json.loads(features)
        except (TypeError, ValueError):
            features = None
    return {
        "fecha_medicion": _iso(medicion.get("fecha_medicion")),
        "temperatura": medicion.get("temperatura"),
        "humedad": medicion.get("humedad"),
        "velocidad_viento": medicion.get("velocidad_viento"),
        "precipitacion": medicion.get("precipitacion"),
        "cobertura_vegetal": medicion.get("cobertura_vegetal"),
        "fuente": medicion.get("fuente"),
        "calidad": medicion.get("calidad"),
        "features": features if isinstance(features, dict) else {},
    }


@app.get("/datos/{zona}/historico", dependencies=[Depends(verificar_token_interno)])
def obtener_historico(
    zona: str,
    desde: str | None = None,
    hasta: str | None = None,
    limite: int = 100,
) -> dict[str, Any]:
    """Serie histórica de variables ambientales de una zona (más reciente primero)."""
    desde = _validar_fecha_param(desde, "desde")
    hasta = _validar_fecha_param(hasta, "hasta")
    try:
        filas = _historico_zona(zona, desde, hasta, limite)
    except Exception as error:  # noqa: BLE001
        logger.warning("No se pudo leer el histórico de %s: %s", zona, error)
        raise HTTPException(status_code=503, detail="Histórico no disponible") from error

    if filas is None:
        raise HTTPException(status_code=404, detail=f"zona '{zona}' no encontrada")

    return {
        "zona": parsers.slugify(zona),
        "total": len(filas),
        "serie": [_serializar_medicion(fila) for fila in filas],
    }


@app.get("/datos/{zona}/infraestructura", dependencies=[Depends(verificar_token_interno)])
def obtener_infraestructura(zona: str, tipo: str | None = None) -> dict[str, Any]:
    """Infraestructura crítica de una zona (consulta PostGIS por id_zona)."""
    conn = db.get_conn()
    try:
        fila_zona = db.zona_por_clave(conn, parsers.slugify(zona)) or db.zona_por_clave(conn, zona)
        if not fila_zona:
            raise HTTPException(status_code=404, detail=f"zona '{zona}' no encontrada")
        elementos = db.infraestructura_por_zona(conn, fila_zona["id_zona"], tipo)
    finally:
        conn.close()
    por_tipo: dict[str, int] = {}
    for elemento in elementos:
        clave = elemento.get("tipo") or "otro"
        por_tipo[clave] = por_tipo.get(clave, 0) + 1
    return {
        "zona": fila_zona["slug"],
        "total": len(elementos),
        "por_tipo": por_tipo,
        "elementos": elementos,
    }


@app.get("/datos/{zona}/vegetacion", dependencies=[Depends(verificar_token_interno)])
def obtener_vegetacion(zona: str) -> dict[str, Any]:
    """Capas NDVI/combustible ya persistidas por la ingesta batch."""
    conn = db.get_conn()
    try:
        fila_zona = db.zona_por_clave(conn, parsers.slugify(zona)) or db.zona_por_clave(conn, zona)
        if not fila_zona:
            raise HTTPException(status_code=404, detail=f"zona '{zona}' no encontrada")
        capas = db.capas_vegetacion(conn, fila_zona["id_zona"])
    finally:
        conn.close()
    if not capas:
        return {"zona": fila_zona["slug"], "capas": [], "fuente": "no disponible"}
    return {
        "zona": fila_zona["slug"],
        "capas": [
            {
                "indice": capa.get("indice"),
                "valor": capa.get("valor"),
                "categoria": capa.get("categoria"),
                "fecha_captura": _iso(capa.get("fecha_captura")),
                "fuente": capa.get("fuente"),
            }
            for capa in capas
        ],
        "fuente": capas[0].get("fuente") or "ingesta batch",
    }


@app.post("/datos/vegetacion", dependencies=[Depends(verificar_token_interno)])
def ingestar_vegetacion(peticion: IngestaVegetacionRequest) -> dict[str, Any]:
    """Ingesta batch de NDVI/combustible; nunca se invoca durante un request de usuario."""
    conn = db.get_conn()
    try:
        registros = db.insertar_capas_vegetacion(
            conn, [capa.model_dump() for capa in peticion.capas]
        )
    finally:
        conn.close()
    return {"ok": True, "registros": registros}


@app.get("/datos/{zona}", dependencies=[Depends(verificar_token_interno)])
def obtener_datos(zona: str) -> dict[str, Any]:
    try:
        respuesta = _leer_zona(zona)
    except Exception as error:  # noqa: BLE001
        logger.warning("Fallo leyendo %s desde PostgreSQL: %s", zona, error)
        return _respuesta_fallback(zona, f"BD no disponible: {error}")

    if "error" in respuesta:
        return respuesta
    return respuesta


Instrumentator().instrument(app).expose(app)
