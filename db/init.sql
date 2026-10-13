-- ============================================================
-- SIRIF - Esquema de base de datos
-- Basado en el modelo entidad-relación del informe arc42
-- ============================================================

-- 1. Tabla principal: COMUNA_ZONA
CREATE TABLE IF NOT EXISTS comuna_zona (
    id_zona SERIAL PRIMARY KEY,
    nombre_zona VARCHAR(100) NOT NULL,
    comuna VARCHAR(100) NOT NULL,
    region VARCHAR(50) DEFAULT 'Valparaíso',
    geometria TEXT,  -- En producción sería GEOMETRY con PostGIS
    created_at TIMESTAMP DEFAULT NOW()
);

-- 2. Variables ambientales por zona
CREATE TABLE IF NOT EXISTS variable_ambiental (
    id_variable SERIAL PRIMARY KEY,
    id_zona INT REFERENCES comuna_zona(id_zona),
    temperatura FLOAT NOT NULL,
    humedad FLOAT NOT NULL,
    velocidad_viento FLOAT NOT NULL,
    cobertura_vegetal VARCHAR(20),
    fecha_medicion TIMESTAMP DEFAULT NOW()
);

-- 3. Historial de incendios
CREATE TABLE IF NOT EXISTS historial_incendio (
    id_incendio SERIAL PRIMARY KEY,
    id_zona INT REFERENCES comuna_zona(id_zona),
    fecha_evento DATE NOT NULL,
    fuente VARCHAR(50) DEFAULT 'CONAF',
    superficie_afectada FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 4. Resultados de clasificación de riesgo (CACHÉ PRINCIPAL)
CREATE TABLE IF NOT EXISTS resultado_riesgo (
    id_resultado SERIAL PRIMARY KEY,
    id_zona INT REFERENCES comuna_zona(id_zona),
    zona VARCHAR(100) NOT NULL,
    nivel_riesgo VARCHAR(10) NOT NULL,
    puntaje INT NOT NULL,
    puntaje_maximo INT NOT NULL,
    version_modelo VARCHAR(20) DEFAULT 'v1.0',
    fecha_evaluacion TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_zona_fecha 
    ON resultado_riesgo(zona, fecha_evaluacion DESC);

-- 5. Catálogo de fuentes de datos (trazabilidad)
CREATE TABLE IF NOT EXISTS fuente_datos (
    id_fuente SERIAL PRIMARY KEY,
    nombre_fuente VARCHAR(100) NOT NULL,
    tipo_fuente VARCHAR(50),
    enlace_url VARCHAR(255),
    fecha_descarga DATE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 6. Tabla intermedia de trazabilidad
CREATE TABLE IF NOT EXISTS trazabilidad (
    id_trazabilidad SERIAL PRIMARY KEY,
    id_zona INT REFERENCES comuna_zona(id_zona),
    id_fuente INT REFERENCES fuente_datos(id_fuente),
    variable_utilizada VARCHAR(100),
    fecha_uso TIMESTAMP DEFAULT NOW()
);

-- 7. Log de revisiones sistemáticas (PRISMA)
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
);

-- ============================================================
-- DATOS DE EJEMPLO (SEED) para que la demo funcione
-- ============================================================

-- Comunas de la Región de Valparaíso
INSERT INTO comuna_zona (nombre_zona, comuna, region, geometria) VALUES
    ('Quilpué Centro', 'Quilpué', 'Valparaíso', '{"type":"Point","coordinates":[-71.4425,-33.0486]}'),
    ('Viña del Mar', 'Viña del Mar', 'Valparaíso', '{"type":"Point","coordinates":[-71.5518,-33.0245]}'),
    ('Valparaíso', 'Valparaíso', 'Valparaíso', '{"type":"Point","coordinates":[-71.6127,-33.0472]}');

-- Fuentes de datos
INSERT INTO fuente_datos (nombre_fuente, tipo_fuente, enlace_url, fecha_descarga) VALUES
    ('IDE Chile', 'Geoportal', 'https://www.ide.cl', '2026-10-01'),
    ('CONAF', 'Históricos', 'https://www.conaf.cl', '2026-10-01'),
    ('Open-Meteo', 'Meteorológico', 'https://open-meteo.com', '2026-10-13'),
    ('Mandante Adevcom', 'Dataset privado', 'contacto@adevcom.cl', '2026-09-15');

-- Variables ambientales de ejemplo
INSERT INTO variable_ambiental (id_zona, temperatura, humedad, velocidad_viento, cobertura_vegetal) VALUES
    (1, 32.5, 25.0, 28.0, 'alta'),
    (2, 24.0, 55.0, 12.0, 'media'),
    (3, 18.0, 70.0, 6.0, 'baja');

-- Historial de incendios (ejemplo)
INSERT INTO historial_incendio (id_zona, fecha_evento, fuente, superficie_afectada) VALUES
    (1, '2024-02-15', 'CONAF', 120.5),
    (1, '2023-01-20', 'CONAF', 85.0),
    (2, '2024-03-10', 'CONAF', 45.2),
    (3, '2022-12-05', 'CONAF', 15.0);

-- Resultados iniciales (para que el frontend tenga datos desde el inicio)
INSERT INTO resultado_riesgo (id_zona, zona, nivel_riesgo, puntaje, puntaje_maximo) VALUES
    (1, 'quilpue', 'Alto', 15, 15),
    (2, 'vina_del_mar', 'Medio', 10, 15),
    (3, 'valparaiso', 'Bajo', 5, 15);