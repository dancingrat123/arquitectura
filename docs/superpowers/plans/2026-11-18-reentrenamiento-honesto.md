# Reentrenamiento honesto del modelo de riesgo — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el modelo de riesgo por un Random Forest con clima diario real, restricciones monótonas y probabilidad calibrada, y propagar esa probabilidad por ingesta, API y UI con métricas honestas.

**Architecture:** `ml-pipeline/train_honesto.py` entrena un `RandomForestClassifier` monotónico calibrado con sigmoid sobre 9 features de clima diario + 16 indicadores de región, y guarda un bundle en `risk-service/modelo_riesgo.pkl` y métricas en `ml-pipeline/model_metadata.json`. `data-service` descarga 31 días diarios de Open-Meteo `ecmwf_ifs025`, calcula las features y las persiste como JSONB. `risk-service` construye la fila con la región y devuelve probabilidad, nivel y advertencias. `api-service` persiste y expone la probabilidad; el frontend la muestra como riesgo meteorológico.

**Tech Stack:** Python 3.11, scikit-learn, pandas, joblib, FastAPI, PostgreSQL/PostGIS, Docker Compose, JavaScript vanilla.

**Spec:** `docs/superpowers/specs/2026-11-18-reentrenamiento-honesto.md`

## Global Constraints

- Se mantiene **Random Forest** (`RandomForestClassifier`). No se reemplaza por logística ni por otro algoritmo.
- El artefacto debe incluir `monotonic_cst` con: `TEMP_MED +1`, `TEMP_MAX +1`, `temp_media_7d +1`, `HUMEDAD_REL -1`, `humedad_7d -1`, `PRECIPITACION -1`, `precip_7d -1`, `precip_30d -1`, `VEL_VIENTO 0`.
- Hiperparámetros: `n_estimators=500`, `min_samples_leaf=5`, `class_weight=None`, `random_state=42`.
- Calibración: `CalibratedClassifierCV(method="sigmoid", cv=StratifiedKFold(5, shuffle=True, random_state=42))`.
- Umbrales: ALTO `>= 0.20`, MEDIO `>= 0.08`, BAJO `< 0.08`.
- `puntaje = round(probabilidad * 15)`, máximo 15. Nunca un mapeo fijo.
- Ventana de entrenamiento: meses 7, 8 y 9 de 2026.
- Open-Meteo: `models=ecmwf_ifs025`, `daily`, `past_days=31`, `forecast_days=1`, `timezone=UTC`.
- No se agregan microservicios ni se toca autenticación, TLS o gateway.
- Todo el texto de UI y documentación va en español.
- Fechas en BD UTC naive; serialización ISO con `Z`.

## Review Focus

- Open-Meteo devuelve menos de 30 días o un día con `null`: las features rodadas no deben producir `NaN` ni romper el sync; la fila se marca `calidad='degradado'`.
- Región con acentos o alias (`La Araucanía`, `Aysén del General...`, `Metropolitana de Santiago`): debe mapear al slug del modelo; una región desconocida no debe lanzar excepción.
- Registro EMAS antiguo sin `features` JSONB: `data-service` y `risk-service` deben seguir respondiendo, marcando calidad degradada.
- Falla de Open-Meteo: no debe insertar filas parciales; el último registro válido y `ultimo_exito` se conservan.
- Coherencia de salida: `nivel_riesgo` corresponde a los umbrales, `puntaje = round(probabilidad*15)` y la UI nunca muestra "15/15" fijo.

---

### Task 1: Versionar la serie de clima y excluirla de `.gitignore`

**Files:**
- Create: `ml-pipeline/openmeteo_entrenamiento.csv`
- Modify: `.gitignore`
- Create: `ml-pipeline/descargar_clima.py`

**Interfaces:**
- Produces: CSV con columnas `region_emas,region_nombre,fecha,TEMP_MED,TEMP_MAX,HUMEDAD_REL,VEL_VIENTO,PRECIPITACION`, 16 regiones × 120 días (2026-06-01 a 2026-09-28).

- [ ] **Step 1: Crear `ml-pipeline/descargar_clima.py`**

Debe leer `emas.csv`, agrupar por `region_nombre` promediando `ema_f_latitudwgs84` y `ema_f_longitudwgs84`, y descargar por región desde `https://archive-api.open-meteo.com/v1/archive` con `models=ecmwf_ifs025`, `daily=temperature_2m_mean,temperature_2m_max,relative_humidity_2m_mean,wind_speed_10m_max,precipitation_sum`, `start_date=2026-06-01`, `end_date=2026-09-28`, `timezone=UTC`. Escribir el CSV en UTF-8 sin BOM, ordenado por `region_emas, fecha`.

- [ ] **Step 2: Ejecutarlo y verificar la cobertura**

Run: `python ml-pipeline/descargar_clima.py`
Expected: `regiones=16 filas=1920`, sin valores nulos en las 5 variables.

- [ ] **Step 3: Agregar la excepción en `.gitignore`**

Después de `*.csv`, agregar:

```gitignore
!ml-pipeline/openmeteo_entrenamiento.csv
```

- [ ] **Step 4: Verificar que git lo ve**

Run: `git check-ignore -v ml-pipeline/conaf.csv ml-pipeline/openmeteo_entrenamiento.csv`
Expected: `conaf.csv` ignorado; `openmeteo_entrenamiento.csv` **no** ignorado (sin salida para ese archivo).

- [ ] **Step 5: Commit**

```bash
git add .gitignore ml-pipeline/descargar_clima.py ml-pipeline/openmeteo_entrenamiento.csv
git commit -m "chore: versiona la serie diaria de Open-Meteo para entrenamiento"
```

---

### Task 2: Pipeline de entrenamiento y artefacto

**Files:**
- Create: `ml-pipeline/train_honesto.py`
- Create: `ml-pipeline/test_train_honesto.py`
- Create (generado): `risk-service/modelo_riesgo.pkl`
- Create (generado): `ml-pipeline/model_metadata.json`

**Interfaces:**
- Produces:
  - `normalizar_region(valor: str) -> str`
  - `construir_features_clima(serie: list[dict]) -> dict[str, float]`
  - `construir_matriz(df: pandas.DataFrame) -> tuple[pandas.DataFrame, list[str], list[str]]`
  - `entrenar() -> dict` (bundle)
  - Bundle: `{"modelo", "features", "regiones", "monotonic_cst", "umbrales", "meses_entrenamiento", "rango_features", "metadata"}`.

- [ ] **Step 1: Escribir los tests de helpers**

En `ml-pipeline/test_train_honesto.py`:

```python
import numpy as np
import pandas as pd
import train_honesto as t


def test_normalizar_region_aliases():
    assert t.normalizar_region("La Araucanía") == "araucania"
    assert t.normalizar_region("Aysén del General Carlos Ibáñez del Campo") == "aysen"
    assert t.normalizar_region("Metropolitana de Santiago") == "metropolitana"
    assert t.normalizar_region("Libertador General Bernardo O'Higgins") == "o'higgins"
    assert t.normalizar_region("Valparaíso") == "valparaiso"


def test_features_rodadura():
    serie = [
        {"fecha": pd.Timestamp("2026-09-01") + pd.Timedelta(days=i),
         "TEMP_MED": 10.0 + i, "TEMP_MAX": 15.0 + i, "HUMEDAD_REL": 80 - i,
         "VEL_VIENTO": 5.0, "PRECIPITACION": 1.0}
        for i in range(31)
    ]
    f = t.construir_features_clima(serie)
    assert f["TEMP_MED"] == 40.0
    assert f["precip_7d"] == 7.0
    assert f["precip_30d"] == 30.0
    assert abs(f["temp_media_7d"] - np.mean([34, 35, 36, 37, 38, 39, 40])) < 1e-6


def test_features_con_menos_de_30_dias_no_falla():
    serie = [{"fecha": pd.Timestamp("2026-09-28"), "TEMP_MED": 12.0,
              "TEMP_MAX": 18.0, "HUMEDAD_REL": 70.0, "VEL_VIENTO": 3.0,
              "PRECIPITACION": 0.0}]
    f = t.construir_features_clima(serie)
    assert set(f) == {
        "TEMP_MED", "TEMP_MAX", "HUMEDAD_REL", "VEL_VIENTO", "PRECIPITACION",
        "precip_7d", "temp_media_7d", "humedad_7d", "precip_30d",
    }
    assert all(np.isfinite(v) for v in f.values())
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `cd ml-pipeline; python -m pytest test_train_honesto.py -v`
Expected: FAIL con `ModuleNotFoundError: train_honesto`.

- [ ] **Step 3: Implementar `train_honesto.py`**

Responsabilidades exactas:

- `normalizar_region`: `unicodedata.normalize("NFKD")`, quitar diacríticos, minúsculas, `strip`, y aplicar aliases:
  `la araucania -> araucania`, `aysen del general carlos ibanez del campo -> aysen`, `magallanes y de la antartica chilena -> magallanes`, `metropolitana de santiago -> metropolitana`, `libertador general bernardo o'higgins -> o'higgins`.
- `construir_features_clima`: ordenar por `fecha`, usar la última fila como día actual, rodaduras con `min_periods=min(ventana, len(serie))` para 7 y 30 días. Nunca devolver `NaN`; si falta un valor, usar 0.0.
- `construir_matriz`: `pd.get_dummies(region_slug, prefix="reg")`, devolver `X`, lista de features de clima y lista de columnas de región.
- `entrenar()`:
  - leer `openmeteo_entrenamiento.csv`, `conaf.csv`, `emas.csv`;
  - calcular `y = (nro_inc_act > 5) | (sup_afec_act > 100)`;
  - entrenar el RF monotónico y el calibrador con los parámetros de `Global Constraints`;
  - calcular validación temporal (`fecha < 2026-09-01`), *leave-one-region-out* con clima solo, tablas de calibración y umbrales OOF;
  - escribir `modelo_riesgo.pkl` y `model_metadata.json` (métricas, umbrales, fecha UTC, commit `git rev-parse --short HEAD`, fuentes y limitaciones del spec §7 y §11);
  - imprimir un resumen con AUC/AP/Brier temporal y el perfil de sensibilidad.
- Guardar `monotonic_cst` como lista alineada con `features + regiones`.

- [ ] **Step 4: Ejecutar tests y entrenamiento**

Run: `cd ml-pipeline; python -m pytest test_train_honesto.py -v`
Expected: 3 PASS.

Run: `cd ml-pipeline; python train_honesto.py`
Expected: `AUC temporal >= 0.60`, `Brier temporal <= 0.22`, perfil `frio < templado < calor_seco`, y artefactos creados.

- [ ] **Step 5: Verificar el artefacto**

Run:

```bash
cd ml-pipeline
python -c "import joblib; b=joblib.load('../risk-service/modelo_riesgo.pkl'); print(type(b['modelo']).__name__, len(b['features']), len(b['regiones']), b['umbrales'])"
```

Expected: `CalibratedClassifierCV 9 16 {'alto': 0.2, 'medio': 0.08}`.

- [ ] **Step 6: Commit**

```bash
git add ml-pipeline/train_honesto.py ml-pipeline/test_train_honesto.py ml-pipeline/model_metadata.json risk-service/modelo_riesgo.pkl
git commit -m "feat: entrena RF monotónico calibrado con clima diario real"
```

---

### Task 3: Parser de clima diario y features

**Files:**
- Modify: `data-service/parsers.py`
- Modify: `data-service/tests/test_parsers.py`

**Interfaces:**
- Produces:
  - `parse_openmeteo_diario(payload: Any) -> list[dict]`, cada item:
    `{"latitud": float, "longitud": float, "serie": list[dict], "valido": bool}`.
  - `construir_features_clima(serie: list[dict]) -> dict[str, float]` con las 9 claves del spec §5.2.
- Consumes: nada.

- [ ] **Step 1: Escribir los tests**

Agregar a `data-service/tests/test_parsers.py`:

```python
def test_parse_openmeteo_diario_lista():
    payload = [{
        "latitude": -33.05, "longitude": -71.44,
        "daily": {
            "time": ["2026-11-17", "2026-11-18"],
            "temperature_2m_mean": [17.4, 18.2],
            "temperature_2m_max": [22.1, 23.0],
            "relative_humidity_2m_mean": [70, 68],
            "wind_speed_10m_max": [15.2, 18.4],
            "precipitation_sum": [0.0, 1.2],
        },
    }]
    [item] = parsers.parse_openmeteo_diario(payload)
    assert item["valido"] is True
    assert len(item["serie"]) == 2
    assert item["serie"][-1]["TEMP_MED"] == 18.2
    assert item["serie"][-1]["PRECIPITACION"] == 1.2


def test_parse_openmeteo_diario_nulos_no_rompe():
    payload = {"latitude": -33.0, "longitude": -71.0,
               "daily": {"time": ["2026-11-18"],
                         "temperature_2m_mean": [None],
                         "temperature_2m_max": [None],
                         "relative_humidity_2m_mean": [None],
                         "wind_speed_10m_max": [None],
                         "precipitation_sum": [None]}}
    [item] = parsers.parse_openmeteo_diario(payload)
    assert item["valido"] is False
    assert item["serie"] == []


def test_construir_features_clima_rodadura():
    serie = [{"fecha": datetime(2026, 9, 1) + timedelta(days=i),
              "TEMP_MED": 10 + i, "TEMP_MAX": 15 + i, "HUMEDAD_REL": 80 - i,
              "VEL_VIENTO": 5.0, "PRECIPITACION": 1.0} for i in range(31)]
    f = parsers.construir_features_clima(serie)
    assert set(f) == {"TEMP_MED", "TEMP_MAX", "HUMEDAD_REL", "VEL_VIENTO",
                      "PRECIPITACION", "precip_7d", "temp_media_7d",
                      "humedad_7d", "precip_30d"}
    assert f["TEMP_MED"] == 40.0
    assert f["precip_7d"] == 7.0
    assert f["precip_30d"] == 30.0
```

Ajustar los imports de `datetime` para incluir `timedelta`.

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `cd data-service; python -m pytest tests/test_parsers.py -v`
Expected: FAIL por funciones inexistentes.

- [ ] **Step 3: Implementar en `parsers.py`**

- `parse_openmeteo_diario`: aceptar objeto o lista. Si falta `daily`, devolver `valido=False, serie=[]`. Si un día tiene algún `None` en las 5 variables, omitirlo de `serie`. Convertir `time` con `_parse_fecha`.
- `construir_features_clima`: misma lógica que `train_honesto.construir_features_clima` (ordenar, última fila, rodaduras con `min_periods=min(ventana, n)`, sin `NaN`).
- Eliminar `parse_openmeteo` y `construir_features` o dejarlos como alias obsoletos solo si otro código los usa; actualizar los tests que los referencian para que prueben las funciones nuevas.

- [ ] **Step 4: Ejecutar tests**

Run: `cd data-service; python -m pytest tests/test_parsers.py -v`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add data-service/parsers.py data-service/tests/test_parsers.py
git commit -m "feat: parsea clima diario y calcula features meteorológicas"
```

---

### Task 4: Ingesta diaria, JSONB y lectura de features

**Files:**
- Modify: `data-service/ingestion.py`
- Modify: `data-service/db.py`
- Modify: `data-service/main.py`
- Test: `data-service/tests/test_parsers.py` (casos de borde de features)

**Interfaces:**
- Consumes: `parsers.parse_openmeteo_diario`, `parsers.construir_features_clima`.
- Produces:
  - `variable_ambiental.features` (JSONB)
  - `resultado_riesgo.probabilidad` (FLOAT)
  - `resultado_riesgo.fuera_ventana_estacional` (BOOLEAN)
  - `GET /datos/{zona}` incluye `region` y `features` (9 claves) y `meta.calidad`.

- [ ] **Step 1: Agregar el DDL**

En `db.py`, dentro de `DDL`, después de las columnas de `variable_ambiental`:

```python
"ALTER TABLE variable_ambiental ADD COLUMN IF NOT EXISTS features JSONB",
```

Después de las columnas de `resultado_riesgo`:

```python
"ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS probabilidad FLOAT",
"ALTER TABLE resultado_riesgo ADD COLUMN IF NOT EXISTS fuera_ventana_estacional BOOLEAN DEFAULT FALSE",
```

- [ ] **Step 2: Actualizar `ultima_medicion`**

Agregar `features` al `SELECT` de `db.ultima_medicion`. No cambiar el orden existente de columnas; agregar `features` al final para no romper índices posicionales de otros consumidores.

- [ ] **Step 3: Reescribir la descarga y persistencia en `ingestion.py`**

- `VARIABLES_OPEN_METEO = "temperature_2m_mean,temperature_2m_max,relative_humidity_2m_mean,wind_speed_10m_max,precipitation_sum"`.
- `PARAMETROS_OPEN_METEO = {"daily": VARIABLES_OPEN_METEO, "past_days": 31, "forecast_days": 1, "timezone": "UTC", "models": "ecmwf_ifs025"}`.
- `_descargar_openmeteo` usa `parse_openmeteo_diario`.
- En `sincronizar`, por zona:
  - `serie = medicion["serie"]`; si `not medicion["valido"] or not serie`, saltar la zona con warning.
  - `features = parsers.construir_features_clima(serie)`.
  - `ultimo = serie[-1]`.
  - `calidad = "ok" if len(serie) >= 30 else "degradado"`.
  - Insertar `temperatura=ultimo["TEMP_MED"]`, `humedad=ultimo["HUMEDAD_REL"]`, `velocidad_viento=ultimo["VEL_VIENTO"]`, `precipitacion=ultimo["PRECIPITACION"]`, `features=json.dumps(features)`, `fecha_medicion=ultimo["fecha"]`, `calidad`.
  - Mantener la trazabilidad con `VARIABLES_OPEN_METEO`.
- Si `registros == 0`, lanzar `IngestaError` (comportamiento actual).
- No insertar filas parciales: todo ocurre dentro de la misma transacción.

- [ ] **Step 4: Actualizar `main.py`**

- `_leer_zona` agrega `"region": fila_zona["region"]` y `"features": medicion.get("features")`.
- Si `features` es `None` (fila EMAS antigua), construir una serie de un día con los valores de la medición, llamar `parsers.construir_features_clima`, y forzar `meta.calidad = "degradado"`.
- `variables` mantiene las claves actuales para no romper el frontend.
- El fallback local usa `construir_features_clima` con una serie de un día.

- [ ] **Step 5: Verificar parseo y DDL**

Run: `cd data-service; python -m pytest tests/ -v`
Expected: PASS.

Run (con el stack arriba):

```bash
docker compose up -d --build data-service
docker compose logs --tail=50 data-service
```

Expected: `Esquema PostGIS verificado` incluye las sentencias nuevas y el bootstrap termina `ok`.

- [ ] **Step 6: Commit**

```bash
git add data-service
git commit -m "feat: sincroniza clima diario y persiste features en JSONB"
```

---

### Task 5: Riesgo con probabilidad calibrada y advertencias

**Files:**
- Modify: `risk-service/main.py`
- Create: `risk-service/test_main.py`

**Interfaces:**
- Consumes: bundle de Task 2; `GET /datos/{zona}` de Task 4.
- Produces: `POST /clasificar` con el contrato del spec §8, incluyendo `probabilidad`, `nivel_riesgo`, `puntaje`, `region_conocida`, `fuera_ventana_estacional`, `fuera_de_rango`, `tipo_estimacion` y `advertencia`.

- [ ] **Step 1: Escribir los tests**

En `risk-service/test_main.py`:

```python
import main


def test_nivel_por_umbrales():
    assert main.nivel_desde_probabilidad(0.40) == "ALTO"
    assert main.nivel_desde_probabilidad(0.20) == "ALTO"
    assert main.nivel_desde_probabilidad(0.199) == "MEDIO"
    assert main.nivel_desde_probabilidad(0.08) == "MEDIO"
    assert main.nivel_desde_probabilidad(0.079) == "BAJO"


def test_puntaje_derivado():
    assert main.calcular_puntaje(0.0) == 0
    assert main.calcular_puntaje(0.34) == 5
    assert main.calcular_puntaje(1.0) == 15


def test_normalizar_region_aliases():
    assert main.normalizar_region("Valparaíso") == "valparaiso"
    assert main.normalizar_region("La Araucanía") == "araucania"


def test_region_desconocida_no_falla():
    fila, conocida = main.construir_fila(
        {f: 1.0 for f in main.FEATURES_CLIMA}, "Región Inventada", ["valparaiso"]
    )
    assert conocida is False
    assert fila.shape == (1, len(main.FEATURES_CLIMA) + 1)
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `cd risk-service; python -m pytest test_main.py -v`
Expected: FAIL por funciones inexistentes.

- [ ] **Step 3: Implementar `risk-service/main.py`**

- `MODEL_VERSION = "rf-clima-region-v2"`.
- `FEATURES_CLIMA` = las 9 features del spec.
- `UMBRAL_ALTO = float(os.getenv("UMBRAL_ALTO", "0.20"))`, `UMBRAL_MEDIO = float(os.getenv("UMBRAL_MEDIO", "0.08"))`.
- `normalizar_region`: misma normalización y aliases que Task 2.
- `nivel_desde_probabilidad(p: float) -> str`.
- `calcular_puntaje(p: float) -> int` con `round(p * 15)`.
- `construir_fila(features: dict, region: str | None, regiones: list[str]) -> tuple[pd.DataFrame, bool]`: columnas = `FEATURES_CLIMA + [f"reg_{r}" for r in regiones]`; one-hot; si la región no está, todas las columnas de región en 0 y `conocida=False`.
- `load_model`: cargar el bundle; aceptar un modelo sklearn antiguo solo para no romper el arranque, marcando `modelo_legacy=True`.
- `clasificar_riesgo`:
  - usar `variables.features` si viene; si no, derivar de `temperatura/humedad/viento/precipitacion` con rodaduras = valores del día y marcar `calidad="degradado"`;
  - `prob = float(modelo.predict_proba(fila)[0, 1])`;
  - `fuera_ventana = datetime.now().month not in meta["meses_entrenamiento"]`;
  - `fuera_rango = any(valor < min or valor > max for feature, valor in features.items())`;
  - devolver el contrato del spec §8.
- `GET /` reporta `version_modelo`, `features`, `umbrales`, `meses_entrenamiento` y `limitaciones`.

- [ ] **Step 4: Ejecutar tests**

Run: `cd risk-service; python -m pytest test_main.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add risk-service/main.py risk-service/test_main.py
git commit -m "feat: inferencia con probabilidad calibrada y advertencias"
```

---

### Task 6: Persistir y exponer `probabilidad` en la API

**Files:**
- Modify: `api-service/main.py`
- Modify: `api-service/test_main.py`

**Interfaces:**
- Consumes: contrato de Task 5.
- Produces: `resultado_riesgo.probabilidad`, `fuera_ventana_estacional`; respuestas de `/api/riesgo/todas` y `/api/riesgo/{zona}` con `probabilidad`, `tipo_estimacion` y `fuera_ventana_estacional`.

- [ ] **Step 1: Escribir el test del contrato**

Agregar a `api-service/test_main.py` un test unitario de serialización sin BD:

```python
def test_serializar_zona_incluye_probabilidad():
    from main import serializar_zona
    fila = ("quilpue", "ALTO", 5, 15, None, "rf-clima-region-v2",
            "Quilpué", "5801", "Open-Meteo", None, None, 0.34, True)
    data = serializar_zona(fila)
    assert data["probabilidad"] == 0.34
    assert data["fuera_ventana_estacional"] is True
    assert data["tipo_estimacion"] == "riesgo_meteorologico_regional"
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `cd api-service; python -m pytest test_main.py -v`
Expected: FAIL por `serializar_zona` inexistente.

- [ ] **Step 3: Implementar en `api-service/main.py`**

- En `inferir_y_guardar`, insertar `probabilidad` y `fuera_ventana_estacional` desde `resultado`.
- Extraer `serializar_zona(fila) -> dict` con el orden de columnas del `SELECT`:
  `zona, nivel_riesgo, puntaje, puntaje_maximo, fecha_evaluacion, version_modelo, comuna, codigo_comuna, fuente, fecha_medicion, antiguedad_minutos, probabilidad, fuera_ventana_estacional`.
  Debe devolver `probabilidad` (float o `None`), `tipo_estimacion="riesgo_meteorologico_regional"` y `fuera_ventana_estacional` (bool).
- `/api/riesgo/todas`: agregar `r.probabilidad`, `r.fuera_ventana_estacional` al `SELECT` y usar `serializar_zona`.
- `/api/riesgo/{zona}`: incluir ambas columnas en la caché y en la consulta on-demand.
- Evento `riesgo.calculado`: agregar `probabilidad`.

- [ ] **Step 4: Ejecutar tests**

Run: `cd api-service; python -m pytest test_main.py -v`
Expected: 5 PASS (4 existentes + el nuevo).

- [ ] **Step 5: Commit**

```bash
git add api-service
git commit -m "feat: persiste y expone probabilidad de riesgo meteorológico"
```

---

### Task 7: UI con probabilidad, etiqueta y advertencia

**Files:**
- Modify: `frontend/index.html`

**Interfaces:**
- Consumes: `/api/riesgo/todas` y `/datos/distritos` de Task 6.

- [ ] **Step 1: Cambiar la etiqueta de la sección**

`Riesgo por zona` → `Riesgo meteorológico por zona`. Agregar bajo el subtítulo:

```html
<p class="disclaimer">Estimación del modelo para condiciones similares a jul–sep 2026. No es pronóstico de ocurrencia.</p>
```

Con estilo `.disclaimer { font-size: 0.75rem; color: #94a3b8; margin: 4px 0 12px; }`.

- [ ] **Step 2: Mostrar probabilidad en la lista**

En `renderizarZonas`, reemplazar la línea del puntaje por:

```js
const prob = (z.probabilidad !== null && z.probabilidad !== undefined)
    ? `${Math.round(z.probabilidad * 100)} %` : 'sin probabilidad';
```

y mostrar `<div class="fuente-tag">Probabilidad: ${prob}</div>`. Si `z.fuera_ventana_estacional`, agregar `<div class="fuente-tag">Fuera de la ventana estacional</div>`.

- [ ] **Step 3: Aclarar la herencia comunal en el popup**

En el popup de cada distrito, cambiar `<b>Riesgo:</b> ${nivel}` por:

```html
<p style="margin: 4px 0;"><b>Riesgo meteorológico:</b> ${nivel} (comunal)</p>
<p style="margin: 4px 0; color: #64748b;">Heredado del modelo regional. No es inferencia distrital.</p>
```

- [ ] **Step 4: Verificar que no queda el puntaje fijo**

Run: `Select-String -Path frontend/index.html -Pattern "Puntaje:|15/15"`
Expected: sin coincidencias.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html
git commit -m "feat: muestra probabilidad y advertencia de riesgo meteorológico"
```

---

### Task 8: Documentación y verificación end-to-end

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-11-18-reentrenamiento-honesto.md` (estado → implementado)

**Interfaces:**
- Consumes: todo lo anterior.

- [ ] **Step 1: Actualizar `README.md`**

Agregar una sección "Modelo de riesgo" con:

- tipo de modelo (RF monotónico calibrado) y features;
- métricas temporales (AUC 0.662, AP 0.523, Brier 0.191 vs base 0.632/0.201);
- umbrales 0.20 / 0.08;
- limitaciones: regional, sin estacionalidad, no distrital, sin generalización a regiones nuevas;
- comando `python ml-pipeline/train_honesto.py`;
- fuentes y reproducibilidad.

- [ ] **Step 2: Levantar el stack y verificar salud**

Run: `docker compose up -d --build`
Expected: 10 contenedores `healthy` (o el mismo número que antes).

- [ ] **Step 3: Verificar ingesta y features**

Run:

```bash
docker compose exec -T data-service python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://localhost:8000/datos/quilpue')); print(d['region'], sorted(d['features']), d['meta']['calidad'])"
```

Expected: `Valparaíso` + 9 claves + `ok` (o `degradado` si aún no hay 30 días).

- [ ] **Step 4: Verificar API**

Obtener token y consultar:

```bash
curl.exe -k -X POST https://localhost:9443/token -d "username=admin&password=sirif2026"
curl.exe -k -H "Authorization: Bearer <token>" https://localhost:9443/api/riesgo/todas
```

Expected: cada zona con `probabilidad`, `nivel_riesgo`, `version_modelo="rf-clima-region-v2"` y `tipo_estimacion`.

- [ ] **Step 5: Verificar UI con Playwright**

Abrir `https://localhost:9443` con `ignoreHTTPSErrors`, comprobar:
- sección "Riesgo meteorológico por zona";
- probabilidad visible en cada zona;
- popup con "comunal" y "No es inferencia distrital";
- 7 polígonos coloreados.

- [ ] **Step 6: Verificar el fallback**

Run:

```bash
docker compose stop data-service
docker compose run --rm -e OPEN_METEO_URL=http://127.0.0.1:9 data-service python -c "import ingestion; print(ingestion.sincronizar())"
```

Expected: `ok=False`, sin filas nuevas, `ingesta_control.estado='error'` y `ultimo_exito` preservado.

- [ ] **Step 7: Commit final**

```bash
git add README.md docs/superpowers/specs/2026-11-18-reentrenamiento-honesto.md
git commit -m "docs: documenta el modelo honesto, métricas y limitaciones"
```

---

## Self-Review

- **Cobertura del spec:** §4 datos → Task 1; §5 features → Tasks 2/3; §6 modelo y umbrales → Task 2/5; §7 métricas → Task 2/8; §8 contrato → Task 5; §9.1 → Task 2; §9.2 → Tasks 3/4; §9.3 → Task 5; §9.4 → Task 6; §9.5 → Task 7; §9.6 → Task 8; §10 → Task 1/2; §11-13 → Task 8.
- **Tipos y nombres:** `construir_features_clima` se usa con la misma firma en `train_honesto.py`, `parsers.py` y `main.py`. `FEATURES_CLIMA` y `normalizar_region` se repiten en `risk-service` con los mismos aliases. El bundle conserva las claves `modelo`, `features`, `regiones`, `monotonic_cst`, `umbrales`, `meses_entrenamiento`, `rango_features`, `metadata`.
- **Review Focus cubierto:** menos de 30 días → Tasks 2/3/4; aliases → Tasks 2/5; fila EMAS sin features → Task 4/5; fallo de Open-Meteo → Task 4/8; coherencia de salida → Tasks 5/6/7.
- **Proporción:** el plan fija interfaces, valores y pruebas; no transcribe los cuerpos completos.
