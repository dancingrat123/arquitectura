# Diseño: ingesta real de datos (batch horario) para SIRIF

Fecha: 2026-11-17
Rama: `feature/ingesta-real`
Estado: implementado y verificado end-to-end

## 1. Objetivo

Reemplazar los datos simulados de SIRIF por un flujo de ingesta real, por lotes y
trazable, sin consultas síncronas a APIs externas durante las peticiones del
usuario.

Al terminar, el sistema debe:

1. Cargar una sola vez las capas territoriales, los históricos de CONAF y la
   climatología base en PostgreSQL + PostGIS, al arrancar `data-service`.
2. Actualizar la meteorología de todas las zonas de la Región de Valparaíso cada
   1 hora desde Open-Meteo, mediante un endpoint interno de `data-service`
   invocado por `monitoring-service`.
3. Publicar `monitoreo.zona` en RabbitMQ tras cada ciclo para recalcular riesgo.
4. Servir desde `data-service` la última medición almacenada junto con su
   trazabilidad (fuente, fecha de medición, fecha de ingesta, calidad).
5. Reflejar en la UI el nivel de riesgo y la fuente de los datos.

## 2. Alcance

### Dentro

- Ingesta dentro de `data-service` (sin microservicios nuevos):
  - Carga inicial idempotente al arrancar: IDE Chile/INE, CONAF y EMAS.
  - `POST /datos/sincronizar`: descarga batch de Open-Meteo y persiste con
    trazabilidad.
- `monitoring-service` como único worker/scheduler: cada `MONITORING_INTERVAL`
  llama a `POST /datos/sincronizar` y publica `monitoreo.zona`.
- PostgreSQL con PostGIS: `comuna_zona`, `historial_incendio`,
  `variable_ambiental`, `distrito_censal`, `fuente_datos`, `trazabilidad`,
  `ingesta_control`.
- Lectura de BD en `data-service` (sin llamadas externas por request).
- Features reales en `risk-service` (mes actual, CONAF, precipitación).
- Metadatos de trazabilidad en la API y en el frontend.
- Migración idempotente aplicada por `data-service` (sirve para volúmenes
  existentes).

### Fuera de alcance

- Reentrenamiento del modelo (`modelo_riesgo.pkl` se mantiene).
- Rediseño del heatmap por distrito más allá de tomar el riesgo comunal vigente.
- Autenticación, TLS o cambios de gateway no relacionados.
- Descarga de nuevas fuentes externas distintas de Open-Meteo.

## 3. Arquitectura

```
                        (cada MONITORING_INTERVAL)
  monitoring-service ───────────────┐
        │                           │ POST /datos/sincronizar
        │                           ▼
        │                    data-service ──────► Open-Meteo (batch, 1 req)
        │                     │      │
        │ publica             │      └──────────► PostgreSQL + PostGIS
        ▼                     │ lee                      ▲
   RabbitMQ ──► api-service ──┘                          │
  monitoreo.zona     │                                  │
                     ▼                                  │
                risk-service                            │
                     │                                  │
                     └──────────► frontend ◄────────────┘
```

- La ingesta inicial corre en un hilo de arranque de `data-service`; la API
  responde desde el primer momento (con *fallback* declarado si aún no hay BD).
- `monitoring-service` no conoce la lógica de ingesta: solo programa, invoca y
  publica.
- `POST /datos/sincronizar` exige `X-Internal-Token` y el gateway lo bloquea
  externamente.

## 4. Flujo

### 4.1 Arranque de `data-service`

1. Espera a PostgreSQL con reintentos (no bloquea el arranque de la API).
2. Aplica DDL idempotente: `CREATE EXTENSION IF NOT EXISTS postgis`, tablas,
   columnas nuevas, índices.
3. Registra/actualiza las fuentes en `fuente_datos`.
4. Carga inicial única (idempotente, controlada por `ingesta_control`):
   - `ide_chile`: inserta/actualiza `distrito_censal` desde el GeoJSON,
     reproyectando con PostGIS a EPSG:4326 y guardando `codigo_distrito`,
     `codigo_comuna`, `nombre_comuna`, población, superficie y `geom`.
   - `zonas`: asegura las zonas (`comuna_zona`) con `slug`, `codigo_comuna`,
     `latitud`, `longitud` y `geom`. Se mantienen las coordenadas urbanas
     validadas por zona; los polígonos censales se guardan aparte en
     `distrito_censal`.
   - `conaf`: carga las filas de la Región de Valparaíso (90 registros diarios,
     jul–sep 2026) en `historial_incendio` con `region`, `nro_incendios`,
     `nro_incendios_ant`, `superficie_afectada`, `superficie_afectada_ant` y
     `periodo`.
   - `emas`: elige la estación EMAS más cercana con lecturas para cada zona y
     guarda una fila base en `variable_ambiental`
     (`fuente='EMAS (clima.csv)'`).
5. No aborta si faltan los CSV: registra `detalle` en `ingesta_control` y sigue
   operando; Open-Meteo cubre la meteorología.

### 4.2 Ciclo programado

`monitoring-service`:

1. `POST {DATA_SERVICE_URL}/datos/sincronizar` con `X-Internal-Token`.
2. Si `ok=true`, publica `monitoreo.zona` por cada zona devuelta.
3. Si `ok=false` (Open-Meteo caído), publica igualmente `monitoreo.zona` para las
   zonas conocidas con `motivo='fallback_ultimo_valor'`, para recalcular con el
   último registro válido.
4. Espera `MONITORING_INTERVAL` segundos (30 en dev, 3600 en prod).

`data-service` en cada sincronización:

- Una sola petición batch a Open-Meteo con todas las coordenadas
  (`current=temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation`).
- Por zona inserta una fila en `variable_ambiental` con
  `fuente='Open-Meteo'`, `calidad='ok'`, `fecha_medicion` (hora de la
  observación) y `fecha_ingesta=NOW()`.
- Inserta una fila en `trazabilidad` por zona y ciclo.
- Actualiza `fuente_datos.ultima_ingesta` y `registros_ultimo_lote`.
- Actualiza `ingesta_control`.
- Si Open-Meteo falla, responde `ok=false` y **no inserta nada**: se conserva el
  último registro válido.

## 5. Modelo de datos (migración idempotente)

### `comuna_zona`
Añade: `slug VARCHAR(100) UNIQUE`, `codigo_comuna VARCHAR(10)`,
`latitud FLOAT`, `longitud FLOAT`, `geom GEOMETRY(Point,4326)` + índice GIST.

### `variable_ambiental`
Añade: `fuente VARCHAR(60)`, `calidad VARCHAR(20)`, `fecha_ingesta TIMESTAMP
DEFAULT NOW()`, `geom GEOMETRY(Point,4326)`.
Índice `(id_zona, fecha_medicion DESC)`.

### `historial_incendio`
Añade: `region VARCHAR(50)`, `periodo DATE`, `nro_incendios INT`,
`nro_incendios_ant INT`, `superficie_afectada_ant FLOAT`,
`geom GEOMETRY(Point,4326)`. `id_zona` pasa a ser opcional para datos
regionales. Índice único `(region, periodo, fuente)` para idempotencia.

### `distrito_censal` (nueva)
`id_distrito`, `codigo_distrito UNIQUE`, `codigo_comuna`, `nombre_comuna`,
`tipo`, `poblacion`, `superficie`, `geom GEOMETRY(MultiPolygon,4326)`,
`id_fuente`, `created_at`.

### `fuente_datos`
Añade: `ultima_ingesta TIMESTAMP`, `registros_ultimo_lote INT`.

### `ingesta_control` (nueva)
`id_proceso TEXT PRIMARY KEY`, `ultima_ejecucion`, `ultimo_exito`, `estado`,
`detalle`, `registros`.

### Seeds
- Zonas: Quilpué (`5801`), Viña del Mar (`5109`), Valparaíso (`5101`).
- Fuentes: IDE Chile / INE, CONAF, EMAS, Open-Meteo.
- La carga EMAS reemplaza el seed meteorológico inventado.

## 6. Contratos

### `GET /datos/{zona}` (data-service)
```json
{
  "zona": "quilpue",
  "id_zona": 1,
  "comuna": "Quilpué",
  "variables": {
    "temperatura": 16.8,
    "humedad": 75.0,
    "viento": 2.7,
    "precipitacion": 0.0,
    "vegetacion": "alta",
    "incendios_historicos": 11
  },
  "features": {
    "mes": 11,
    "severidad_ant": 2.85,
    "delta_incendios": 1,
    "nro_inc_ant": 10,
    "sup_afec_ant": 28.46,
    "TEMP_MED": 16.8,
    "HUMEDAD_REL": 75.0,
    "VEL_VIENTO": 2.7,
    "PRECIPITACION": 0.0
  },
  "meta": {
    "fuente": "Open-Meteo",
    "fecha_medicion": "2026-11-18T00:30:00",
    "fecha_ingesta": "2026-11-18T01:00:12",
    "antiguedad_minutos": 30.2,
    "calidad": "ok",
    "historico_fuente": "CONAF",
    "historico_periodo": "2026-09-28",
    "trazabilidad_id": 128
  }
}
```
Si la BD no responde, `data-service` devuelve el último valor en memoria con
`meta.fuente="fallback-local"` y `meta.calidad="degradado"` (nunca 500).

### `POST /datos/sincronizar` (interno)
```json
{
  "ok": true,
  "zonas_actualizadas": ["quilpue", "vina_del_mar", "valparaiso"],
  "zonas_conocidas": ["quilpue", "vina_del_mar", "valparaiso"],
  "registros": 3,
  "fuente": "Open-Meteo",
  "fecha_medicion": "2026-11-18T01:00:00",
  "duracion_ms": 412
}
```

### `GET /api/riesgo/todas` (api-service)
Cada zona añade `comuna`, `codigo_comuna`, `fuente`, `fecha_medicion`,
`antiguedad_minutos`.

### `GET /datos/distritos` (data-service)
Sigue devolviendo `distritos: {codigo: nivel}`, ahora calculado desde el último
`resultado_riesgo` de la comuna; añade `meta` con fuente y antigüedad. Si no hay
datos en BD, usa `distritos_riesgo.json` como fallback.

### `POST /clasificar` (risk-service)
Acepta los 9 features si vienen informados; si no, los deriva (compatibilidad).
`mes` deja de ser fijo y usa el mes actual. No usa `vegetacion` (el modelo no la
considera).

## 7. Frontend

- La lista de zonas muestra `fuente · actualizado hace X`.
- El popup del distrito muestra riesgo comunal vigente, fuente y antigüedad.
- Se mantiene la advertencia de datos antiguos.

## 8. Configuración

- `db` pasa a `postgis/postgis:16-3.4-alpine`.
- `data-service` recibe `DATABASE_URL`, `INTERNAL_TOKEN`, `OPEN_METEO_URL` y
  monta en solo lectura `./ml-pipeline` y `./frontend/data`.
- `monitoring-service` recibe `DATA_SERVICE_URL`, `INTERNAL_TOKEN`,
  `MONITORING_INTERVAL` (30 dev / 3600 prod).
- Gateway bloquea `POST /api/datos/sincronizar`.

## 9. Verificación

1. Pruebas unitarias de las funciones puras de ingesta (parseo de CSV/GeoJSON,
   estación más cercana, mapeo Open-Meteo, features, fallback).
2. `docker compose up -d` y logs de `data-service`/`monitoring-service`.
3. `GET /datos/quilpue` devuelve `fuente="Open-Meteo"` y menos de 90 minutos de
   antigüedad.
4. `GET /api/riesgo/todas` devuelve `fecha_evaluacion` renovada tras el evento
   `monitoreo.zona`.
5. Simulación de caída de Open-Meteo: se conserva el último registro y el
   servicio sigue respondiendo.
6. Revisión visual del frontend con datos reales.

## 10. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Open-Meteo caído o rate-limited | Batch único, timeout, reintento, último valor válido |
| Volumen PostgreSQL existente sin PostGIS | DDL idempotente al arrancar + `CREATE EXTENSION` |
| CSV no versionados (`*.csv` en `.gitignore`) | Se documenta; si faltan, el sistema degrada y Open-Meteo cubre la meteorología |
| Datos CONAF son regionales, no por comuna | Se modelan como regionales; features del modelo son regionales |
| Clima.csv es un snapshot (2026-09-28) | Solo línea base; Open-Meteo lo reemplaza cada hora |
| `resultado_riesgo` crece sin límite | Índice existente; retención fuera de alcance |
| `compose` sin swarm ignora `replicas` | No afecta al flujo; se documenta |
