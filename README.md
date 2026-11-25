# SIRIF - Sistema Inteligente de Riesgo de Incendios Forestales

Proyecto semestral de la asignatura Arquitectura de Sistemas de Inteligencia Artificial (ITY1102).

Caso: Sistema de apoyo a la prevención y clasificación de riesgo de incendios forestales en la Región de Valparaíso.

## Descripción

SIRIF es una plataforma web que clasifica el nivel de riesgo de incendios forestales (Bajo, Medio, Alto) para distritos censales de las comunas de Quilpué, Viña del Mar y Valparaíso. Integra datos geoespaciales de IDE Chile y, en su versión objetivo, datos históricos de CONAF y variables meteorológicas.

El sistema no predice incendios. Solo clasifica susceptibilidad territorial como apoyo al análisis preventivo.

## Arquitectura

Arquitectura de microservicios event-driven con comunicación asíncrona vía RabbitMQ.

### Servicios

| Servicio | Descripción | Puerto interno |
|---|---|---|
| gateway | Nginx como API Gateway. Autenticación, rate limiting, security headers y balanceo. | 80 |
| frontend | Aplicación web con Leaflet para visualizar distritos censales. | 80 |
| api-service | FastAPI que expone endpoints de consulta de riesgo. | 8000 |
| data-service | Ingesta real: carga IDE/INE, CONAF y EMAS en PostGIS al arrancar y sincroniza Open-Meteo por lote (`POST /datos/sincronizar`). | 8000 |
| risk-service | FastAPI con el Random Forest entrenado sobre CONAF + climatología. | 8001 |
| monitoring-service | Worker único: cada `MONITORING_INTERVAL` dispara la ingesta y publica `monitoreo.zona` en RabbitMQ. | - |
| notification-service | Consume eventos de riesgo y genera alertas. | - |
| rabbitmq | Bus de eventos. | 5672 / 15672 |
| db | PostgreSQL + PostGIS. | 5432 |
| prometheus | Recolección de métricas. | 9090 |
| grafana | Visualización de métricas. | 3000 |
| node-exporter | Métricas del host. | 9100 |
| cadvisor | Métricas de contenedores. | 8080 |

### Flujo principal

1. `data-service` carga una sola vez las capas territoriales (IDE/INE), los históricos de CONAF y la climatología base EMAS en PostgreSQL + PostGIS, registrando fuente y trazabilidad.
2. `monitoring-service` dispara cada `MONITORING_INTERVAL` (30 s en desarrollo, 3600 s en producción) el endpoint interno `POST /datos/sincronizar`.
3. `data-service` consulta Open-Meteo en una sola petición batch para todas las zonas, guarda las mediciones en `variable_ambiental` y conserva el último valor válido si la API falla.
4. `monitoring-service` publica `monitoreo.zona`; `api-service` infiere con `risk-service` usando las features reales y guarda el resultado en PostgreSQL.
5. La API expone `/api/riesgo/todas` con nivel, fuente y antigüedad de los datos.
6. El frontend colorea cada distrito con el riesgo comunal vigente y muestra la fuente y la hora de la última medición.

## Requisitos

- Docker Desktop
- Docker Compose (plugin v2)
- Navegador web moderno

## Ejecución

```bash
docker compose --compatibility up -d --build
```

El flag `--compatibility` permite que Docker Compose respete `deploy.replicas` en `api-service`.

Para detener el stack:

```bash
docker compose stop
```

Para reanudar:

```bash
docker compose start
```

Para eliminar contenedores (conserva volúmenes):

```bash
docker compose down
```

## URLs y credenciales

| Servicio | URL | Credenciales |
|---|---|---|
| Frontend | http://localhost:9080 | admin / sirif2026 |
| Mapa público (sin sesión) | http://localhost:9080 | - |
| API de riesgo | http://localhost:9080/api/riesgo/todas | admin / sirif2026 |
| API de distritos | http://localhost:9080/api/datos/distritos | admin / sirif2026 |
| API pública de zonas | http://localhost:9080/api/publico/zonas | - |
| Prometheus | http://localhost:9090 | - |
| Grafana | http://localhost:3000 | admin / admin |
| RabbitMQ | http://localhost:15672 | sirif / sirif2026 |

### Acceso público y sesión

El mapa de riesgo es de **acceso público**: al entrar sin sesión se muestran las
38 comunas coloreadas, la leyenda y el nivel/probabilidad de la comuna
seleccionada. Los endpoints `/api/publico/zonas` y `/api/publico/distritos` son
de solo lectura, no reciben parámetros y no exponen usuarios, bitácora,
acciones ni factores explicativos.

El resto de la plataforma (diagnóstico, historial, ranking, simulador,
operación territorial, alertas, usuarios, reportes y exportaciones) exige
**iniciar sesión**.

El **registro es autoservicio** desde la pantalla de login: con usuario y
contraseña (sin correo) se crea al instante una cuenta con perfil
`visualizador` (solo lectura). El servidor fija el rol; el usuario no puede
auto-elevarse. La asignación de `analista` o `admin`, la desactivación y el
reseteo de contraseñas siguen siendo exclusivos del administrador.

La interfaz incluye **tema claro y oscuro**, con detección inicial del tema del
sistema y preferencia persistida en `localStorage` (`sirif_tema`).

## Escalado y balanceo

El servicio `api-service` corre con 2 réplicas declaradas en `docker-compose.yml` mediante `deploy.replicas: 2`.

Nginx balancea las solicitudes entre ambas réplicas usando round-robin.

Verificar réplicas:

```bash
docker compose ps
```

Deben aparecer dos contenedores de `api-service`:

```
arquitectura_api-service_1
arquitectura_api-service_2
```

## Monitoreo

Prometheus scrapea cada 15 segundos el endpoint `/metrics` de los servicios FastAPI instrumentados con `prometheus-fastapi-instrumentator`.

Grafana muestra un dashboard con:

- Requests por segundo (RPS).
- Latencia p95.
- Errores por segundo.
- CPU y RAM del host.
- Contenedores activos.

Para generar tráfico y ver métricas:

```bash
1..200 | ForEach-Object { curl.exe -s -u admin:sirif2026 http://localhost:9080/api/riesgo/todas > $null }
```

## Seguridad

Controles implementados en el gateway Nginx:

- Autenticación JWT Bearer en las rutas `/api/` (excepto `/api/publico/`).
- Acceso público de solo lectura en `/api/publico/` (mapa sin sesión, sin datos operativos).
- Rate limiting: 10 solicitudes por segundo por IP, con ráfaga de 20.
- Auto-registro limitado a 3 cuentas por minuto por IP.
- Contraseñas PBKDF2 (mínimo 8 caracteres); el registro solo puede crear `visualizador`.
- Security headers: X-Frame-Options, X-Content-Type-Options, X-XSS-Protection, Referrer-Policy.
- `Content-Security-Policy` con scripts propios (`script-src 'self'`), estilos inline para Leaflet y teselas de OpenStreetMap como única fuente externa de imágenes.
- `server_tokens off` (Nginx no expone su versión).
- HSTS pendiente hasta usar un certificado emitido por una CA real.
- Puertos de infraestructura en `127.0.0.1` (Prometheus, Grafana, panel de RabbitMQ).
- AMQP, cAdvisor y node-exporter solo en la red interna de Docker (sin puerto en el host).
- Gestión de secretos mediante variables de entorno en Docker Compose.

El certificado TLS de desarrollo es **autofirmado** y se genera en el primer
arranque del gateway dentro del volumen `gateway_tls`. `nginx.key` y
`nginx.crt` **no se versionan**; la clave que estaba en el repositorio fue
rotada. Para regenerar el certificado:

```bash
docker compose exec gateway rm -f /etc/nginx/tls/nginx.crt /etc/nginx/tls/nginx.key
docker compose restart gateway
```

El navegador mostrará una advertencia de certificado no confiable (esperable en
desarrollo). En producción se debe usar un certificado de una CA real (por
ejemplo, Let's Encrypt) y habilitar HSTS.

Credenciales de la API: `admin / sirif2026`.

## Estructura del repositorio

```
arquitectura/
├── api-service/
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── data-service/
│   ├── main.py
│   ├── distritos_riesgo.json
│   ├── requirements.txt
│   └── Dockerfile
├── risk-service/
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── monitoring-service/
│   └── main.py
├── notification-service/
│   └── main.py
├── frontend/
│   ├── index.html
│   ├── leaflet.js
│   ├── leaflet.css
│   ├── data/
│   │   └── distritos_filtrados.geojson
│   └── Dockerfile
├── gateway/
│   ├── nginx.conf
│   └── .htpasswd
├── db/
│   └── init.sql
├── monitoring/
│   └── prometheus.yml
├── docker-compose.yml
└── README.md
```

## Estado de implementación

- Ingesta real: implementada. Open-Meteo cada hora (batch), CONAF histórico y climatología EMAS en PostgreSQL + PostGIS, con trazabilidad en `fuente_datos` y `trazabilidad`.
- Modelo Random Forest: real, cargado desde `risk-service/modelo_riesgo.pkl` y alimentado con features reales.
- Riesgo por distrito: derivado del último riesgo comunal calculado.
- Limitaciones conocidas:
  - La cobertura vegetal sigue siendo un atributo territorial estático (los datasets disponibles no la contienen) y el modelo no la usa.
  - `clima.csv` es un snapshot del 2026-09-28: se usa solo como línea base hasta la primera sincronización.
  - CONAF publica agregados regionales, no por comuna; las features históricas son regionales.
  - Los CSV de `ml-pipeline/` no se versionan (`*.csv` en `.gitignore`): si faltan, el sistema degrada y Open-Meteo cubre la meteorología.

### Configuración de la ingesta

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `MONITORING_INTERVAL` | `3600` | Segundos entre ciclos de ingesta. Usar `30` en desarrollo. |
| `INTERNAL_TOKEN` | — | Protege `POST /datos/sincronizar` (el gateway además lo bloquea externamente). |
| `OPEN_METEO_URL` | `https://api.open-meteo.com/v1/forecast` | Endpoint de Open-Meteo. |
| `OPEN_METEO_TIMEOUT` | `30` | Timeout en segundos de la petición batch. |

## Modelo de riesgo

- Tipo: Random Forest monotónico calibrado con sigmoid (`CalibratedClassifierCV`), `n_estimators=500`,
  `min_samples_leaf=5`, `class_weight=None`, `random_state=42`.
- Features: 9 de clima diario (`TEMP_MED`, `TEMP_MAX`, `HUMEDAD_REL`, `VEL_VIENTO`, `PRECIPITACION`,
  `precip_7d`, `precip_30d`, `temp_media_7d`, `humedad_7d`) más 16 indicadores de región (tasa base).
- Métricas temporales (jul-ago → sep 2026): AUC 0.662, AP 0.523, Brier 0.191 (tasa base regional:
  AUC 0.632, Brier 0.201).
- Umbrales: ALTO `>= 0.20`, MEDIO `>= 0.08`, BAJO `< 0.08`. `puntaje = round(probabilidad * 15)`.
- Limitaciones: modelo regional (no distrital), sin estacionalidad fuera de jul-sep 2026, no generaliza a
  regiones no vistas sin reentrenar, probabilidad calibrada solo en la ventana de entrenamiento.
- Reentrenar: `python ml-pipeline/train_honesto.py` (usa `ml-pipeline/openmeteo_entrenamiento.csv` versionado).
- Fuentes: CONAF, EMAS, Open-Meteo `ecmwf_ifs025`. Artefacto: `risk-service/modelo_riesgo.pkl`,
  métricas: `ml-pipeline/model_metadata.json`.

## Modelo comunal (experimental)

Además del modelo regional, se entrenó un **Random Forest comunal diario** para Valparaíso:

- Dataset: `datos_externos/conaf/dataset_comunal_diario_valparaiso_2017_2025.csv` (36 comunas, 2017-2025).
- Features: 9 de clima diario + mes (sin/cos) + 36 indicadores de comuna.
- Validación temporal (2017-2023 → 2024-2025): AUC 0.760, AP 0.124, Brier 0.037.
- Validación espacial (5-fold por comuna, clima+mes sin comuna): AUC 0.729, AP 0.069, Brier 0.029.
- Umbrales OOF (percentiles entrenados): ALTO top 5 % (0.07), MEDIO top 20 % (0.031).
- **Umbrales operativos (los que usa la API):** probabilidad **absoluta** ALTO ≥ 15 %, MEDIO ≥ 5 %.
  Motivo: el percentil 0.07 es tan bajo que en un día seco todas las comunas quedaban en ALTO.
  Configurables con `UMBRAL_ALTO_COMUNAL` / `UMBRAL_MEDIO_COMUNAL`.
- Artefacto: `risk-service/modelo_riesgo_comunal.pkl`; métricas: `ml-pipeline/model_metadata_comunal.json`.
- Reentrenar: `python ml-pipeline/train_comunal.py`.

El modelo comunal está conectado al flujo operacional para las comunas de Quilpué, Viña del Mar y Valparaíso. Si la comuna no está en el bundle, `risk-service` usa el modelo regional como fallback. La API expone `tipo_estimacion="riesgo_meteorologico_comunal"` y `version_modelo="rf-comunal-clima-v1"`.

## Notas
Ahora se usa jwt y https

## Declaración de uso de IA

Se utilizaron herramientas de IA generativa para apoyo en redacción, revisión técnica y generación de diagramas. Todo el contenido fue revisado, validado y adaptado por el equipo. Las decisiones arquitectónicas y el análisis técnico son de autoría propia.

## Referencias

- IDE Chile. (2024). Geoportal y Visor de Mapas. https://www.ide.cl/
- CONAF. (2024). Incendios forestales. https://www.conaf.cl/incendios/
- INE. (2024). Geodatos Abiertos. https://www.ine.gob.cl/herramientas/portal-de-mapas/geodatos-abiertos
- FastAPI. (2024). Documentación oficial. https://fastapi.tiangolo.com/
- Docker. (2024). Docker Compose. https://docs.docker.com/compose/
