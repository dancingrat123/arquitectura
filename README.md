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
| data-service | FastAPI con datos simulados. En producción: ingesta desde IDE Chile, CONAF y meteorología. | 8000 |
| risk-service | FastAPI con modelo de clasificación (simulado). En producción: Random Forest. | 8001 |
| monitoring-service | Publica eventos de monitoreo a RabbitMQ cada 30 segundos. | - |
| notification-service | Consume eventos de riesgo y genera alertas. | - |
| rabbitmq | Bus de eventos. | 5672 / 15672 |
| db | PostgreSQL + PostGIS. | 5432 |
| prometheus | Recolección de métricas. | 9090 |
| grafana | Visualización de métricas. | 3000 |
| node-exporter | Métricas del host. | 9100 |
| cadvisor | Métricas de contenedores. | 8080 |

### Flujo principal

1. Ingest Service (simulado) obtiene datos de fuentes públicas.
2. IA Service (simulado) calcula el riesgo por distrito.
3. Cache Updater (simulado) guarda resultados en PostgreSQL.
4. API Service lee la caché y expone `/api/riesgo/todas`.
5. Frontend carga el GeoJSON de distritos y consulta el riesgo por distrito al Data Service.
6. El mapa colorea cada distrito según su nivel de riesgo.

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
| API de riesgo | http://localhost:9080/api/riesgo/todas | admin / sirif2026 |
| API de distritos | http://localhost:9080/api/datos/distritos | admin / sirif2026 |
| Prometheus | http://localhost:9090 | - |
| Grafana | http://localhost:3000 | admin / admin |
| RabbitMQ | http://localhost:15672 | sirif / sirif2026 |

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

- HTTP Basic Auth en rutas `/api/`.
- Rate limiting: 10 solicitudes por segundo por IP, con ráfaga de 20.
- Security headers: X-Frame-Options, X-Content-Type-Options, X-XSS-Protection, Referrer-Policy.
- Gestión de secretos mediante variables de entorno en Docker Compose.

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

- Ingesta real de datos: simulada.
- Modelo Random Forest: simulado.
- Datos de riesgo por distrito: simulados.
- Arquitectura preparada para reemplazar las simulaciones por implementaciones reales sin rediseño.

## Declaración de uso de IA

Se utilizaron herramientas de IA generativa para apoyo en redacción, revisión técnica y generación de diagramas. Todo el contenido fue revisado, validado y adaptado por el equipo. Las decisiones arquitectónicas y el análisis técnico son de autoría propia.

## Referencias

- IDE Chile. (2024). Geoportal y Visor de Mapas. https://www.ide.cl/
- CONAF. (2024). Incendios forestales. https://www.conaf.cl/incendios/
- INE. (2024). Geodatos Abiertos. https://www.ine.gob.cl/herramientas/portal-de-mapas/geodatos-abiertos
- FastAPI. (2024). Documentación oficial. https://fastapi.tiangolo.com/
- Docker. (2024). Docker Compose. https://docs.docker.com/compose/