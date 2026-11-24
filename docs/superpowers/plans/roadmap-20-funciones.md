# Roadmap 20 Funcionalidades — SIRIF (Implementation Plan)

> **Para agentes:** REQUIRED SUB-SKILL: usar superpowers:subagent-driven-development (recomendado) o superpowers:executing-plans para implementar fase por fase. Los pasos usan checkbox (`- [ ]`) para tracking.

**Goal:** Evolucionar SIRIF desde el mapa de riesgo meteorológico actual hacia una plataforma operativa completa (panel ejecutivo, explicabilidad, simulación, alertas, gestión territorial, reportes y asistente) sin romper lo ya construido en `feature/ingesta-real`.

**Architecture:** Cuatro fases incrementales sobre los 5 microservicios existentes (`api-service`, `data-service`, `risk-service`, `monitoring-service`, `notification-service`). Cada fase añade tablas idempotentes a PostgreSQL/PostGIS, eventos a RabbitMQ (exchange `sirif_eventos`, tipo `topic`) y endpoints JSON aditivos (nunca se eliminan campos existentes). El modelo `rf-clima-region-v2` (RandomForestClassifier calibrado) **no se reemplaza**: toda funcionalidad nueva lee de sus salidas (`probabilidad`, `nivel_riesgo`, `factores_considerados`, `rango_features`, `monotonic_cst`) o aplica reglas deterministas sobre sus features.

**Tech Stack:** Python/FastAPI, PostgreSQL 16 + PostGIS 3.4, RabbitMQ (topic exchange), pika, httpx, reportlab (PDF), leaflet (frontend), pytest + Playwright (CI).

**Spec:** `docs/superpowers/specs/2026-11-18-reentrenamiento-honesto.md` (contrato del modelo, umbrales, advertencias).
**Backlog:** `docs/superpowers/plans/refactorizacion-pendiente.md` (S1–S10, A1–A8, T1–T6).

## Global Constraints

- **Cero llamadas externas síncronas durante requests de usuario.** Prohibido que, cuando un usuario abre el mapa o hace clic en una comuna, `api-service` o `data-service` llamen en ese instante a un servicio externo (Open-Meteo, satélite, LLM de pago) y esperen la respuesta en tiempo real. **Open-Meteo NO está prohibido ni bloqueado**: sigue operando en ingesta batch desde `data-service`/`monitoring-service` (bucle asíncrono), nunca dentro de un request de usuario. Llamadas *internas* entre microservicios (red Docker de confianza) sí están permitidas.
- **`RandomForestClassifier` obligatorio.** El artefacto `risk-service/modelo_riesgo.pkl` sigue siendo el `CalibratedClassifierCV(RandomForestClassifier(...))` del spec. Ninguna fase lo sustituye; explicación, simulación, recomendaciones y asistente derivan de sus salidas o de reglas deterministas sobre sus features.
- **UI y entregables en español** con el término exacto **"riesgo meteorológico"** (nunca "predicción de incendios", nunca "Puntaje: 15/15").
- **Advertencia de ventana estacional** obligatoria: cuando `fuera_ventana_estacional == true`, todo resultado visible (lista, popup, PDF, export, respuesta del asistente) debe mostrar "Fuera de la ventana estacional de entrenamiento (jul–sep 2026)".
- **Advertencia fija** en toda salida de riesgo: `"Estimación de riesgo meteorológico regional; no es pronóstico de ocurrencia."`
- **Umbrales** del modelo: `ALTO >= 0.20`, `MEDIO >= 0.08`, `BAJO < 0.08`; `puntaje = round(probabilidad * 15)` (máx 15). No se redefinen por fase.
- **Compatibilidad aditiva:** todos los campos nuevos se añaden; ninguno de los contratos actuales (spec §8, `GET /api/riesgo/todas`, `GET /datos/{zona}`, `riesgo.calculado`, `monitoreo.zona`) se rompe ni se renombra.
- **Esquema idempotente:** solo `CREATE TABLE IF NOT EXISTS` / `ALTER ... ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`, aplicados en el arranque por `data-service/db.py::ensure_schema`.

## Review Focus

1. **Simulación contamina el historial real** — si un escenario escribe en `resultado_riesgo` o publica `riesgo.calculado`, ensucia alertas, evolución y bitácora. La simulación escribe SOLO en la tabla `simulacion` y no publica eventos de riesgo.
2. **Zona con historial de una sola fila** — evolución/priorización/variación deben devolver serie de 1 elemento y `variacion` nula, no un error 500.
3. **`region` desconocida o sin features** — `risk-service` ya degrada (`region_conocida=false`, features neutros); explicación y recomendaciones deben emitir `explicacion`/`recomendaciones` neutras en ese caso, no lanzar excepción por clave ausente.
4. **Usuario con rol insuficiente** — `visualizador` que intenta crear acción/observación/usuario debe recibir 403 (no 500 ni acceso silencioso); endpoints de escritura validan rol en el mismo `Depends`.
5. **Asistente con pregunta fuera de dominio o zona inexistente** — debe responder en español con los datos reales disponibles o declarar "no tengo datos para esa zona", nunca alucinar un nivel/factor ni llamar a API externa síncronamente.

---

## Resumen de fases y mapeo de funcionalidades

| Fase | Dependencia externa | Funcionalidades |
|---|---|---|
| **1 — Núcleo analítico y persistencia** | Ninguna nueva (Postgres + risk-service local) | 9 Historial · 7 Evolución temporal · 3 Explicación · 1 Panel ejecutivo · 8 Comparador |
| **2 — Simulación, recomendaciones, alertas y estado** | Ninguna nueva (lógica determinista + RabbitMQ) | 4 Simulador · 5 Recomendaciones · 6 Alertas · 18 Centro de notificaciones · 19 Estado de servicios |
| **3 — Gestión operativa y territorial** | PostGIS espacial + roles (ingesta batch de NDVI) | 16 Roles y usuarios · 12 Infraestructura crítica · 2 Priorización · 10 Incidentes · 11 Acciones · 13 Capas vegetación/NDVI · 17 Bitácora |
| **4 — Salida, exportación y asistente** | Bibliotecas PDF/CSV + LLM opcional (fallback local obligatorio) | 14 Reporte PDF · 15 Exportación CSV/PDF · 20 Asistente IA |

---

# Fase 1 — Núcleo analítico y persistencia

**Funcionalidades:** 9 Historial de evaluaciones · 7 Evolución temporal · 3 Explicación del riesgo · 1 Panel ejecutivo · 8 Comparador de zonas.

**Objetivo:** convertir `resultado_riesgo` en un historial completo (snapshot de variables/features/factores/usuario) y exponer lecturas analíticas (panel, evolución, explicación, comparador). Todo es lectura/agregación sobre Postgres + `risk-service`; **no se agregan dependencias externas**.

## Integración con `refactorizacion-pendiente.md`

- **A1 (schema única):** `db/init.sql` pasa a contener **solo** `CREATE EXTENSION postgis` + seeds de `comuna_zona` y `fuente_datos`. El esquema evolutivo (incluidas `probabilidad` y `fuera_ventana_estacional`, hoy solo en `db.py`) se consolida en la lista `DDL` de `data-service/db.py`. Adoptar aquí Alembic queda fuera de alcance (evitar refactor grande); el patrón idempotente existente es la única fuente de verdad.
- **A3 (seed obsoleto):** eliminar el `INSERT INTO resultado_riesgo ... version_modelo='seed'` de `db/init.sql`; la primera inferencia real lo rellena.
- **A6 (lógica duplicada):** crear `shared/riesgo.py` con `FEATURES_CLIMA`, `ALIAS_REGIONES`, `DIRECCIONES_MONOTONAS` (mapeo feature→`+1`/`-1`/`0` leído de `monotonic_cst`), `UMBRAL_ALTO`, `UMBRAL_MEDIO`. Lo importan `risk-service` y `ml-pipeline` (y `api-service` para explicación). Se monta como paquete compartido en los Dockerfiles correspondientes.
- **A5 (pool):** `api-service` adopta `psycopg2.pool.ThreadedConnectionPool` + `try/finally` en `inferir_y_guardar` y en todos los endpoints nuevos.
- **T1 (CI):** desde esta fase, `.github/workflows/ci.yml` ejecuta las 4 suites `pytest` (`ml-pipeline`, `data-service`, `risk-service`, `api-service`).

## Migraciones idempotentes (añadir a `data-service/db.py::DDL`)

```sql
ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS probabilidad FLOAT;
ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS fuera_ventana_estacional BOOLEAN DEFAULT FALSE;
ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS features JSONB;           -- snapshot 9 features
ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS variables JSONB;          -- {temperatura, humedad, viento, precipitacion, vegetacion, incendios_historicos}
ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS factores_predominantes JSONB; -- array de factores (ver contrato)
ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS origen VARCHAR(20) DEFAULT 'automatico';  -- 'automatico'|'on-demand'|'manual'
ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS usuario VARCHAR(50);      -- NULL si es automático
CREATE INDEX IF NOT EXISTS idx_resultado_zona_fecha ON resultado_riesgo (zona, fecha_evaluacion DESC);
```

## Eventos RabbitMQ

- `riesgo.calculado` (routing key existente) — **se enriquece sin romper consumidores**: se añaden `usuario`, `origen`, `factores_predominantes`, `explicacion`. Los campos actuales (`zona`, `nivel_riesgo`, `puntaje`, `probabilidad`, `version_modelo`, `fuente_datos`, `timestamp`) se mantienen.

## Contratos JSON por microservicio

### `risk-service` — `POST /clasificar` (aditivo)

La respuesta conserva los 17 campos del spec §8 y **añade** dos:

```json
{
  "...campos existentes...": "...",
  "factores_predominantes": [
    {"variable": "TEMP_MAX", "etiqueta": "Temperatura máxima", "valor": 32.4, "unidad": "°C", "direccion": "aumenta", "peso": 0.87},
    {"variable": "HUMEDAD_REL", "etiqueta": "Humedad relativa", "valor": 25.0, "unidad": "%", "direccion": "reduce", "peso": 0.71}
  ],
  "explicacion": "El nivel ALTO se explica principalmente por temperatura máxima elevada (32.4 °C) y humedad relativa baja (25.0 %)."
}
```

Regla determinista (local, sin librerías externas): para cada una de las 9 `FEATURES_CLIMA` presente en `rango_features`, calcular posición `t = (valor - min)/(max - min)` recortada a `[0,1]`; si la dirección monótona del feature es `+1` → `riesgo_dir = t`, si `-1` → `riesgo_dir = 1 - t`, si `0` → excluir. `direccion = "aumenta"` si `riesgo_dir >= 0.5`, si no `"reduce"`; `peso = round(riesgo_dir, 2)`. Devolver los 5 de mayor `|riesgo_dir - 0.5|`. `explicacion` se compone con los 2 primeros "aumenta" (plantilla fija en español). Si no hay `rango_features` (modelo legacy), devolver `factores_predominantes: []` y `explicacion: "Explicación no disponible para esta versión del modelo."`.

### `data-service` — `GET /datos/{zona}/historico?desde=&hasta=&limite=100`

```json
{
  "zona": "quilpue", "total": 42,
  "serie": [
    {"fecha_medicion": "2026-11-18T09:00:00Z", "temperatura": 32.5, "humedad": 25.0,
     "velocidad_viento": 28.0, "precipitacion": 0.0, "cobertura_vegetal": "alta",
     "fuente": "Open-Meteo", "calidad": "ok", "features": {"TEMP_MED": 30.1}}
  ]
}
```

Lee `variable_ambiental` ordenada por `fecha_medicion DESC`, opcionalmente filtrada por `desde`/`hasta` (ISO-8601). Con una sola fila devuelve `total: 1`.

### `api-service` — endpoints nuevos

`GET /api/riesgo/{zona}/historial?desde=&hasta=`

```json
{
  "zona": "quilpue", "total": 12,
  "serie": [
    {"fecha_evaluacion": "2026-11-18T10:00:00Z", "nivel_riesgo": "ALTO", "probabilidad": 0.34,
     "puntaje": 5, "version_modelo": "rf-clima-region-v2", "usuario": null,
     "origen": "automatico", "factores_predominantes": []}
  ]
}
```

`GET /api/riesgo/{zona}/explicacion`

```json
{
  "zona": "quilpue", "nivel_riesgo": "ALTO", "probabilidad": 0.34,
  "explicacion": "El nivel ALTO se explica principalmente por ...",
  "factores_predominantes": [],
  "variables": {"temperatura": 32.5, "humedad": 25.0, "viento": 28.0, "precipitacion": 0.0, "vegetacion": "alta", "incendios_historicos": 12},
  "advertencia": "Estimación de riesgo meteorológico regional; no es pronóstico de ocurrencia.",
  "fuera_ventana_estacional": true
}
```

`GET /api/panel/resumen`

```json
{
  "fecha": "2026-11-18T10:00:00Z",
  "resumen_niveles": {"ALTO": 1, "MEDIO": 1, "BAJO": 1},
  "zonas_criticas": [{"zona": "quilpue", "nivel_riesgo": "ALTO", "probabilidad": 0.34}],
  "variacion_reciente": [{"zona": "quilpue", "anterior": "MEDIO", "actual": "ALTO", "delta_probabilidad": 0.12}],
  "alertas_activas": 0,
  "variables_predominantes": [{"variable": "TEMP_MAX", "ocurrencias": 2, "zonas": ["quilpue"]}],
  "total_zonas": 3
}
```

`alertas_activas` devuelve `0` en esta fase (la tabla `alerta` llega en Fase 2); se deja el campo para no romper el contrato del panel.

`GET /api/comparar?zonas=quilpue,valparaiso`

```json
{
  "zonas": ["quilpue", "valparaiso"],
  "comparacion": [
    {"zona": "quilpue", "comuna": "Quilpué", "nivel_riesgo": "ALTO", "probabilidad": 0.34, "puntaje": 5,
     "variables": {"temperatura": 32.5, "humedad": 25.0, "viento": 28.0, "vegetacion": "alta", "incendios_historicos": 12},
     "factores_predominantes": [], "ultima_evaluacion": "2026-11-18T10:00:00Z", "prioridad": null}
  ]
}
```

`prioridad` es `null` hasta Fase 3. Máximo 3 zonas; si `zonas` trae más o una zona inexistente, responde 422 con `{"detail": "máximo 3 zonas"}` / `{"detail": "zona 'x' no encontrada"}`.

`GET /api/riesgo/todas` y `GET /api/riesgo/{zona}` — `serializar_zona` y la respuesta añaden `factores_predominantes` y `origen` (aditivo).

### `monitoring-service` / `notification-service`

Sin cambios en Fase 1.

## Verificación y pruebas obligatorias

**Unitarias (`pytest`):**
- `risk-service/test_main.py::test_factores_predominantes_monotono` — con features `{TEMP_MAX: 40, HUMEDAD_REL: 15, ...}` y `rango_features` conocido, el factor `TEMP_MAX` tiene `direccion="aumenta"` y `HUMEDAD_REL` `direccion="reduce"`.
- `risk-service/test_main.py::test_explicacion_sin_rango` — bundle sin `rango_features` → `factores_predominantes == []` y `explicacion` contiene "no disponible".
- `api-service/test_main.py::test_historial_una_fila` — serie de 1 elemento y `variacion_reciente == []` (no 500).
- `api-service/test_main.py::test_comparar_max_tres` — 4 zonas → 422.
- `api-service/test_main.py::test_panel_resumen_forma` — claves `resumen_niveles`, `zonas_criticas`, `variables_predominantes` presentes.
- `data-service/tests/test_main.py::test_historico_serie` — `total` y `serie` coherentes con filas sembradas.
- `shared/test_riesgo.py::test_direcciones_monotonic` — `DIRECCIONES_MONOTONAS["TEMP_MAX"] == "+1"`, `["HUMEDAD_REL"] == "-1"`, `["VEL_VIENTO"] == "0"`.

**Integración (docker compose):**
1. `docker compose up -d --build` y esperar `healthy`.
2. `curl -sk -H "Authorization: Bearer $token" https://localhost:9443/api/panel/resumen` → `resumen_niveles` con 3 zonas.
3. `curl -sk -H "Authorization: Bearer $token" https://localhost:9443/api/riesgo/quilpue/historial` → `total >= 1`.
4. Playwright: panel ejecutivo visible, popup con "Riesgo meteorológico" y, en noviembre, la advertencia "Fuera de la ventana estacional".

**Criterio de salida:** los 4 suites `pytest` pasan en CI (T1) y el smoke test de integración muestra `factores_predominantes` poblados en el popup.

---

# Fase 2 — Simulación, recomendaciones, alertas y estado

**Funcionalidades:** 4 Simulador · 5 Recomendaciones · 6 Alertas inteligentes · 18 Centro de notificaciones · 19 Estado de servicios.

**Objetivo:** añadir lógica determinista local (sin nuevas dependencias externas) + eventos RabbitMQ que convierten a `notification-service` en un servicio real.

## Integración con `refactorizacion-pendiente.md`

- **A4 (notification-service es stub):** deja de imprimir y pasa a persistir en la tabla `notificacion` (necesita `DATABASE_URL` en su `environment` y acceso a Postgres).
- **A8 (sin healthcheck):** `monitoring-service` publica heartbeat en la tabla `estado_servicio` (cada ciclo, junto a la ingesta); `notification-service` hace lo mismo al consumir. Esto da visibilidad sin exponer endpoints HTTP nuevos.
- **S2 (secretos) — alcance acotado:** `monitoring-service` y `notification-service` leen `DATABASE_URL`/`RABBITMQ_*` solo del entorno (sin defaults); fallan al arrancar si faltan. El resto de S2 (quitar `FAKE_USERS_DB`, rotar TLS) se completa en Fase 3.

## Migraciones idempotentes

```sql
CREATE TABLE IF NOT EXISTS simulacion (
    id_simulacion SERIAL PRIMARY KEY,
    id_zona INT REFERENCES comuna_zona(id_zona),
    zona VARCHAR(100) NOT NULL,
    usuario VARCHAR(50),
    escenario JSONB NOT NULL,            -- {"temperatura": 40.0, "humedad": 15.0, ...}
    variables_base JSONB,                -- variables reales previas
    features_base JSONB,                 -- 9 features reales previas
    probabilidad_original FLOAT,
    nivel_original VARCHAR(10),
    probabilidad_simulada FLOAT,
    nivel_simulado VARCHAR(10),
    delta_probabilidad FLOAT,
    factores_predominantes JSONB,
    fuera_ventana_estacional BOOLEAN,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alerta (
    id_alerta SERIAL PRIMARY KEY,
    id_zona INT REFERENCES comuna_zona(id_zona),
    zona VARCHAR(100) NOT NULL,
    tipo VARCHAR(30) NOT NULL,           -- 'cambio_nivel' | 'umbral'
    nivel_anterior VARCHAR(10),
    nivel_nuevo VARCHAR(10),
    probabilidad FLOAT,
    umbral FLOAT,
    mensaje TEXT NOT NULL,
    estado VARCHAR(20) DEFAULT 'activa', -- 'activa' | 'reconocida' | 'resuelta'
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS notificacion (
    id_notificacion SERIAL PRIMARY KEY,
    tipo VARCHAR(30) NOT NULL,           -- 'alerta' | 'cambio_riesgo' | 'evento' | 'estado_servicio'
    titulo VARCHAR(200) NOT NULL,
    mensaje TEXT NOT NULL,
    zona VARCHAR(100),
    nivel VARCHAR(10),
    metadata JSONB,
    leida BOOLEAN DEFAULT FALSE,
    usuario VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS estado_servicio (
    servicio VARCHAR(50) PRIMARY KEY,
    estado VARCHAR(20) NOT NULL,         -- 'ok' | 'degradado' | 'caido'
    detalle TEXT,
    ultima_comprobacion TIMESTAMP,
    latencia_ms INT
);
```

## Eventos RabbitMQ

- `alerta.generada` (nuevo) — publicado por `api-service` al detectar cambio de nivel o superación de umbral.
- `notificacion.nueva` (nuevo) — publicado por `notification-service` tras persistir una notificación.
- `riesgo.calculado` (existente) — sin cambios; **la simulación NO publica** `riesgo.calculado` ni escribe `resultado_riesgo`.

Contratos de eventos (cuerpo JSON, `delivery_mode=2`):

```json
// alerta.generada
{"id_alerta": 7, "zona": "quilpue", "tipo": "cambio_nivel", "nivel_anterior": "MEDIO",
 "nivel_nuevo": "ALTO", "probabilidad": 0.34, "mensaje": "Quilpué cambió de MEDIO a ALTO.",
 "timestamp": "2026-11-18T10:00:00"}

// notificacion.nueva
{"id_notificacion": 1, "tipo": "alerta", "titulo": "Cambio de nivel en Quilpué",
 "mensaje": "...", "zona": "quilpue", "nivel": "ALTO", "timestamp": "2026-11-18T10:00:00"}
```

`notification-service` vincula su cola a los routing keys `riesgo.calculado`, `alerta.generada`, `notificacion.nueva`.

## Contratos JSON por microservicio

### `api-service`

`POST /api/simular` (body `{"zona": "quilpue", "ajustes": {"temperatura": 40.0, "humedad": 15.0, "viento": 50.0}}`)

```json
{
  "zona": "quilpue",
  "escenario": {"temperatura": 40.0, "humedad": 15.0, "viento": 50.0},
  "original": {"nivel_riesgo": "ALTO", "probabilidad": 0.34, "puntaje": 5},
  "simulado": {"nivel_riesgo": "ALTO", "probabilidad": 0.56, "puntaje": 8},
  "delta_probabilidad": 0.22,
  "factores_predominantes": [],
  "advertencia": "Simulación sobre el modelo regional; no modifica el historial real.",
  "fuera_ventana_estacional": true
}
```

Regla de recálculo determinista de features (desde `features_base` persistidas): sobrescribir `TEMP_MED=TEMP_MAX=temperatura`, `HUMEDAD_REL=humedad`, `VEL_VIENTO=viento`, `PRECIPITACION=precipitacion`; `temp_media_7d += (temperatura - TEMP_MED_base)/7`; `humedad_7d += (humedad - HUMEDAD_REL_base)/7`; `precip_7d += (precipitacion - PRECIPITACION_base)`; `precip_30d += (precipitacion - PRECIPITACION_base)`. Persiste en `simulacion` y **no** publica eventos.

`GET /api/riesgo/{zona}/recomendaciones`

```json
{
  "zona": "quilpue", "nivel_riesgo": "ALTO",
  "recomendaciones": [
    {"codigo": "ALTO_VIENTO", "categoria": "prevencion", "prioridad": 1,
     "texto": "Restringir uso de fuego y maquinaria en zonas expuestas al viento."},
    {"codigo": "ALTO_VEGETACION", "categoria": "mitigacion", "prioridad": 2,
     "texto": "Programar limpieza de vegetación seca en quebradas y bordes de camino."}
  ]
}
```

Reglas deterministas (mínimas; `prioridad` 1 = mayor): `ALTO + factor viento` → `ALTO_VIENTO`; `ALTO + factor temperatura` → `ALTO_TEMP`; `ALTO + vegetacion alta` → `ALTO_VEGETACION`; `MEDIO + viento/humedad` → `MEDIO_VIGILANCIA`; `BAJO` → `BAJO_MANTENCION`. Texto fijo en español.

`GET /api/alertas?estado=activa`, `PATCH /api/alertas/{id}` (cambiar `estado`).

```json
{"total": 2, "alertas": [
  {"id_alerta": 7, "zona": "quilpue", "tipo": "cambio_nivel", "nivel_anterior": "MEDIO",
   "nivel_nuevo": "ALTO", "probabilidad": 0.34, "mensaje": "...", "estado": "activa", "created_at": "..."}
]}
```

Detección (tras `inferir_y_guardar`): comparar `nivel_riesgo` nuevo con el `nivel_riesgo` de la fila previa de `resultado_riesgo` para la zona → si difiere, `cambio_nivel`; si `probabilidad >= UMBRAL_ALERTA` (env, default `0.50`) y la previa `< UMBRAL_ALERTA`, `umbral`. Inserta en `alerta`, publica `alerta.generada`.

`GET /api/notificaciones`, `POST /api/notificaciones/{id}/leida`.

```json
{"total": 5, "no_leidas": 2, "notificaciones": [
  {"id_notificacion": 1, "tipo": "alerta", "titulo": "Cambio de nivel en Quilpué",
   "mensaje": "...", "zona": "quilpue", "nivel": "ALTO", "leida": false, "created_at": "..."}
]}
```

`GET /api/estado/servicios` — lee SOLO la tabla `estado_servicio` (sin llamadas síncronas durante el request).

```json
{"servicios": [
  {"servicio": "api-service", "estado": "ok", "latencia_ms": 12, "ultima_comprobacion": "..."},
  {"servicio": "risk-service", "estado": "ok", "modelo_cargado": true, "version_modelo": "rf-clima-region-v2", "ultima_comprobacion": "..."},
  {"servicio": "open-meteo", "estado": "ok", "ultimo_exito": "...", "ultima_comprobacion": "..."},
  {"servicio": "postgres", "estado": "ok", "ultima_comprobacion": "..."},
  {"servicio": "rabbitmq", "estado": "ok", "ultima_comprobacion": "..."}
]}
```

### `data-service`

Sin endpoints nuevos en Fase 2.

### `risk-service`

Sin cambios en Fase 2 (la simulación reutiliza `POST /clasificar`).

### `monitoring-service`

Añade al ciclo (además de publicar `monitoreo.zona`): probar `GET /health` de `api-service`, `GET /` de `data-service` y `risk-service`, y escribir `estado_servicio` (estado `ok`/`caido`, `latencia_ms`, `ultima_comprobacion`). Para `open-meteo`, reutiliza el `estado`/`ultimo_exito` de `ingesta_control`.

### `notification-service`

Consume `riesgo.calculado`, `alerta.generada`, `notificacion.nueva`; inserta en `notificacion`; publica `notificacion.nueva` con el id generado. Usa conexión Postgres con reintentos idéntica a la de RabbitMQ.

## Verificación y pruebas obligatorias

**Unitarias:**
- `api-service/test_main.py::test_simulacion_no_escribe_resultado` — tras `POST /api/simular`, el conteo de `resultado_riesgo` no cambia y no se publica `riesgo.calculado` (mock de `publicar_evento`).
- `api-service/test_main.py::test_simulacion_recalcula_features` — `features` simuladas respetan la regla de rodadura (assert sobre `temp_media_7d`).
- `api-service/test_main.py::test_recomendaciones_alto_viento` — con factor `VEL_VIENTO` "aumenta" → `ALTO_VIENTO` con `prioridad 1`.
- `api-service/test_main.py::test_deteccion_cambio_nivel` — previo `MEDIO`, nuevo `ALTO` → inserta `alerta` tipo `cambio_nivel`.
- `api-service/test_main.py::test_deteccion_umbral` — `probabilidad 0.6` con previa `0.3` → `alerta` tipo `umbral`.
- `notification-service/test_main.py::test_persiste_notificacion` — evento `alerta.generada` → fila en `notificacion`.
- `monitoring-service/test_main.py::test_heartbeat_estado` — escribe `estado_servicio` con `estado` coherente.

**Integración:**
1. `docker compose up -d --build`; `notification-service` y `monitoring-service` ahora tienen `DATABASE_URL`.
2. `docker compose exec -T db psql -U sirif -d sirif -c "SELECT COUNT(*) FROM notificacion;"` → crece tras un ciclo de monitoreo.
3. Forzar cambio: `DELETE FROM resultado_riesgo;` + esperar ciclo → `alerta` con `cambio_nivel` y notificación asociada.
4. `curl -sk -H "Authorization: Bearer $token" https://localhost:9443/api/estado/servicios` → 5+ servicios con `estado`.

**Criterio de salida:** simulación aislada del historial (verificada), alertas detectadas end-to-end y centro de notificaciones poblado vía RabbitMQ.

---

# Fase 3 — Gestión operativa y territorial

**Funcionalidades:** 16 Roles y usuarios · 12 Infraestructura crítica · 2 Priorización · 10 Incidentes preventivos · 11 Seguimiento de acciones · 13 Capas vegetación/NDVI · 17 Bitácora.

**Objetivo:** introducir el modelo de roles (reemplaza `FAKE_USERS_DB`), las entidades espaciales (PostGIS) y la trazabilidad de decisiones. El NDVI entra por ingesta batch (async), nunca por request.

## Integración con `refactorizacion-pendiente.md`

- **S2 (secretos/credenciales):** reemplazar `FAKE_USERS_DB`/`HASH_SIRIF_2026` por la tabla `usuario` con `hash_password` (PBKDF2-SHA256 vía `passlib`). La contraseña admin inicial se inyecta por env `ADMIN_PASSWORD_HASH` (fail-fast si falta). Quitar `value="sirif2026"` del formulario de login (frontend).
- **S3 (INTERNAL_TOKEN opcional):** `data-service` exige `X-Internal-Token` siempre (401 si falta); fail-fast al arrancar sin `INTERNAL_TOKEN`.
- **S6 (auth `/api/datos`):** el gateway y `api-service` enrutan los endpoints de datos espaciales a través de la API con JWT+rol; `POST /datos/sincronizar` sigue bloqueado en el gateway (solo `monitoring-service`).
- **A8 (healthcheck):** se mantiene el heartbeat de Fase 2 para los nuevos servicios/datos.

## Migraciones idempotentes

```sql
CREATE TABLE IF NOT EXISTS usuario (
    id_usuario SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    nombre_completo VARCHAR(100),
    rol VARCHAR(20) NOT NULL,            -- 'admin' | 'analista' | 'visualizador'
    hash_password VARCHAR(255) NOT NULL,
    activo BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS infraestructura_critica (
    id_infra SERIAL PRIMARY KEY,
    tipo VARCHAR(30) NOT NULL,           -- 'colegio' | 'hospital' | 'vivienda' | 'ruta' | 'hidrante' | 'otro'
    nombre VARCHAR(150),
    descripcion TEXT,
    id_zona INT REFERENCES comuna_zona(id_zona),
    direccion VARCHAR(200),
    latitud FLOAT, longitud FLOAT,
    geom GEOMETRY(Point, 4326),
    expuestos INT,
    fuente VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_infra_geom ON infraestructura_critica USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_infra_zona ON infraestructura_critica (id_zona);

CREATE TABLE IF NOT EXISTS observacion_terreno (
    id_observacion SERIAL PRIMARY KEY,
    id_zona INT REFERENCES comuna_zona(id_zona),
    zona VARCHAR(100) NOT NULL,
    tipo VARCHAR(30) NOT NULL,           -- 'vegetacion_seca' | 'microbasural' | 'quema' | 'acceso_bloqueado' | 'otro'
    descripcion TEXT NOT NULL,
    severidad VARCHAR(10) DEFAULT 'media', -- 'alta' | 'media' | 'baja'
    estado VARCHAR(20) DEFAULT 'abierta',  -- 'abierta' | 'en_revision' | 'cerrada'
    usuario VARCHAR(50),
    latitud FLOAT, longitud FLOAT,
    geom GEOMETRY(Point, 4326),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_obs_geom ON observacion_terreno USING GIST (geom);

CREATE TABLE IF NOT EXISTS accion_preventiva (
    id_accion SERIAL PRIMARY KEY,
    id_zona INT REFERENCES comuna_zona(id_zona),
    zona VARCHAR(100) NOT NULL,
    descripcion TEXT NOT NULL,
    tipo VARCHAR(40),                    -- 'limpieza' | 'fiscalizacion' | 'difusion' | 'otro'
    responsable VARCHAR(50),
    estado VARCHAR(20) DEFAULT 'pendiente', -- 'pendiente' | 'en_progreso' | 'completada' | 'cancelada'
    fecha_programada DATE,
    fecha_ejecucion DATE,
    prioridad VARCHAR(10) DEFAULT 'media',
    observacion_relacionada INT REFERENCES observacion_terreno(id_observacion),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS capa_vegetacion (
    id_capa SERIAL PRIMARY KEY,
    id_zona INT REFERENCES comuna_zona(id_zona),
    zona VARCHAR(100) NOT NULL,
    indice VARCHAR(20) NOT NULL,         -- 'NDVI' | 'combustible'
    valor FLOAT,
    categoria VARCHAR(20),               -- 'seco' | 'moderado' | 'verde'
    fecha_captura DATE,
    fuente VARCHAR(100),
    geom GEOMETRY(Polygon, 4326),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_capa_geom ON capa_vegetacion USING GIST (geom);

CREATE TABLE IF NOT EXISTS bitacora_decision (
    id_bitacora SERIAL PRIMARY KEY,
    usuario VARCHAR(50),
    id_zona INT REFERENCES comuna_zona(id_zona),
    zona VARCHAR(100),
    tipo_evento VARCHAR(40),             -- 'consulta' | 'observacion' | 'accion' | 'reporte' | 'exportacion'
    detalle TEXT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

Seed de usuarios (idempotente, `ON CONFLICT (username) DO NOTHING`): `admin` (rol `admin`, hash desde `ADMIN_PASSWORD_HASH`); `analista` y `visualizador` (roles homónimos, contraseñas desde env opcionales `ANALISTA_PASSWORD_HASH`/`VISUALIZADOR_PASSWORD_HASH`, solo en entorno de desarrollo).

## Eventos RabbitMQ

- `bitacora.nueva` (nuevo) — publicado por `api-service` al registrar consulta/observación/acción/reporte/exportación. `notification-service` lo consume para alimentar el centro de notificaciones.

```json
{"id_bitacora": 5, "usuario": "admin", "zona": "quilpue", "tipo_evento": "accion",
 "detalle": "Acción preventiva creada", "timestamp": "2026-11-18T10:00:00"}
```

## Contratos JSON por microservicio

### `api-service` — autenticación y roles

`POST /token` ahora devuelve rol:

```json
{"access_token": "...", "token_type": "bearer", "rol": "admin", "nombre_completo": "Administrador SIRIF"}
```

`get_current_user` lee de `usuario`. Nuevo `require_rol(*roles)` como `Depends`. Matriz:
- **admin:** todo, incluido `POST/GET/PATCH/DELETE /api/usuarios`.
- **analista:** riesgo (todas, por zona, historial, explicación, comparar), simulación, recomendaciones, alertas, observaciones, acciones, infraestructura (lectura/escritura), prioridad, bitácora, reportes/export.
- **visualizador:** solo lectura (panel, riesgo, historial, comparador, prioridad, reportes/export, notificaciones). Toda escritura → 403.

`POST /api/usuarios` (body `{"username": "...", "rol": "analista", "password": "..."}`) → 201; `GET /api/usuarios` → `{"total": 3, "usuarios": [{"id_usuario": 1, "username": "admin", "rol": "admin", "activo": true}]}` (nunca expone `hash_password`).

`GET /api/infraestructura?zona=&tipo=`

```json
{"zona": "quilpue", "total": 12,
 "por_tipo": {"colegio": 4, "hospital": 1, "vivienda": 5000, "ruta": 3, "hidrante": 4},
 "elementos": [
   {"id_infra": 1, "tipo": "colegio", "nombre": "Colegio ...", "latitud": -33.04, "longitud": -71.44, "expuestos": 320}
 ]}
```

`GET /api/prioridad`

```json
{"total": 3, "zonas": [
  {"zona": "quilpue", "comuna": "Quilpué", "nivel_riesgo": "ALTO", "probabilidad": 0.34,
   "puntaje_prioridad": 87, "posicion": 1,
   "factores": {"riesgo": 0.34, "viviendas_expuestas": 5000, "colegios": 4, "hospitales": 1,
                "infraestructura_total": 12, "vegetacion": "alta", "poblacion": 150000}}
]}
```

Fórmula determinista (documentada en el endpoint): `puntaje_prioridad = round(100 * (W_RIESGO*prob + W_VIVIENDAS*min(viv/5000,1) + W_INFRA*min(infra/20,1) + W_VEGETACION*veg))`, con `veg = {alta:1, media:0.5, baja:0}`; `viviendas_expuestas = expuestos` de `tipo='vivienda'`; `infra = colegios + hospitales + hidrantes`. Los pesos son **constantes configurables** (defaults `W_RIESGO=0.5`, `W_VIVIENDAS=0.2`, `W_INFRA=0.15`, `W_VEGETACION=0.15`), sobreescribibles por variables de entorno (`PESO_RIESGO`, `PESO_VIVIENDAS`, `PESO_INFRA`, `PESO_VEGETACION`) para ajustarlas sin tocar código si el mandante pide cambios; la fórmula normaliza los pesos por su suma para que `puntaje_prioridad` quede en `[0,100]`. Orden desc por `puntaje_prioridad`.

`POST /api/observaciones` / `GET /api/observaciones?zona=&tipo=&estado=`

```json
{"id_observacion": 1, "zona": "quilpue", "tipo": "vegetacion_seca",
 "descripcion": "...", "severidad": "alta", "estado": "abierta", "usuario": "analista1",
 "latitud": -33.04, "longitud": -71.44, "created_at": "..."}
```

`POST /api/acciones` / `GET /api/acciones?zona=&estado=` / `PATCH /api/acciones/{id}`

```json
{"id_accion": 1, "zona": "quilpue", "descripcion": "...", "tipo": "limpieza",
 "responsable": "analista1", "estado": "pendiente", "fecha_programada": "2026-11-25",
 "prioridad": "alta", "observacion_relacionada": 3}
```

`GET /api/bitacora?zona=&usuario=&tipo_evento=`

```json
{"total": 10, "entradas": [
  {"id_bitacora": 1, "usuario": "admin", "zona": "quilpue", "tipo_evento": "consulta",
   "detalle": "Consultó el riesgo de quilpue", "created_at": "..."}
]}
```

La bitácora se inserta automáticamente en cada consulta (`/api/riesgo/{zona}`, `/api/comparar`, `/api/prioridad`), observación, acción, reporte y exportación.

### `data-service`

`GET /datos/{zona}/infraestructura` — igual shape que `GET /api/infraestructura` (consulta PostGIS por `id_zona`).

`GET /datos/{zona}/vegetacion`

```json
{"zona": "quilpue", "capas": [
  {"indice": "NDVI", "valor": 0.18, "categoria": "seco", "fecha_captura": "2026-11-10", "fuente": "Sentinel-2 (ingesta batch)"}
]}
```

Ingesta de NDVI: bootstrap/`monitoring-service` descarga (o lee un archivo versionado) y persiste en `capa_vegetacion` de forma asíncrona; **nunca** en el request. Si no hay capa, `capas: []` con `"fuente": "no disponible"`.

### `risk-service` / `monitoring-service` / `notification-service`

- `risk-service`: sin cambios.
- `monitoring-service`: añade la ingesta batch de NDVI (opcional, si `NDVI_URL`/archivo versionado configurado).
- `notification-service`: consume `bitacora.nueva` → notificación tipo `evento`.

## Verificación y pruebas obligatorias

**Unitarias:**
- `api-service/test_main.py::test_visualizador_no_crea_accion` — token `visualizador` + `POST /api/acciones` → 403.
- `api-service/test_main.py::test_analista_si_crea_observacion` — token `analista` + `POST /api/observaciones` → 201.
- `api-service/test_main.py::test_token_devuelve_rol` — `rol` y `nombre_completo` en la respuesta.
- `api-service/test_main.py::test_usuarios_no_expone_hash` — `GET /api/usuarios` no incluye `hash_password`.
- `api-service/test_main.py::test_prioridad_orden_desc` — zonas ordenadas por `puntaje_prioridad` desc y `posicion` secuencial.
- `api-service/test_main.py::test_prioridad_formula` — con datos sembrados, `puntaje_prioridad` coincide con la fórmula exacta.
- `api-service/test_main.py::test_bitacora_autoregistro` — consultar `/api/riesgo/quilpue` inserta entrada `tipo_evento='consulta'`.
- `data-service/tests/test_main.py::test_vegetacion_vacia` — sin capas → `capas: []` y fuente `no disponible`.

**Integración:**
1. `docker compose up -d --build` (ahora `ADMIN_PASSWORD_HASH`, `INTERNAL_TOKEN` obligatorios).
2. Login admin → `POST /api/usuarios` crea `analista`; login `analista` → puede crear acción/observación; login `visualizador` → 403 en escritura.
3. `POST /api/observaciones` (tipo `vegetacion_seca`, con lat/long) → aparece en `GET /api/observaciones` y en bitácora.
4. `GET /api/prioridad` → 3 zonas con `puntaje_prioridad` y `posicion`.
5. Playwright: selector de rol en login, capa NDVI como overlay si hay datos, panel de priorización visible.

**Criterio de salida:** RBAC funcional (403 verificado), priorización con fórmula determinista, y entidades espaciales consultables por PostGIS.

---

# Fase 4 — Salida, exportación y asistente

**Funcionalidades:** 14 Reporte automático PDF · 15 Exportación CSV/PDF · 20 Asistente IA explicativo.

**Objetivo:** generar entregables (PDF/CSV) y un asistente de preguntas con **fallback local determinista obligatorio** (el LLM externo es opcional y nunca síncrono durante el request).

## Integración con `refactorizacion-pendiente.md`

- **S9 (pickle sin checksum):** el PDF/reporte incluye `version_modelo` y, al cargar, `risk-service` verifica SHA-256 contra `model_metadata.json` (mitigación de la sección de salida).
- **T5 (dos GeoJSON):** el mapa del PDF se genera desde la **única** fuente `frontend/data/distritos_filtrados.geojson` (se elimina la copia raíz o se documenta como derivada).
- **T4 (utcnow):** los endpoints nuevos usan `datetime.now(timezone.utc)`.
- **S10 (rate limit):** `limit_req` más estricto en `/api/asistente/preguntar` y `/api/exportar/*`.

## Migraciones idempotentes

Sin tablas nuevas obligatorias. Los reportes/exportaciones se registran en `bitacora_decision` (`tipo_evento='reporte'`/`'exportacion'`). Opcional: `reporte` para guardar el blob generado, fuera del alcance mínimo.

## Eventos RabbitMQ

Sin eventos nuevos. Cada reporte/exportación publica `bitacora.nueva` (Fase 3) y opcionalmente `notificacion.nueva`.

## Contratos JSON por microservicio

### `api-service`

`GET /api/reportes/{zona}/pdf` → `Content-Type: application/pdf`. Ficha en español con: zona/comuna, nivel ("Riesgo meteorológico: ALTO · 34 %"), causas (`factores_predominantes`), mapa (render estático desde el GeoJSON local + geometría almacenada, sin tiles externos), recomendaciones, fecha de generación y las dos advertencias obligatorias (regional + ventana estacional). Implementación con `reportlab`.

`GET /api/exportar/csv?zonas=` → `text/csv`. Columnas fijas: `zona,comuna,nivel_riesgo,probabilidad,puntaje,fecha_evaluacion,version_modelo,fuera_ventana_estacional,temperatura,humedad,viento,vegetacion`.

`GET /api/exportar/pdf` → PDF multi-zona con el mismo esquema de la ficha, más la tabla resumen del panel.

`POST /api/asistente/preguntar` — body `{"pregunta": "¿Por qué quilpué tiene riesgo alto?"}`:

```json
{
  "pregunta": "¿Por qué quilpué tiene riesgo alto?",
  "respuesta": "Quilpué presenta riesgo meteorológico ALTO (probabilidad 34 %). Se explica principalmente por temperatura máxima elevada (32.4 °C) y humedad relativa baja (25 %). Estimación para condiciones similares a jul–sep 2026; no es pronóstico de ocurrencia.",
  "fuente": "local-determinista",
  "datos_utilizados": {"zona": "quilpue", "nivel_riesgo": "ALTO", "probabilidad": 0.34, "factores_predominantes": []},
  "advertencia": "Estimación de riesgo meteorológico regional; no es pronóstico de ocurrencia."
}
```

**Flujo del asistente (obligatorio):**
1. Parsear la pregunta localmente: extraer `zona` (por slug/comuna) e intención (`por qué`, `nivel`, `factores`, `comparar`, `historial`, `recomendaciones`). Sin LLM.
2. Responder **siempre** con el fallback local determinista usando `explicacion`, `factores_predominantes`, `historial` y `recomendaciones` reales (templates en español). Zona inexistente → `"No tengo datos para la zona 'x'."`; pregunta fuera de dominio → `"Solo puedo responder sobre riesgo meteorológico, factores, historial y recomendaciones de las zonas del sistema."`
3. **Opcional** (solo si `LLM_URL` y `LLM_API_KEY` están definidos): reformular la respuesta con el LLM **en segundo plano** (task async) y actualizar el registro en bitácora; el request del usuario devuelve de inmediato la respuesta determinista (`fuente: "local-determinista"`). Si el LLM responde más tarde, se guarda en `bitacora_decision.metadata`. **Nunca** se espera al LLM de forma síncrona; **nunca** se usa el LLM como única fuente.

### `data-service` / `risk-service` / `monitoring-service` / `notification-service`

- `risk-service`: verifica checksum SHA-256 del `.pkl` al cargar (S9).
- Los demás: sin cambios de contrato; `notification-service` opcionalmente notifica exportaciones.

## Verificación y pruebas obligatorias

**Unitarias:**
- `api-service/test_main.py::test_asistente_por_que` — pregunta "¿por qué quilpue tiene riesgo alto?" → `fuente == "local-determinista"` y `respuesta` contiene "riesgo meteorológico".
- `api-service/test_main.py::test_asistente_zona_inexistente` — "¿por qué talca tiene riesgo alto?" → respuesta "No tengo datos para la zona 'talca'." (sin excepción).
- `api-service/test_main.py::test_asistente_fuera_dominio` — "¿cuál es el clima en Marte?" → respuesta de fuera de dominio, sin llamar a LLM (mock verifica que no se invoca `httpx` externo).
- `api-service/test_main.py::test_asistente_sin_llm_no_llama_externo` — con `LLM_URL` vacío, no hay llamadas HTTP externas durante el request.
- `api-service/test_main.py::test_export_csv_columnas` — header CSV exacto.
- `api-service/test_main.py::test_reporte_pdf_advertencias` — el texto del PDF incluye "riesgo meteorológico" y "Estimación de riesgo meteorológico regional".

**Integración:**
1. `docker compose up -d --build`.
2. `curl -sk -H "Authorization: Bearer $token" "https://localhost:9443/api/reportes/quilpue/pdf" -o ficha.pdf` → `file ficha.pdf` reporta PDF y contiene las advertencias.
3. `curl -sk -H "Authorization: Bearer $token" "https://localhost:9443/api/exportar/csv"` → CSV con header correcto y 3 filas.
4. `curl -sk -H "Authorization: Bearer $token" -H "Content-Type: application/json" -d '{"pregunta":"¿por qué quilpué tiene riesgo alto?"}' https://localhost:9443/api/asistente/preguntar` → `fuente: local-determinista`.
5. Playwright: botón "Reporte PDF", "Exportar CSV" y caja de preguntas del asistente visibles y funcionales.

**Criterio de salida:** PDF/CSV descargables con advertencias en español y asistente que responde desde datos reales sin llamadas externas síncronas.

---

## Orden de ejecución y dependencias entre fases

1. **Fase 1** es prerrequisito de 2 (alertas usan historial), 3 (prioridad usa infraestructura y la explicación se reutiliza en observaciones) y 4 (PDF/asistente leen explicación e historial).
2. **Fase 2** depende solo de Fase 1 y es independiente de Fase 3.
3. **Fase 3** depende de Fase 1 (explicación/historial) y puede ejecutarse en paralelo con Fase 2.
4. **Fase 4** depende de Fases 1–3 (ficha = riesgo + causas + mapa + recomendaciones + fecha).

Cada fase es una sesión independiente ejecutable con modelos `low`/`medium`; los contratos JSON y los nombres de test de este documento son la interfaz que evita renegociar decisiones entre sesiones.

## Criterios globales de "no romper"

- `docker compose up -d --build` sigue arrancando con el stack completo y 3 zonas.
- `GET /api/riesgo/todas`, `GET /api/riesgo/{zona}`, `GET /datos/{zona}` y `POST /datos/sincronizar` conservan su shape; solo crecen.
- El artefacto `modelo_riesgo.pkl` sigue siendo `RandomForestClassifier` calibrado; `version_modelo` sigue `rf-clima-region-v2`.
- Todo texto visible/entregable dice "riesgo meteorológico" y muestra la advertencia de ventana estacional cuando `fuera_ventana_estacional == true`.
