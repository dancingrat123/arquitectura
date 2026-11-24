"""Carga inicial real (IDE/INE, CONAF, EMAS) y sincronización Open-Meteo."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Any

import httpx

import db
import parsers

logger = logging.getLogger("data-service.ingestion")

RUTA_CONAF = os.getenv("CONAF_CSV", "/app/datos/ml/conaf.csv")
RUTA_EMAS = os.getenv("EMAS_CSV", "/app/datos/ml/emas.csv")
RUTA_CLIMA = os.getenv("CLIMA_CSV", "/app/datos/ml/clima.csv")
RUTA_GEOJSON = os.getenv("DISTRITOS_GEOJSON", "/app/datos/geo/distritos_region.geojson")
RUTA_COMUNAS_REGION = os.getenv("COMUNAS_REGION", "/app/datos/geo/comunas_region.json")
OPEN_METEO_URL = os.getenv("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast")
OPEN_METEO_TIMEOUT = float(os.getenv("OPEN_METEO_TIMEOUT", "30"))
TAMANO_LOTE = int(os.getenv("OPEN_METEO_BATCH", "50"))

VARIABLES_OPEN_METEO = "temperature_2m_mean,temperature_2m_max,relative_humidity_2m_mean,wind_speed_10m_max,precipitation_sum"
PARAMETROS_OPEN_METEO = {
    "daily": VARIABLES_OPEN_METEO,
    "past_days": 31,
    "forecast_days": 1,
    "timezone": "UTC",
    "models": "ecmwf_ifs025",
}

ZONAS_SEED: list[dict[str, Any]] = [
    {
        "slug": "quilpue",
        "nombre_zona": "Quilpué",
        "comuna": "Quilpué",
        "codigo_comuna": "5801",
        "latitud": -33.0486,
        "longitud": -71.4425,
    },
    {
        "slug": "vina_del_mar",
        "nombre_zona": "Viña del Mar",
        "comuna": "Viña del Mar",
        "codigo_comuna": "5109",
        "latitud": -33.0245,
        "longitud": -71.5518,
    },
    {
        "slug": "valparaiso",
        "nombre_zona": "Valparaíso",
        "comuna": "Valparaíso",
        "codigo_comuna": "5101",
        "latitud": -33.0472,
        "longitud": -71.6127,
    },
]

PUNTO_REGION = (-33.0472, -71.6127)


class IngestaError(RuntimeError):
    """Error controlado del proceso de ingesta."""


# ---------------------------------------------------------------------------
# Bootstrap (una sola vez, idempotente)
# ---------------------------------------------------------------------------


def bootstrap() -> dict[str, Any]:
    conn = db.get_conn()
    resultados: dict[str, Any] = {}
    try:
        db.ensure_schema(conn)
        fuentes = db.upsert_fuentes(conn)
        conn.commit()

        pasos = [
            ("ide_chile", lambda: _bootstrap_ide(conn, fuentes["IDE Chile / INE Geodatos"])),
            ("zonas", lambda: _bootstrap_zonas(conn)),
            ("comunas_region", lambda: _bootstrap_comunas_region(conn)),
            ("conaf", lambda: _bootstrap_conaf(conn, fuentes["CONAF"])),
            ("legado", lambda: _limpiar_legado(conn)),
            ("emas", lambda: _bootstrap_emas(conn, fuentes["EMAS"])),
            ("resultados", lambda: _reparar_resultados(conn)),
        ]
        for nombre, funcion in pasos:
            resultados[nombre] = _ejecutar_paso(conn, nombre, funcion)
        return resultados
    finally:
        conn.close()


def _ejecutar_paso(conn, nombre: str, funcion) -> dict[str, Any]:
    inicio = time.time()
    try:
        registros = funcion() or 0
        conn.commit()
        db.marcar_ingesta(conn, nombre, "ok", f"{registros} registros", registros)
        conn.commit()
        logger.info("[bootstrap] %s ok (%s registros, %.0f ms)", nombre, registros, (time.time() - inicio) * 1000)
        return {"estado": "ok", "registros": registros}
    except Exception as error:  # noqa: BLE001
        conn.rollback()
        mensaje = f"{type(error).__name__}: {error}"
        logger.exception("[bootstrap] %s falló: %s", nombre, mensaje)
        try:
            db.marcar_ingesta(conn, nombre, "error", mensaje, 0)
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback()
        return {"estado": "error", "detalle": mensaje}


def _bootstrap_ide(conn, id_fuente: int) -> int:
    features = parsers.leer_geojson(RUTA_GEOJSON)
    registros = 0
    with conn.cursor() as cur:
        for feature in features:
            distrito = parsers.distrito_desde_feature(feature)
            if not distrito:
                continue
            cur.execute(
                """
                INSERT INTO distrito_censal
                    (codigo_distrito, codigo_comuna, nombre_comuna, tipo, poblacion,
                     superficie, geom, id_fuente)
                VALUES
                    (%(codigo_distrito)s, %(codigo_comuna)s, %(nombre_comuna)s, %(tipo)s,
                     %(poblacion)s, %(superficie)s,
                     ST_Multi(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(geom_geojson)s), 3857), 4326)),
                     %(id_fuente)s)
                ON CONFLICT (codigo_distrito) DO UPDATE
                    SET codigo_comuna = EXCLUDED.codigo_comuna,
                        nombre_comuna = EXCLUDED.nombre_comuna,
                        tipo = EXCLUDED.tipo,
                        poblacion = EXCLUDED.poblacion,
                        superficie = EXCLUDED.superficie,
                        geom = EXCLUDED.geom,
                        id_fuente = EXCLUDED.id_fuente
                """,
                {**distrito, "id_fuente": id_fuente},
            )
            registros += 1
    db.marcar_fuente(conn, id_fuente, registros)
    return registros


def _bootstrap_zonas(conn) -> int:
    registros = 0
    with conn.cursor() as cur:
        for zona in ZONAS_SEED:
            cur.execute(
                """
                SELECT id_zona FROM comuna_zona
                WHERE slug = %(slug)s
                   OR lower(comuna) = lower(%(comuna)s)
                   OR lower(nombre_zona) = lower(%(nombre_zona)s)
                ORDER BY id_zona
                LIMIT 1
                """,
                zona,
            )
            fila = cur.fetchone()
            if fila:
                cur.execute(
                    """
                    UPDATE comuna_zona
                    SET slug = %(slug)s,
                        comuna = %(comuna)s,
                        nombre_zona = %(nombre_zona)s,
                        region = 'Valparaíso',
                        codigo_comuna = %(codigo_comuna)s,
                        latitud = %(latitud)s,
                        longitud = %(longitud)s,
                        geom = ST_SetSRID(ST_MakePoint(%(longitud)s, %(latitud)s), 4326),
                        geometria = json_build_object(
                            'type', 'Point',
                            'coordinates', json_build_array(%(longitud)s, %(latitud)s)
                        )::text
                    WHERE id_zona = %(id_zona)s
                    """,
                    {**zona, "id_zona": fila[0]},
                )
            else:
                cur.execute(
                    """
                    INSERT INTO comuna_zona
                        (nombre_zona, comuna, region, slug, codigo_comuna, latitud, longitud, geom)
                    VALUES
                        (%(nombre_zona)s, %(comuna)s, 'Valparaíso', %(slug)s, %(codigo_comuna)s,
                         %(latitud)s, %(longitud)s,
                         ST_SetSRID(ST_MakePoint(%(longitud)s, %(latitud)s), 4326))
                    """,
                    zona,
                )
            registros += 1

        # Nota: no se deriva la coordenada de la zona desde el centroide de sus
        # distritos. Los distritos censales incluyen territorio rural (incluso
        # geometrías atípicas) y desplazarían la consulta meteorológica fuera
        # del centro urbano. Se mantienen las coordenadas urbanas validadas.
    return registros


def _bootstrap_comunas_region(conn) -> int:
    """Siembra TODAS las comunas de la región (38) desde `comunas_region.json`.

    Idempotente: hace upsert por código INE o slug. Las 3 comunas operativas
    conservan sus coordenadas urbanas validadas (vienen en el JSON).
    """
    if not os.path.exists(RUTA_COMUNAS_REGION):
        logger.warning("No se encontró %s: solo se sembrarán las 3 zonas base", RUTA_COMUNAS_REGION)
        return 0
    with open(RUTA_COMUNAS_REGION, "r", encoding="utf-8") as archivo:
        comunas = json.load(archivo)

    registros = 0
    with conn.cursor() as cur:
        for comuna in comunas:
            geometria = json.dumps(
                {"type": "Point", "coordinates": [comuna["lon"], comuna["lat"]]}
            )
            cur.execute(
                """
                SELECT id_zona FROM comuna_zona
                WHERE codigo_comuna = %s OR slug = %s
                ORDER BY id_zona LIMIT 1
                """,
                (comuna["codigo"], comuna["slug"]),
            )
            fila = cur.fetchone()
            if fila:
                cur.execute(
                    """
                    UPDATE comuna_zona
                    SET nombre_zona = %s, comuna = %s, region = 'Valparaíso',
                        slug = %s, codigo_comuna = %s, latitud = %s, longitud = %s,
                        geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                        geometria = %s
                    WHERE id_zona = %s
                    """,
                    (
                        comuna["nombre"], comuna["nombre"], comuna["slug"],
                        comuna["codigo"], comuna["lat"], comuna["lon"],
                        comuna["lon"], comuna["lat"], geometria, fila[0],
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO comuna_zona
                        (nombre_zona, comuna, region, slug, codigo_comuna,
                         latitud, longitud, geom, geometria)
                    VALUES (%s, %s, 'Valparaíso', %s, %s, %s, %s,
                            ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s)
                    """,
                    (
                        comuna["nombre"], comuna["nombre"], comuna["slug"],
                        comuna["codigo"], comuna["lat"], comuna["lon"],
                        comuna["lon"], comuna["lat"], geometria,
                    ),
                )
            registros += 1
    return registros


def _limpiar_legado(conn) -> int:
    """Elimina restos del esquema simulado anterior (mediciones y semillas).

    Es idempotente: en un arranque limpio no encuentra nada que borrar.
    """
    eliminados = 0
    with conn.cursor() as cur:
        cur.execute("DELETE FROM variable_ambiental WHERE fuente IS NULL")
        eliminados += cur.rowcount
        cur.execute("DELETE FROM historial_incendio WHERE region IS NULL OR periodo IS NULL")
        eliminados += cur.rowcount
        cur.execute(
            """
            DELETE FROM fuente_datos f
            WHERE f.nombre_fuente <> ALL(%s)
              AND NOT EXISTS (SELECT 1 FROM trazabilidad t WHERE t.id_fuente = f.id_fuente)
              AND NOT EXISTS (SELECT 1 FROM distrito_censal d WHERE d.id_fuente = f.id_fuente)
            """,
            ([fuente["nombre_fuente"] for fuente in db.FUENTES],),
        )
        eliminados += cur.rowcount
    return eliminados


def _bootstrap_conaf(conn, id_fuente: int) -> int:
    registros = 0
    lat, lon = PUNTO_REGION
    with conn.cursor() as cur:
        for registro in parsers.filtrar_region(parsers.leer_conaf(RUTA_CONAF)):
            cur.execute(
                """
                INSERT INTO historial_incendio
                    (id_zona, fecha_evento, fuente, region, periodo, nro_incendios,
                     nro_incendios_ant, nro_incendios_prom_quint,
                     superficie_afectada, superficie_afectada_ant, geom)
                VALUES
                    (NULL, %(periodo)s, 'CONAF', %(region)s, %(periodo)s, %(nro_incendios)s,
                     %(nro_incendios_ant)s, %(nro_incendios_prom_quint)s,
                     %(superficie_afectada)s, %(superficie_afectada_ant)s,
                     ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326))
                ON CONFLICT (region, periodo, fuente) DO NOTHING
                """,
                {**registro, "lat": lat, "lon": lon},
            )
            registros += cur.rowcount
    db.marcar_fuente(conn, id_fuente, registros)
    return registros


def _bootstrap_emas(conn, id_fuente: int) -> int:
    estaciones = parsers.leer_emas(RUTA_EMAS)
    lecturas, fecha_snapshot = parsers.leer_clima(RUTA_CLIMA)
    zonas = db.zonas_activas(conn)
    registros = 0
    with conn.cursor() as cur:
        for zona in zonas:
            cur.execute(
                "SELECT 1 FROM variable_ambiental WHERE id_zona = %s AND fuente LIKE 'EMAS%%' LIMIT 1",
                (zona["id_zona"],),
            )
            if cur.fetchone():
                continue
            cercana = parsers.estacion_mas_cercana(
                zona["latitud"], zona["longitud"], estaciones, lecturas
            )
            if not cercana:
                logger.warning("Sin estación EMAS utilizable para %s", zona["slug"])
                continue
            cur.execute(
                """
                INSERT INTO variable_ambiental
                    (id_zona, temperatura, humedad, velocidad_viento, precipitacion,
                     cobertura_vegetal, fuente, calidad, fecha_medicion, fecha_ingesta, geom)
                VALUES
                    (%s, %s, %s, %s, %s, NULL, %s, 'ok', %s, NOW(),
                     (SELECT geom FROM comuna_zona WHERE id_zona = %s))
                RETURNING id_variable
                """,
                (
                    zona["id_zona"],
                    cercana["temperatura"],
                    cercana["humedad"],
                    cercana["viento"],
                    cercana["precipitacion"],
                    f"EMAS ({cercana['estacion']['nombre']})",
                    fecha_snapshot or datetime.now(),
                    zona["id_zona"],
                ),
            )
            id_variable = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO trazabilidad (id_zona, id_fuente, variable_utilizada, fecha_uso)
                VALUES (%s, %s, %s, NOW())
                """,
                (zona["id_zona"], id_fuente, "TEMP_MED,HUMEDAD_REL,VEL_VIENTO,PRECIPITACION"),
            )
            logger.info(
                "[bootstrap] EMAS %s -> %s (%.1f km) id_variable=%s",
                zona["slug"],
                cercana["estacion"]["nombre"],
                cercana["distancia_km"],
                id_variable,
            )
            registros += 1
    db.marcar_fuente(conn, id_fuente, registros)
    return registros


def _reparar_resultados(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE resultado_riesgo rr
            SET id_zona = z.id_zona
            FROM comuna_zona z
            WHERE rr.id_zona IS NULL AND rr.zona = z.slug
            """
        )
        return cur.rowcount


# ---------------------------------------------------------------------------
# Sincronización horaria con Open-Meteo
# ---------------------------------------------------------------------------


def sincronizar() -> dict[str, Any]:
    inicio = time.time()
    conn = db.get_conn()
    zonas: list[dict[str, Any]] = []
    try:
        zonas = db.zonas_activas(conn)
        conocidas = [z["slug"] for z in zonas]
        if not zonas:
            raise IngestaError("No hay zonas activas con coordenadas")

        mediciones = _descargar_openmeteo(zonas)
        id_fuente = db.upsert_fuentes(conn)["Open-Meteo"]

        registros = 0
        actualizadas: list[str] = []
        fecha_medicion: datetime | None = None
        with conn.cursor() as cur:
            for zona, medicion in zip(zonas, mediciones):
                serie = medicion.get("serie") or []
                if not medicion.get("valido") or not serie:
                    logger.warning("Medición Open-Meteo inválida para %s: %s", zona["slug"], medicion)
                    continue
                features = parsers.construir_features_clima(serie)
                ultimo = serie[-1]
                calidad = "ok" if len(serie) >= 30 else "degradado"
                fecha = ultimo.get("fecha") or datetime.now()
                if not isinstance(fecha, datetime):
                    fecha = datetime.now()
                fecha_medicion = fecha if fecha_medicion is None else max(fecha_medicion, fecha)
                cur.execute(
                    """
                    INSERT INTO variable_ambiental
                        (id_zona, temperatura, humedad, velocidad_viento, precipitacion,
                         cobertura_vegetal, fuente, calidad, fecha_medicion, fecha_ingesta, geom, features)
                    VALUES
                        (%s, %s, %s, %s, %s, NULL, 'Open-Meteo', %s, %s, NOW(),
                         (SELECT geom FROM comuna_zona WHERE id_zona = %s), %s)
                    RETURNING id_variable
                    """,
                    (
                        zona["id_zona"],
                        ultimo.get("TEMP_MED"),
                        ultimo.get("HUMEDAD_REL"),
                        ultimo.get("VEL_VIENTO"),
                        ultimo.get("PRECIPITACION"),
                        calidad,
                        fecha,
                        zona["id_zona"],
                        json.dumps(features),
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO trazabilidad (id_zona, id_fuente, variable_utilizada, fecha_uso)
                    VALUES (%s, %s, %s, NOW())
                    """,
                    (zona["id_zona"], id_fuente, VARIABLES_OPEN_METEO),
                )
                registros += 1
                actualizadas.append(zona["slug"])

        if registros == 0:
            raise IngestaError("Open-Meteo no devolvió mediciones válidas")

        db.marcar_fuente(conn, id_fuente, registros)
        db.marcar_ingesta(
            conn,
            "openmeteo",
            "ok",
            f"{registros} zonas actualizadas desde Open-Meteo",
            registros,
        )
        conn.commit()
        duracion = (time.time() - inicio) * 1000
        logger.info("[sync] %s zonas actualizadas en %.0f ms", registros, duracion)
        return {
            "ok": True,
            "zonas_actualizadas": actualizadas,
            "zonas_conocidas": conocidas,
            "registros": registros,
            "fuente": "Open-Meteo",
            "fecha_medicion": fecha_medicion.isoformat() if fecha_medicion else None,
            "duracion_ms": round(duracion, 1),
        }
    except Exception as error:  # noqa: BLE001
        conn.rollback()
        mensaje = f"{type(error).__name__}: {error}"
        logger.warning("[sync] falló, se conserva el último registro válido: %s", mensaje)
        try:
            db.marcar_ingesta(conn, "openmeteo", "error", mensaje, 0)
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback()
        return {
            "ok": False,
            "error": mensaje,
            "zonas_actualizadas": [],
            "zonas_conocidas": [z["slug"] for z in zonas] if zonas else [],
            "duracion_ms": round((time.time() - inicio) * 1000, 1),
        }
    finally:
        conn.close()


def _descargar_openmeteo(zonas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mediciones: list[dict[str, Any]] = []
    for inicio in range(0, len(zonas), TAMANO_LOTE):
        lote = zonas[inicio : inicio + TAMANO_LOTE]
        parametros = {
            "latitude": ",".join(f"{z['latitud']:.6f}" for z in lote),
            "longitude": ",".join(f"{z['longitud']:.6f}" for z in lote),
            "daily": VARIABLES_OPEN_METEO,
            "past_days": 31,
            "forecast_days": 1,
            "timezone": "UTC",
            "models": "ecmwf_ifs025",
        }
        respuesta = httpx.get(OPEN_METEO_URL, params=parametros, timeout=OPEN_METEO_TIMEOUT)
        respuesta.raise_for_status()
        items = parsers.parse_openmeteo_diario(respuesta.json())
        if len(items) != len(lote):
            raise IngestaError(
                f"Open-Meteo devolvió {len(items)} ubicaciones para {len(lote)} zonas"
            )
        mediciones.extend(items)
    return mediciones
