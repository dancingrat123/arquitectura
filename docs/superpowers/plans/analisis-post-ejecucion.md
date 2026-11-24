# Análisis post-ejecución — SIRIF (roadmap 20 funciones)

Fecha: 2026-11-24
Rama: `feature/ingesta-real`
Estado: diagnóstico aprobado; correcciones de bloques A/B/S2 en curso.

## Veredicto general

La ejecución del roadmap fue fiel y de buena calidad: módulo `shared`, RBAC,
aislamiento del simulador, migraciones idempotentes y verificación de checksum
están bien resueltos. Los problemas más importantes no son de código sucio, sino
de **coherencia entre el modelo comunal (nuevo, fuera del plan) y el modelo
regional**, que produce inconsistencias visibles para el mandante.

## A. Correcciones (bugs)

- **A1 [Alto]** `direccion`/`peso` de `factores_predominantes` invertido para features de monotonía −1 (`risk-service/main.py::calcular_factores_predominantes`).
- **A2 [Alto]** Panel ejecutivo: `alertas_activas` siempre `0` (`api-service/main.py:1047`).
- **A3 [Alto]** `UMBRAL_ALERTA=0.50` muerto para el modelo comunal (prob. 0.03–0.09).
- **A4 [Alto]** Escala de `puntaje = round(p*15)` rota con el comunal ("ALTO · Puntaje 1/15").
- **A5 [Alto]** Simulador siempre usa el modelo regional; `delta_probabilidad` incomparable.
- **A6 [Medio]** `GET /api/riesgo/{zona}` dispara escritura+eventos on-demand accesible por `visualizador`.
- **A7 [Medio]** `POST /api/simular` accesible por `visualizador` (el plan lo restringía a analista).
- **A8 [Medio]** Asistente responde "no tengo datos para la zona 'mayor'" ante preguntas agregadas.
- **A9 [Medio]** `JWT_SECRET` con default hardcodeado (S2 incompleto).
- **A10 [Medio]** `buscar_usuario` abre conexión Postgres nueva por request (sin pool).
- **A11 [Bajo]** notification-service: self-consume de `notificacion.nueva` + doble conexión por evento.
- **A12 [Bajo]** monitoring-service reporta `postgres: ok` hardcodeado.
- **A13 [Bajo]** `simular_escenario` no registra en bitácora.
- **A14 [Bajo]** `region_conocida = ... == "valparaiso"` hardcodeado (`risk-service/main.py:427`).

## B. Inconsistencias del modelo comunal

1. Cambia silenciosamente la semántica (escala de probabilidad, umbrales, `puntaje`, `fuera_ventana_estacional`) y el frontend no muestra `tipo_estimacion`.
2. Tensión con el relato "honesto": target de ocurrencia (≥1 incendio/día) sin spec propio.
3. Train/serve skew: entrena con ERA5, infiere con `ecmwf_ifs025`.
4. `train_comunal.py` redefine `FEATURES_CLIMA` localmente (no importa `shared`).
5. Sin checksum SHA-256 en `load_model_comunal` (S9 asimétrico).
6. `_bundle_comunal()` default con umbrales 0.20/0.08 vs reales 0.07/0.031.

## C. Seguridad pendiente (backlog)

- S1: `gateway/nginx.key`/`nginx.crt` versionados.
- S4: puertos 5672/15672/9090/3000/8080/9100 expuestos al host.
- S5: cAdvisor `privileged: true` + montajes amplios.
- S7: faltan HSTS/CSP/Permissions-Policy; `X-XSS-Protection` obsoleto.
- S2 (parcial): `JWT_SECRET` con default; sin bloqueo por N fallos.
- S9 (parcial): comunal sin checksum.

## D. Deuda técnica / reproducibilidad

- A2: `conaf.csv`/`emas.csv`/`clima.csv` ignorados (`*.csv`); dataset comunal `datos_externos/...` no versionado → `train_comunal.py` no reproducible.
- T3: `train.py`, `eda.py`, `matriz_correlacion.png`, `test_load.py`, `filtrar_distritos.py` obsoletos; `output4.png` untracked.
- `docs/superpowers/plans/*.md` sin commitear.

## E. Funcionalidades sugeridas (trabajo futuro v2.1)

1. Vista gráfica de evolución temporal en el frontend.
2. Monitoreo de drift (`fuera_de_rango` no expuesto ni alertado).
3. Configuración por modelo de `UMBRAL_ALERTA` y `puntaje_maximo`.
4. Comparador con evolución superpuesta.
5. Canal real de notificación (SMTP/webhook).
6. Umbrales ajustables por zona desde admin.
7. Poblar `revision_log` en cada reentrenamiento.
8. Playwright e2e automatizado en CI.
9. Semáforo por antigüedad del heartbeat en `estado_servicio`.
10. Documentar/versionar el dataset comunal.

## Orden de ejecución acordado

1. Bloque A (A1–A14).
2. Bloque B seleccionado (B.1 UI `tipo_estimacion`, B.4 `shared` en `train_comunal.py`, B.5 checksum comunal, B.6 umbrales default).
3. S2 (`JWT_SECRET` fail-fast).
4. Bloque E queda como **v2.1** (documentado, no ejecutado).

## Recalibración operativa de umbrales (post-análisis)

- Hallazgo: el umbral comunal era un percentil (0.07 = top 5 %); en un día seco todas las zonas quedaban en ALTO, y Viña (7.18 %) quedaba a 0.0018 del corte.
- Se pasó a un criterio **absoluto**: ALTO ≥ 15 %, MEDIO ≥ 5 % de probabilidad diaria (≈5× y ≈1.6× la tasa base).
- Configurable con `UMBRAL_ALTO_COMUNAL` / `UMBRAL_MEDIO_COMUNAL`.
- Pendiente (v2.1): dominancia de la identidad de comuna y clima compartido (misma celda ECMWF), y sobreconfianza en extremos.
