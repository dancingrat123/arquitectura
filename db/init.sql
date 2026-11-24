-- ============================================================
-- SIRIF - Bootstrap de PostgreSQL/PostGIS
--
-- Única fuente de verdad del esquema: la lista `DDL` de
-- `data-service/db.py::ensure_schema`, que se aplica de forma
-- idempotente al arrancar el servicio. Aquí solo se habilita PostGIS.
--
-- Los seeds de `comuna_zona` y `fuente_datos` también los aplica
-- `data-service/ingestion.py::bootstrap()` (idempotente), porque este
-- script corre antes de que existan las tablas. No se siembran
-- resultados de riesgo: la primera inferencia real los rellena.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS postgis;
