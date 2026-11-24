# Estado actual del proyecto — SIRIF (feature/ingesta-real)

## Resumen
SIRIF es un sistema de clasificación de riesgo de incendios forestales para la region de valparaiso. La rama `feature/ingesta-real` reemplaza el modelo antiguo por un Random Forest monotónico calibrado con clima diario real de Open-Meteo.

## Rama y commits recientes
- Rama: `feature/ingesta-real`
- Últimos commits:
  - `5bfc94f` docs: documenta el modelo honesto, métricas y limitaciones
  - `ec712a4` feat: muestra probabilidad y advertencia de riesgo meteorológico
  - `ae158f5` feat: persiste y expone probabilidad de riesgo meteorológico
  - `33b1bd2` feat: inferencia con probabilidad calibrada y advertencias
  - `ca2cbad` feat: sincroniza clima diario y persiste features en JSONB
  - `d0c67e6` feat: parsea clima diario y calcula features meteorológicas
  - `b264d3d` feat: entrena RF monotónico calibrado con clima diario real
  - `f695508` chore: versiona la serie diaria de Open-Meteo para entrenamiento

## Estado de las tareas (Tareas 1–8)
- **Tarea 1 – COMPLETA:** `ml-pipeline/descargar_clima.py` ejecutado (16 regiones, 1920 filas, 0 nulos). `openmeteo_entrenamiento.csv` versionado.
- **Tarea 2 – COMPLETA:** `train_honesto.py` (3 tests PASS), artefacto `risk-service/modelo_riesgo.pkl` (CalibratedClassifierCV, 9 features + 16 regiones) y `model_metadata.json`.
- **Tarea 3 – COMPLETA:** `data-service/parsers.py` con `parse_openmeteo_diario` y `construir_features_clima` (10 tests PASS).
- **Tarea 4 – COMPLETA:** DDL con `features JSONB`, `probabilidad`, `fuera_ventana_estacional`; ingesta diaria con `ecmwf_ifs025`; `_leer_zona` expone `region` y `features`.
- **Tarea 5 – COMPLETA:** `risk-service` con probabilidad calibrada, umbrales 0.20/0.08, `puntaje=round(p*15)`, advertencias (4 tests PASS).
- **Tarea 6 – COMPLETA:** API persiste y expone `probabilidad`, `fuera_ventana_estacional`, `serializar_zona` (5 tests PASS).
- **Tarea 7 – COMPLETA:** Frontend con “Riesgo meteorológico por zona”, probabilidad, popup comunal y disclaimer.
- **Tarea 8 – COMPLETA:** README y spec actualizados; stack verificado con Docker Compose.

## Cómo ejecutar
```powershell
docker compose up -d --build
```

## Verificación de ingesta
- Sincronizar (dentro del contenedor, el host no tiene acceso al puerto 8000):
  ```powershell
  docker compose exec -T data-service python -c "import ingestion; print(ingestion.sincronizar())"
  ```
- Verificar features y calidad:
  ```powershell
  docker compose exec -T data-service python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://localhost:8000/datos/quilpue')); print(d['region'], sorted(d['features']), d['meta']['calidad'])"
  ```
  Esperado: `Valparaíso` + 9 claves + `ok`.

## Verificación de riesgo
```powershell
docker compose exec -T db psql -U sirif -d sirif -c "DELETE FROM resultado_riesgo;"
$token = (curl.exe -sk -X POST https://localhost:9443/token -d "username=admin&password=sirif2026" | ConvertFrom-Json).access_token
curl.exe -sk -H "Authorization: Bearer $token" https://localhost:9443/api/riesgo/todas
```
Debe mostrar `probabilidad`, `version_modelo="rf-clima-region-v2"` y `fuera_ventana_estacional=true` (noviembre está fuera de jul–sep).

## Modelo comunal (experimental)
- Dataset: `datos_externos/conaf/dataset_comunal_diario_valparaiso_2017_2025.csv` (36 comunas, 2017-2025).
- Script: `ml-pipeline/train_comunal.py`; artefacto `risk-service/modelo_riesgo_comunal.pkl`.
- Validación temporal: AUC 0.760, AP 0.124, Brier 0.037.
- Validación espacial: AUC 0.729, AP 0.069, Brier 0.029.
- Umbrales OOF: ALTO 0.07 (top 5 %), MEDIO 0.031 (top 20 %).
- Integrado al flujo operacional para Quilpué, Viña del Mar y Valparaíso; fallback regional si la comuna no está en el bundle.
- La API expone `tipo_estimacion="riesgo_meteorologico_comunal"` y `version_modelo="rf-comunal-clima-v1"`.

## Tests
- `ml-pipeline`: 3 PASS
- `data-service`: 10 PASS
- `risk-service`: 4 PASS
- `api-service`: 5 PASS

## Limitaciones conocidas
- Modelo regional (no distrital); no generaliza a regiones no vistas sin reentrenar.
- Ventana de entrenamiento jul–sep 2026; fuera de ella la salida es extrapolación.
- Calibración solo validada en la ventana de entrenamiento.
- Probabilidad es estimación para condiciones similares a jul–sep 2026, no pronóstico de ocurrencia.