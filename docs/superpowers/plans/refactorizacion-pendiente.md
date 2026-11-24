# Plan de refactorización pendiente — SIRIF

Fecha: 2026-11-18
Rama revisada: `feature/ingesta-real` (HEAD `5bfc94f`)
Estado: backlog vigente sobre el código actual

## 1. Contexto y alcance

Se revisó el repositorio en disco. **Ya están resueltos** (y por tanto quedan fuera
de este plan):

- Ingesta real por lotes con PostgreSQL + PostGIS, trazabilidad y fallback que
  conserva el último registro válido (`495c664`, `ca2cbad`).
- Modelo `rf-clima-region-v2`: Random Forest monotónico calibrado con sigmoid,
  clima diario de Open-Meteo `ecmwf_ifs025`, 9 features + 16 regiones, umbrales
  0.20/0.08 y `puntaje = round(p*15)` (`b264d3d` … `5bfc94f`).
- Parser diario, features en JSONB, probabilidad y advertencias propagadas por
  `risk-service`, `api-service` y frontend.

Este documento conserva únicamente las **mejoras de seguridad, arquitectura y
deuda técnica que siguen vigentes** sobre ese código. Cada ítem indica
ubicación, problema, mejora propuesta y prioridad (Alta / Media / Baja).

---

## 2. Seguridad

### S1. Clave privada TLS y `.htpasswd` versionados en git — ALTA

- **Ubicación:** `gateway/nginx.key`, `gateway/nginx.crt`, `gateway/.htpasswd`
  (los tres están trackeados en git).
- **Problema:** la clave privada del certificado queda en el historial del repo,
  a la vista de cualquiera con acceso. `.htpasswd` además está obsoleto:
  `nginx.conf` no usa `auth_basic` en ningún `location`.
- **Mejora:**
  - Rotar el certificado (generar un par nuevo; el antiguo ya está expuesto en
    el historial) y dejar de versionar `.key`/`.crt`: generarlos en el deploy o
    inyectarlos por secretos, montando solo en el contenedor `gateway`.
  - Eliminar `gateway/.htpasswd` (sin uso).

### S2. Secretos hardcodeados con fallbacks débiles — ALTA

- **Ubicación:**
  - `api-service/auth.py:9` — `SECRET_KEY = os.getenv("JWT_SECRET", "sirif_super_secret_key_2026")`.
  - `api-service/auth.py:18` — `HASH_SIRIF_2026 = pwd_context.hash("sirif2026")`
    (contraseña admin fija en código; además se recalcula el hash en cada arranque
    de cada worker, coste CPU innecesario).
  - `monitoring-service/main.py:10-11` y `notification-service/main.py:8-9` —
    `RABBITMQ_USER`/`RABBITMQ_PASS` con default `"sirif"` / `"sirif2026"`.
  - `frontend/index.html:85` — `<input type="password" ... value="sirif2026">`
    prellena la contraseña en el formulario de login.
- **Problema:** credenciales y secretos en el código, con defaults que permiten
  arrancar sin configurar el entorno y quedan operativos por accidente.
- **Mejora:**
  - Eliminar todos los defaults de secretos: leer solo de entorno y **fallar al
    arrancar** si no están definidos (fail-fast).
  - Mover la contraseña admin a configuración (p. ej. `ADMIN_PASSWORD_HASH`
    generado fuera del código) o a un almacén de usuarios real.
  - Quitar el `value="sirif2026"` del HTML.
  - Crear `.env.example` con todas las claves (sin valores reales) para el
    onboarding.

### S3. `INTERNAL_TOKEN` opcional → sincronización sin autenticación — ALTA

- **Ubicación:** `data-service/main.py:283` (`if esperado and x_internal_token != esperado`);
  `monitoring-service/main.py:13` (`INTERNAL_TOKEN = os.getenv(..., "")`).
- **Problema:** si `INTERNAL_TOKEN` no está definido, `POST /datos/sincronizar`
  queda abierto a cualquier contenedor de la red interna.
- **Mejora:** exigir el token siempre (responder 401 si falta), con fail-fast al
  arrancar si la variable no está presente.

### S4. Puertos de infraestructura expuestos al host sin autenticación — ALTA

- **Ubicación:** `docker-compose.yml` publica al host: RabbitMQ `5672` y `15672`
  (management), Prometheus `9090`, Grafana `3000`, cAdvisor `8080`,
  node-exporter `9100`.
- **Problema:** sin firewall ni reverse-proxy, estos servicios son alcanzables
  desde fuera. Grafana usa `admin/admin` si no se define `GRAFANA_PASSWORD`
  (el README lo documenta como `admin / admin`); Prometheus, cAdvisor y
  node-exporter no tienen autenticación.
- **Mejora:**
  - Exponer al host solo lo imprescindible (idealmente solo el gateway `9443`);
    el resto por red interna.
  - Autenticar Grafana con contraseña obligatoria (no `admin`), poner
  Prometheus/cAdvisor/node-exporter/RabbitMQ-management detrás del gateway o de
  una VPN, o restringir a `127.0.0.1`.
  - Actualizar el README para no documentar credenciales por defecto reales.

### S5. cAdvisor privilegiado con montajes amplios del host — ALTA

- **Ubicación:** `docker-compose.yml:193-199` — `privileged: true` y montajes de
  `/`, `/var/run`, `/sys`, `/var/lib/docker`, `/dev/disk`.
- **Problema:** acceso de lectura a casi todo el host desde un contenedor;
  aumenta la superficie de ataque y el riesgo de escape.
- **Mejora:** quitar `privileged: true` y limitar montajes a los estrictamente
  necesarios (o evaluar si cAdvisor es necesario en absoluto para este alcance).

### S6. Endpoints de datos sin autenticación en el gateway — MEDIA

- **Ubicación:** `gateway/nginx.conf:71-76` — `/api/datos/` se proxya sin exigir
  JWT (a diferencia de `/api/riesgo`, que lo valida en la app).
- **Problema:** `/api/datos/distritos` y `/api/datos/{zona}` quedan accesibles
  sin token a través del gateway.
- **Mejora:** aclarar qué es público y proteger el resto. El frontend usa el
  archivo estático `/data/distritos_filtrados.geojson`, no el endpoint; si el
  endpoint no es público, exigir JWT o el token interno.

### S7. Cabeceras de seguridad incompletas — MEDIA

- **Ubicación:** `gateway/nginx.conf:36-40`.
- **Problema:** faltan `Strict-Transport-Security` (HSTS), `Content-Security-Policy`
  y `Permissions-Policy`. `X-XSS-Protection` está obsoleto.
- **Mejora:** agregar HSTS, CSP y Permissions-Policy; retirar `X-XSS-Protection`.

### S8. CI sin escaneo de secretos, SAST ni escaneo de imágenes — MEDIA

- **Ubicación:** `.github/workflows/ci.yml`.
- **Problema:** nada detecta secretos commiteados (p. ej. S1/S2) ni
  vulnerabilidades de dependencias/imágenes.
- **Mejora:** añadir `gitleaks` (o trufflehog), un linter/SAST (bandit, ruff) y
  `docker buildx` + escaneo (Trivy/Grype) o Dependabot/Renovate.

### S9. Carga de pickle sin verificación de integridad — BAJA

- **Ubicación:** `risk-service/main.py:137` — `joblib.load(MODEL_PATH)`.
- **Problema:** si el artefacto se sustituye, es ejecución arbitraria.
- **Mejora:** publicar el modelo con un checksum (p. ej. SHA-256 en
  `model_metadata.json`) y verificar antes de cargar.

### S10. Sin bloqueo de login y rate limit débil — BAJA

- **Ubicación:** `gateway/nginx.conf:43-48` (solo `limit_req` en `/token`);
  `api-service/main.py:205` (sin contador de intentos).
- **Mejora:** bloqueo temporal tras N fallos y/o `limit_req` más estricto en
  `/token`.

---

## 3. Arquitectura

### A1. Esquema de BD en dos fuentes con drift — ALTA

- **Ubicación:** `db/init.sql` vs `data-service/db.py` (lista `DDL`).
- **Problema:** `init.sql` **no** tiene `variable_ambiental.features JSONB` ni
  `resultado_riesgo.probabilidad`/`fuera_ventana_estacional`; `db.py` los agrega
  con `ALTER ... IF NOT EXISTS` en runtime. Dos verdades distintas y divergentes.
- **Mejora:** una única fuente de verdad de esquema con migraciones versionadas
  (Alembic o Flyway); `init.sql` limitado a extensiones y seeds, o generado desde
  las migraciones.

### A2. Datos de entrada no versionados → no reproducible desde un clone — ALTA

- **Ubicación:** `.gitignore:22` (`*.csv`); `data-service/ingestion.py:19-21` y
  `ml-pipeline/train_honesto.py` (rutas `conaf.csv`, `emas.csv`, `clima.csv`).
- **Problema:** `conaf.csv`, `emas.csv` y `clima.csv` son **requeridos** en el
  bootstrap de `data-service` y en el entrenamiento, pero están ignorados por
  git (no trackeados). Un clone limpio no puede arrancar la ingesta inicial ni
  reentrenar. Solo `openmeteo_entrenamiento.csv` tiene la excepción.
- **Mejora:** versionar los tres archivos de entrada (agregar excepciones en
  `.gitignore` equivalentes a la de `openmeteo_entrenamiento.csv`), o publicarlos
  en un almacén versionado y documentar cómo obtenerlos. Esto es la condición
  para que `train_honesto.py` sea reproducible.

### A3. Seed con el mapeo de puntaje fijo ya eliminado — MEDIA

- **Ubicación:** `db/init.sql:175-178` — `resultado_riesgo` seed con
  `puntaje 15/7/3` y `version_modelo='seed'`.
- **Problema:** reproduce el esquema antiguo (ALTO→15, MEDIO→7, BAJO→3) que el
  modelo honesto eliminó; además asume `id_zona` 1,2,3.
- **Mejora:** eliminar el seed o regenerarlo con el contrato nuevo
  (`puntaje = round(probabilidad*15)`, `version_modelo='rf-clima-region-v2'`) y
  sin `id_zona` hardcodeado.

### A4. `notification-service` es un stub — MEDIA

- **Ubicación:** `notification-service/main.py:31-49` — solo imprime a consola.
- **Problema:** no emite ninguna notificación real (email/webhook/cola DLQ);
  consume eventos y los descarta.
- **Mejora:** definir el canal real de notificación y el contrato de reintentos
  (DLQ), o documentar explícitamente que es un placeholder.

### A5. `api-service` sin pool de conexiones y sin gestión de recursos — MEDIA

- **Ubicación:** `api-service/main.py:29-30` (`psycopg2.connect` por request),
  `inferir_y_guardar` (conexiones sin `try/finally`), `consumir_monitoreo`
  (hilo que no se recupera si el canal RabbitMQ muere).
- **Problema:** conexión nueva por request (latencia y presión sobre Postgres),
  fugas de conexión en error, y pérdida silenciosa del consumidor.
- **Mejora:** pool (`psycopg2.pool` o SQLAlchemy), `try/finally` en el acceso a
  BD, y supervisión/reconexión del consumidor (o `pika` con heartbeat y
  reconexión en bucle).

### A6. Lógica duplicada de región y features — MEDIA

- **Ubicación:** `ALIAS_REGIONES` y `FEATURES_CLIMA` repetidos en
  `ml-pipeline/train_honesto.py` y `risk-service/main.py` (y parcialmente en
  `data-service/parsers.py`).
- **Problema:** riesgo de drift entre entrenamiento e inferencia si cambian los
  alias o el orden de features.
- **Mejora:** extraer a un módulo compartido (p. ej. `shared/` o un paquete pip
  interno) consumido por los tres servicios.

### A7. Imágenes `latest` y dependencias sin fijar — MEDIA

- **Ubicación:** `docker-compose.yml` (prometheus, grafana, cadvisor,
  node-exporter con `:latest`); `api-service/requirements.txt` y
  `risk-service/requirements.txt` sin versiones.
- **Problema:** builds no reproducibles; actualizaciones implícitas que pueden
  romper el sistema.
- **Mejora:** fijar tags de imagen y versiones de dependencias (como ya hacen
  `data-service`, `monitoring-service` y `notification-service`).

### A8. `monitoring-service` y `notification-service` sin healthcheck ni métricas — BAJA

- **Ubicación:** `docker-compose.yml:100-127` (sin `healthcheck`); son workers
  sin endpoint HTTP.
- **Mejora:** exponer un healthcheck mínimo (p. ej. heartbeat a una tabla o
  endpoint `/health`) y/o métricas para detectar que dejaron de consumir.

---

## 4. Deuda técnica y reproducibilidad

### T1. CI no ejecuta la mayoría de los tests — ALTA

- **Ubicación:** `.github/workflows/ci.yml:27-30` — solo `api-service/test_main.py`.
- **Problema:** quedan fuera los 10 tests de `data-service`, 4 de `risk-service`
  y 3 de `ml-pipeline`; no hay tests de integración ni e2e.
- **Mejora:** ejecutar las cuatro suites en CI (con `pytest` por servicio) y
  añadir al menos un smoke test end-to-end.

### T2. Sin `.dockerignore` en los servicios — BAJA

- **Ubicación:** ninguno de los seis servicios tiene `.dockerignore`.
- **Problema:** el contexto de build incluye `__pycache__`, `.pytest_cache`,
  etc.
- **Mejora:** agregar `.dockerignore` por servicio.

### T3. Artefactos y scripts obsoletos — BAJA

- **Ubicación:** `ml-pipeline/train.py` (superado por `train_honesto.py`),
  `ml-pipeline/eda.py`, `ml-pipeline/matriz_correlacion.png`, `test_load.py`,
  `filtrar_distritos.py`.
- **Mejora:** eliminar lo que ya no se usa o moverlo a una carpeta
  `scripts/`/`notebooks/` documentada.

### T4. Código obsoleto menor — BAJA

- **Ubicación:** `api-service/auth.py:38,41` — `datetime.utcnow()` (deprecado en
  Python 3.12+).
- **Mejora:** usar `datetime.now(timezone.utc)`.

### T5. Dos copias del GeoJSON de distritos — BAJA

- **Ubicación:** `distritos_censales.geojson` (raíz) y
  `frontend/data/distritos_filtrados.geojson`.
- **Mejora:** conservar una sola fuente y documentar el filtrado
  (`filtrar_distritos.py`).

### T6. README documenta credenciales por defecto reales — BAJA

- **Ubicación:** `README.md` (tabla "URLs y credenciales").
- **Mejora:** tras S2/S4, dejar de publicar `sirif2026` y `admin/admin`; remitir
  a `.env.example` y rotación.

---

## 5. Orden de ejecución sugerido

1. **Quick wins de seguridad (Alta, bajo esfuerzo):** S2 (quitar defaults y
   fail-fast), S3 (token interno obligatorio), S1 (rotar y sacar `.key`/`.crt`/
   `.htpasswd`), S4 (cerrar puertos y forzar contraseña de Grafana).
2. **Reproducibilidad (Alta):** A2 (versionar `conaf.csv`/`emas.csv`/`clima.csv`),
   A1 (una sola fuente de esquema), T1 (CI con las 4 suites).
3. **Endurecimiento de infraestructura (Media):** S5 (cAdvisor), S6 (auth de
   `/api/datos`), S7 (cabeceras), S8 (escaneo en CI).
4. **Limpieza de arquitectura (Media/Baja):** A4 (notification), A5 (pool),
   A6 (módulo compartido), A7 (pins), A3/T3/T4/T5/T6.

> Nota: los ítems de Alta en seguridad (S1, S2, S3, S4, S5) y reproducibilidad
> (A1, A2) son los que mayor impacto tienen y los que se recomienda abordar
> primero; el resto es deuda incremental.
