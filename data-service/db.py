"""Acceso a PostgreSQL/PostGIS y migración idempotente de SIRIF."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger("data-service.db")

DDL: list[str] = [
    "CREATE EXTENSION IF NOT EXISTS postgis",
    # --- comuna_zona -------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS comuna_zona (
        id_zona SERIAL PRIMARY KEY,
        nombre_zona VARCHAR(100) NOT NULL,
        comuna VARCHAR(100) NOT NULL,
        region VARCHAR(50) DEFAULT 'Valparaíso',
        geometria TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "ALTER TABLE comuna_zona ADD COLUMN IF NOT EXISTS slug VARCHAR(100)",
    "ALTER TABLE comuna_zona ADD COLUMN IF NOT EXISTS codigo_comuna VARCHAR(10)",
    "ALTER TABLE comuna_zona ADD COLUMN IF NOT EXISTS latitud FLOAT",
    "ALTER TABLE comuna_zona ADD COLUMN IF NOT EXISTS longitud FLOAT",
    "ALTER TABLE comuna_zona ADD COLUMN IF NOT EXISTS geom GEOMETRY(Point, 4326)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_comuna_zona_slug ON comuna_zona (slug) WHERE slug IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_comuna_zona_geom ON comuna_zona USING GIST (geom)",
    # --- variable_ambiental ------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS variable_ambiental (
        id_variable SERIAL PRIMARY KEY,
        id_zona INT REFERENCES comuna_zona(id_zona),
        temperatura FLOAT NOT NULL,
        humedad FLOAT NOT NULL,
        velocidad_viento FLOAT NOT NULL,
        cobertura_vegetal VARCHAR(20),
        fecha_medicion TIMESTAMP DEFAULT NOW()
    )
    """,
    "ALTER TABLE variable_ambiental ADD COLUMN IF NOT EXISTS precipitacion FLOAT DEFAULT 0",
    "ALTER TABLE variable_ambiental ADD COLUMN IF NOT EXISTS fuente VARCHAR(60)",
    "ALTER TABLE variable_ambiental ADD COLUMN IF NOT EXISTS calidad VARCHAR(20)",
    "ALTER TABLE variable_ambiental ADD COLUMN IF NOT EXISTS fecha_ingesta TIMESTAMP DEFAULT NOW()",
    "ALTER TABLE variable_ambiental ADD COLUMN IF NOT EXISTS geom GEOMETRY(Point, 4326)",
    "ALTER TABLE variable_ambiental ADD COLUMN IF NOT EXISTS features JSONB",
    "CREATE INDEX IF NOT EXISTS idx_variable_zona_fecha ON variable_ambiental (id_zona, fecha_medicion DESC)",
    "CREATE INDEX IF NOT EXISTS idx_variable_geom ON variable_ambiental USING GIST (geom)",
    # --- historial_incendio ------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS historial_incendio (
        id_incendio SERIAL PRIMARY KEY,
        id_zona INT REFERENCES comuna_zona(id_zona),
        fecha_evento DATE NOT NULL,
        fuente VARCHAR(50) DEFAULT 'CONAF',
        superficie_afectada FLOAT,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "ALTER TABLE historial_incendio ADD COLUMN IF NOT EXISTS region VARCHAR(50)",
    "ALTER TABLE historial_incendio ADD COLUMN IF NOT EXISTS periodo DATE",
    "ALTER TABLE historial_incendio ADD COLUMN IF NOT EXISTS nro_incendios INT DEFAULT 0",
    "ALTER TABLE historial_incendio ADD COLUMN IF NOT EXISTS nro_incendios_ant INT DEFAULT 0",
    "ALTER TABLE historial_incendio ADD COLUMN IF NOT EXISTS nro_incendios_prom_quint INT DEFAULT 0",
    "ALTER TABLE historial_incendio ADD COLUMN IF NOT EXISTS superficie_afectada_ant FLOAT DEFAULT 0",
    "ALTER TABLE historial_incendio ADD COLUMN IF NOT EXISTS geom GEOMETRY(Point, 4326)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_historial_region_periodo_fuente ON historial_incendio (region, periodo, fuente)",
    "CREATE INDEX IF NOT EXISTS idx_historial_geom ON historial_incendio USING GIST (geom)",
    # --- resultado_riesgo --------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS resultado_riesgo (
        id_resultado SERIAL PRIMARY KEY,
        id_zona INT REFERENCES comuna_zona(id_zona),
        zona VARCHAR(100) NOT NULL,
        nivel_riesgo VARCHAR(10) NOT NULL,
        puntaje INT NOT NULL,
        puntaje_maximo INT NOT NULL,
        version_modelo VARCHAR(20) DEFAULT 'v1.0',
        fecha_evaluacion TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_zona_fecha ON resultado_riesgo (zona, fecha_evaluacion DESC)",
    # --- resultado_riesgo: snapshot analítico (Fase 1) --------------------
    "ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS probabilidad FLOAT",
    "ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS fuera_ventana_estacional BOOLEAN DEFAULT FALSE",
    "ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS features JSONB",
    "ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS variables JSONB",
    "ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS factores_predominantes JSONB",
    "ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS origen VARCHAR(20) DEFAULT 'automatico'",
    "ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS usuario VARCHAR(50)",
    "CREATE INDEX IF NOT EXISTS idx_resultado_zona_fecha ON resultado_riesgo (zona, fecha_evaluacion DESC)",
    # --- fuente_datos ------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS fuente_datos (
        id_fuente SERIAL PRIMARY KEY,
        nombre_fuente VARCHAR(100) NOT NULL,
        tipo_fuente VARCHAR(50),
        enlace_url VARCHAR(255),
        fecha_descarga DATE,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "ALTER TABLE fuente_datos ADD COLUMN IF NOT EXISTS ultima_ingesta TIMESTAMP",
    "ALTER TABLE fuente_datos ADD COLUMN IF NOT EXISTS registros_ultimo_lote INT",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_fuente_nombre ON fuente_datos (nombre_fuente)",
    # --- trazabilidad ------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS trazabilidad (
        id_trazabilidad SERIAL PRIMARY KEY,
        id_zona INT REFERENCES comuna_zona(id_zona),
        id_fuente INT REFERENCES fuente_datos(id_fuente),
        variable_utilizada VARCHAR(100),
        fecha_uso TIMESTAMP DEFAULT NOW()
    )
    """,
    "ALTER TABLE trazabilidad ALTER COLUMN variable_utilizada TYPE VARCHAR(255)",
    # --- distrito_censal ---------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS distrito_censal (
        id_distrito SERIAL PRIMARY KEY,
        codigo_distrito VARCHAR(20) UNIQUE NOT NULL,
        codigo_comuna VARCHAR(10) NOT NULL,
        nombre_comuna VARCHAR(100),
        tipo VARCHAR(20),
        poblacion INT,
        superficie FLOAT,
        geom GEOMETRY(MultiPolygon, 4326),
        id_fuente INT REFERENCES fuente_datos(id_fuente),
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_distrito_comuna ON distrito_censal (codigo_comuna)",
    "CREATE INDEX IF NOT EXISTS idx_distrito_geom ON distrito_censal USING GIST (geom)",
    # --- ingesta_control ---------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS ingesta_control (
        id_proceso TEXT PRIMARY KEY,
        ultima_ejecucion TIMESTAMP,
        ultimo_exito TIMESTAMP,
        estado VARCHAR(20),
        detalle TEXT,
        registros INT DEFAULT 0
    )
    """,
    # --- revision_log (histórico de revisiones del modelo) -----------------
    """
    CREATE TABLE IF NOT EXISTS revision_log (
        id_revision SERIAL PRIMARY KEY,
        fecha_inicio TIMESTAMP DEFAULT NOW(),
        fecha_fin TIMESTAMP,
        datos_utilizados TEXT,
        modelo_anterior VARCHAR(50),
        modelo_nuevo VARCHAR(50),
        metrica_anterior FLOAT,
        metrica_nueva FLOAT,
        decision VARCHAR(20),
        justificacion TEXT
    )
    """,
    # --- Fase 2: simulación, alertas, notificaciones y estado --------------
    """
    CREATE TABLE IF NOT EXISTS simulacion (
        id_simulacion SERIAL PRIMARY KEY,
        id_zona INT REFERENCES comuna_zona(id_zona),
        zona VARCHAR(100) NOT NULL,
        usuario VARCHAR(50),
        escenario JSONB NOT NULL,
        variables_base JSONB,
        features_base JSONB,
        probabilidad_original FLOAT,
        nivel_original VARCHAR(10),
        probabilidad_simulada FLOAT,
        nivel_simulado VARCHAR(10),
        delta_probabilidad FLOAT,
        factores_predominantes JSONB,
        fuera_ventana_estacional BOOLEAN,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_simulacion_zona_fecha ON simulacion (zona, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS alerta (
        id_alerta SERIAL PRIMARY KEY,
        id_zona INT REFERENCES comuna_zona(id_zona),
        zona VARCHAR(100) NOT NULL,
        tipo VARCHAR(30) NOT NULL,
        nivel_anterior VARCHAR(10),
        nivel_nuevo VARCHAR(10),
        probabilidad FLOAT,
        umbral FLOAT,
        mensaje TEXT NOT NULL,
        estado VARCHAR(20) DEFAULT 'activa',
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_alerta_estado_fecha ON alerta (estado, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_alerta_zona_fecha ON alerta (zona, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS notificacion (
        id_notificacion SERIAL PRIMARY KEY,
        tipo VARCHAR(30) NOT NULL,
        titulo VARCHAR(200) NOT NULL,
        mensaje TEXT NOT NULL,
        zona VARCHAR(100),
        nivel VARCHAR(10),
        metadata JSONB,
        leida BOOLEAN DEFAULT FALSE,
        usuario VARCHAR(50),
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_notificacion_leida_fecha ON notificacion (leida, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS estado_servicio (
        servicio VARCHAR(50) PRIMARY KEY,
        estado VARCHAR(20) NOT NULL,
        detalle TEXT,
        ultima_comprobacion TIMESTAMP,
        latencia_ms INT
    )
    """,
    # --- Fase 3: roles, gestión territorial y trazabilidad -----------------
    """
    CREATE TABLE IF NOT EXISTS usuario (
        id_usuario SERIAL PRIMARY KEY,
        username VARCHAR(50) UNIQUE NOT NULL,
        nombre_completo VARCHAR(100),
        rol VARCHAR(20) NOT NULL,
        hash_password VARCHAR(255) NOT NULL,
        activo BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS infraestructura_critica (
        id_infra SERIAL PRIMARY KEY,
        tipo VARCHAR(30) NOT NULL,
        nombre VARCHAR(150),
        descripcion TEXT,
        id_zona INT REFERENCES comuna_zona(id_zona),
        direccion VARCHAR(200),
        latitud FLOAT,
        longitud FLOAT,
        geom GEOMETRY(Point, 4326),
        expuestos INT,
        fuente VARCHAR(100),
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_infra_geom ON infraestructura_critica USING GIST (geom)",
    "CREATE INDEX IF NOT EXISTS idx_infra_zona ON infraestructura_critica (id_zona)",
    """
    CREATE TABLE IF NOT EXISTS observacion_terreno (
        id_observacion SERIAL PRIMARY KEY,
        id_zona INT REFERENCES comuna_zona(id_zona),
        zona VARCHAR(100) NOT NULL,
        tipo VARCHAR(30) NOT NULL,
        descripcion TEXT NOT NULL,
        severidad VARCHAR(10) DEFAULT 'media',
        estado VARCHAR(20) DEFAULT 'abierta',
        usuario VARCHAR(50),
        latitud FLOAT,
        longitud FLOAT,
        geom GEOMETRY(Point, 4326),
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_obs_geom ON observacion_terreno USING GIST (geom)",
    "CREATE INDEX IF NOT EXISTS idx_obs_zona_fecha ON observacion_terreno (zona, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS accion_preventiva (
        id_accion SERIAL PRIMARY KEY,
        id_zona INT REFERENCES comuna_zona(id_zona),
        zona VARCHAR(100) NOT NULL,
        descripcion TEXT NOT NULL,
        tipo VARCHAR(40),
        responsable VARCHAR(50),
        estado VARCHAR(20) DEFAULT 'pendiente',
        fecha_programada DATE,
        fecha_ejecucion DATE,
        prioridad VARCHAR(10) DEFAULT 'media',
        observacion_relacionada INT REFERENCES observacion_terreno(id_observacion),
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_accion_zona_estado ON accion_preventiva (zona, estado)",
    """
    CREATE TABLE IF NOT EXISTS capa_vegetacion (
        id_capa SERIAL PRIMARY KEY,
        id_zona INT REFERENCES comuna_zona(id_zona),
        zona VARCHAR(100) NOT NULL,
        indice VARCHAR(20) NOT NULL,
        valor FLOAT,
        categoria VARCHAR(20),
        fecha_captura DATE,
        fuente VARCHAR(100),
        geom GEOMETRY(Polygon, 4326),
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_capa_geom ON capa_vegetacion USING GIST (geom)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_capa_zona_indice_fecha ON capa_vegetacion (zona, indice, fecha_captura, fuente)",
    """
    CREATE TABLE IF NOT EXISTS bitacora_decision (
        id_bitacora SERIAL PRIMARY KEY,
        usuario VARCHAR(50),
        id_zona INT REFERENCES comuna_zona(id_zona),
        zona VARCHAR(100),
        tipo_evento VARCHAR(40),
        detalle TEXT,
        metadata JSONB,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_bitacora_fecha ON bitacora_decision (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_bitacora_zona_fecha ON bitacora_decision (zona, created_at DESC)",
]

# Catálogo de fuentes reales del sistema.
FUENTES: list[dict[str, Any]] = [
    {
        "nombre_fuente": "IDE Chile / INE Geodatos",
        "tipo_fuente": "Geoportal",
        "enlace_url": "https://www.ide.cl",
    },
    {
        "nombre_fuente": "CONAF",
        "tipo_fuente": "Históricos",
        "enlace_url": "https://www.conaf.cl/incendios/",
    },
    {
        "nombre_fuente": "EMAS",
        "tipo_fuente": "Meteorológico",
        "enlace_url": "https://agromet.inia.cl",
    },
    {
        "nombre_fuente": "Open-Meteo",
        "tipo_fuente": "Meteorológico",
        "enlace_url": "https://open-meteo.com",
    },
]


def database_url() -> str | None:
    return os.getenv("DATABASE_URL")


def get_conn():
    import psycopg2

    return psycopg2.connect(database_url(), connect_timeout=10)


def esperar_db(max_intentos: int = 30, espera: float = 2.0) -> bool:
    import psycopg2

    for intento in range(1, max_intentos + 1):
        try:
            conn = get_conn()
            conn.close()
            return True
        except psycopg2.Error as error:
            logger.warning("PostgreSQL no disponible (%s/%s): %s", intento, max_intentos, error)
            time.sleep(espera)
    return False


def _hash_desde_entorno(valor: str | None) -> str | None:
    """Acepta el hash PBKDF2 literal o codificado en base64 (`base64:...`).

    El prefijo permite guardar el hash en `.env` sin que docker compose
    interprete sus `$` como variables.
    """
    if not valor:
        return None
    if valor.startswith("base64:"):
        import base64

        try:
            return base64.b64decode(valor[len("base64:"):]).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise RuntimeError("ADMIN_PASSWORD_HASH base64 inválido") from error
    return valor


def usuarios_seed() -> list[dict[str, str]]:
    """Usuarios iniciales desde el entorno (idempotente).

    `ADMIN_PASSWORD_HASH` es obligatorio: sin él no hay forma segura de
    iniciar sesión. Los roles de desarrollo son opcionales.
    """
    admin_hash = _hash_desde_entorno(os.getenv("ADMIN_PASSWORD_HASH"))
    if not admin_hash:
        raise RuntimeError(
            "Falta ADMIN_PASSWORD_HASH: genera un hash PBKDF2-SHA256 antes de arrancar"
        )
    semilla = [
        {
            "username": "admin",
            "nombre_completo": "Administrador SIRIF",
            "rol": "admin",
            "hash_password": admin_hash,
        }
    ]
    opcionales = (
        ("analista", "Analista SIRIF", "analista", "ANALISTA_PASSWORD_HASH"),
        ("visualizador", "Visualizador SIRIF", "visualizador", "VISUALIZADOR_PASSWORD_HASH"),
    )
    for username, nombre, rol, variable in opcionales:
        hash_password = _hash_desde_entorno(os.getenv(variable))
        if hash_password:
            semilla.append(
                {
                    "username": username,
                    "nombre_completo": nombre,
                    "rol": rol,
                    "hash_password": hash_password,
                }
            )
    return semilla


def seed_usuarios(conn) -> int:
    """Inserta los usuarios semilla sin sobrescribir hashes ya rotados."""
    usuarios = usuarios_seed()
    with conn.cursor() as cur:
        for usuario in usuarios:
            cur.execute(
                """
                INSERT INTO usuario (username, nombre_completo, rol, hash_password)
                VALUES (%(username)s, %(nombre_completo)s, %(rol)s, %(hash_password)s)
                ON CONFLICT (username) DO NOTHING
                """,
                usuario,
            )
    return len(usuarios)


def ensure_schema(conn) -> None:
    conn.autocommit = True
    try:
        cur = conn.cursor()
        for sentencia in DDL:
            try:
                cur.execute(sentencia)
            except Exception as error:  # noqa: BLE001
                raise RuntimeError(f"Fallo aplicando DDL: {sentencia[:80]}... -> {error}") from error
        cur.close()
        seed_usuarios(conn)
        logger.info("Esquema PostGIS verificado (%s sentencias)", len(DDL))
    finally:
        conn.autocommit = False


def upsert_fuentes(conn) -> dict[str, int]:
    from psycopg2.extras import RealDictCursor

    ids: dict[str, int] = {}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        for fuente in FUENTES:
            cur.execute(
                """
                INSERT INTO fuente_datos (nombre_fuente, tipo_fuente, enlace_url, fecha_descarga)
                VALUES (%(nombre_fuente)s, %(tipo_fuente)s, %(enlace_url)s, CURRENT_DATE)
                ON CONFLICT (nombre_fuente) DO UPDATE
                    SET tipo_fuente = EXCLUDED.tipo_fuente,
                        enlace_url = EXCLUDED.enlace_url
                RETURNING id_fuente
                """,
                fuente,
            )
            ids[fuente["nombre_fuente"]] = cur.fetchone()["id_fuente"]
    return ids


def marcar_ingesta(
    conn,
    proceso: str,
    estado: str,
    detalle: str = "",
    registros: int = 0,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingesta_control (id_proceso, ultima_ejecucion, ultimo_exito, estado, detalle, registros)
            VALUES (%s, NOW(), CASE WHEN %s = 'ok' THEN NOW() ELSE NULL END, %s, %s, %s)
            ON CONFLICT (id_proceso) DO UPDATE
                SET ultima_ejecucion = NOW(),
                    ultimo_exito = CASE WHEN EXCLUDED.estado = 'ok' THEN NOW() ELSE ingesta_control.ultimo_exito END,
                    estado = EXCLUDED.estado,
                    detalle = EXCLUDED.detalle,
                    registros = EXCLUDED.registros
            """,
            (proceso, estado, estado, detalle[:1000], registros),
        )


def marcar_fuente(conn, id_fuente: int, registros: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE fuente_datos
            SET ultima_ingesta = NOW(), registros_ultimo_lote = %s, fecha_descarga = CURRENT_DATE
            WHERE id_fuente = %s
            """,
            (registros, id_fuente),
        )


def zonas_activas(conn, region: str = "Valparaíso") -> list[dict[str, Any]]:
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id_zona, slug, comuna, codigo_comuna, latitud, longitud
            FROM comuna_zona
            WHERE region = %s AND latitud IS NOT NULL AND longitud IS NOT NULL
            ORDER BY id_zona
            """,
            (region,),
        )
        return [dict(fila) for fila in cur.fetchall()]


def zonas_conocidas(conn) -> list[str]:
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT slug FROM comuna_zona WHERE slug IS NOT NULL ORDER BY id_zona")
        return [fila["slug"] for fila in cur.fetchall()]


def zona_por_clave(conn, clave: str) -> dict[str, Any] | None:
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id_zona, slug, nombre_zona, comuna, region, codigo_comuna, latitud, longitud
            FROM comuna_zona
            WHERE slug = %s
               OR lower(comuna) = lower(%s)
               OR lower(nombre_zona) = lower(%s)
            ORDER BY id_zona
            LIMIT 1
            """,
            (clave, clave, clave),
        )
        fila = cur.fetchone()
        return dict(fila) if fila else None


def ultima_medicion(conn, id_zona: int) -> dict[str, Any] | None:
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id_variable, temperatura, humedad, velocidad_viento, precipitacion,
                   cobertura_vegetal, fuente, calidad, fecha_medicion, fecha_ingesta,
                   EXTRACT(EPOCH FROM (NOW() - fecha_medicion)) / 60.0 AS antiguedad_minutos,
                   features
            FROM variable_ambiental
            WHERE id_zona = %s
            ORDER BY fecha_medicion DESC, id_variable DESC
            LIMIT 1
            """,
            (id_zona,),
        )
        fila = cur.fetchone()
        return dict(fila) if fila else None


def historico_variable(
    conn,
    id_zona: int,
    desde: str | None = None,
    hasta: str | None = None,
    limite: int = 100,
) -> list[dict[str, Any]]:
    """Serie de mediciones de una zona, más reciente primero."""
    from psycopg2.extras import RealDictCursor

    condiciones = ["id_zona = %s"]
    parametros: list[Any] = [id_zona]
    if desde:
        condiciones.append("fecha_medicion >= %s")
        parametros.append(desde)
    if hasta:
        condiciones.append("fecha_medicion <= %s")
        parametros.append(hasta)
    parametros.append(max(1, min(int(limite or 100), 1000)))

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT fecha_medicion, temperatura, humedad, velocidad_viento,
                   precipitacion, cobertura_vegetal, fuente, calidad, features
            FROM variable_ambiental
            WHERE {' AND '.join(condiciones)}
            ORDER BY fecha_medicion DESC, id_variable DESC
            LIMIT %s
            """,
            parametros,
        )
        return [dict(fila) for fila in cur.fetchall()]


def ultimo_conaf(conn, region: str = "Valparaíso") -> dict[str, Any] | None:
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT region, periodo, nro_incendios, nro_incendios_ant,
                   nro_incendios_prom_quint, superficie_afectada, superficie_afectada_ant
            FROM historial_incendio
            WHERE region = %s AND periodo IS NOT NULL
            ORDER BY periodo DESC
            LIMIT 1
            """,
            (region,),
        )
        fila = cur.fetchone()
        return dict(fila) if fila else None


def ultima_trazabilidad(conn, id_zona: int) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_trazabilidad
            FROM trazabilidad
            WHERE id_zona = %s
            ORDER BY fecha_uso DESC, id_trazabilidad DESC
            LIMIT 1
            """,
            (id_zona,),
        )
        fila = cur.fetchone()
        return fila[0] if fila else None


def distritos_con_riesgo(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.codigo_distrito, COALESCE(r.nivel_riesgo, 'BAJO') AS nivel
            FROM distrito_censal d
            LEFT JOIN LATERAL (
                SELECT rr.nivel_riesgo
                FROM resultado_riesgo rr
                JOIN comuna_zona z ON z.slug = rr.zona
                WHERE z.codigo_comuna = d.codigo_comuna
                ORDER BY rr.fecha_evaluacion DESC
                LIMIT 1
            ) r ON TRUE
            """
        )
        return {fila[0]: fila[1] for fila in cur.fetchall()}


def infraestructura_por_zona(
    conn,
    id_zona: int,
    tipo: str | None = None,
) -> list[dict[str, Any]]:
    """Elementos de infraestructura crítica de una zona (consulta PostGIS)."""
    from psycopg2.extras import RealDictCursor

    condiciones = ["id_zona = %s"]
    parametros: list[Any] = [id_zona]
    if tipo:
        condiciones.append("tipo = %s")
        parametros.append(tipo)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT id_infra, tipo, nombre, descripcion, direccion,
                   COALESCE(latitud, ST_Y(geom)) AS latitud,
                   COALESCE(longitud, ST_X(geom)) AS longitud,
                   expuestos, fuente
            FROM infraestructura_critica
            WHERE {' AND '.join(condiciones)}
            ORDER BY tipo, id_infra
            """,
            parametros,
        )
        return [dict(fila) for fila in cur.fetchall()]


def capas_vegetacion(conn, id_zona: int) -> list[dict[str, Any]]:
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT indice, valor, categoria, fecha_captura, fuente
            FROM capa_vegetacion
            WHERE id_zona = %s
            ORDER BY fecha_captura DESC NULLS LAST, id_capa DESC
            """,
            (id_zona,),
        )
        return [dict(fila) for fila in cur.fetchall()]


def insertar_capas_vegetacion(conn, capas: list[dict[str, Any]]) -> int:
    """Ingesta batch idempotente de capas (NDVI/combustible)."""
    from psycopg2.extras import RealDictCursor

    registros = 0
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        for capa in capas:
            cur.execute(
                """
                SELECT id_zona FROM comuna_zona
                WHERE slug = %s OR lower(comuna) = lower(%s)
                ORDER BY id_zona
                LIMIT 1
                """,
                (capa["zona"], capa["zona"]),
            )
            fila = cur.fetchone()
            if not fila:
                continue
            cur.execute(
                """
                INSERT INTO capa_vegetacion
                    (id_zona, zona, indice, valor, categoria, fecha_captura, fuente)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (zona, indice, fecha_captura, fuente) DO UPDATE
                    SET valor = EXCLUDED.valor,
                        categoria = EXCLUDED.categoria
                """,
                (
                    fila["id_zona"],
                    capa["zona"],
                    capa.get("indice") or "NDVI",
                    capa.get("valor"),
                    capa.get("categoria"),
                    capa.get("fecha_captura"),
                    capa.get("fuente") or "ingesta batch",
                ),
            )
            registros += 1
    conn.commit()
    return registros


def meta_ingesta(conn) -> dict[str, Any] | None:
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT fuente, calidad, fecha_medicion, fecha_ingesta,
                   EXTRACT(EPOCH FROM (NOW() - fecha_medicion)) / 60.0 AS antiguedad_minutos
            FROM variable_ambiental
            ORDER BY fecha_medicion DESC, id_variable DESC
            LIMIT 1
            """
        )
        fila = cur.fetchone()
        return dict(fila) if fila else None
