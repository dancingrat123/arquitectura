# Diseño: reentrenamiento honesto del modelo de riesgo

Fecha: 2026-11-18
Rama: `feature/ingesta-real`
Estado: implementado

## 1. Objetivo

Reemplazar el modelo actual por uno cuyas predicciones sean defendibles ante la
comisión: entrenado con clima real diario, sin lags de incendios que conviertan
la salida en un *nowcast*, validado temporalmente y con métricas honestas
publicadas. Se mantiene **Random Forest** por restricción del proyecto. La UI
debe dejar de presentar el resultado como "predicción de incendios" y pasar a
"riesgo meteorológico estimado".

## 2. Evidencia que motiva el cambio

El modelo actual (Random Forest, 9 features) obtiene accuracy 0.944, pero esa
cifra no es defendible:

| Hecho medido | Resultado |
|---|---|
| "Hoy = ayer" (persistencia de la clase) | acierta 98.4 % |
| Clima real + actividad reciente | 83.8 % — peor que persistencia |
| `clima.csv` como fuente | snapshot único (2026-09-28 09:00); 1 valor por región para 90 días |
| 16 regiones con clima | 16 tuplas climáticas distintas → el clima funciona como identificador de región |
| Sensibilidad del modelo actual | 40 °C / 15 % HR → ALTO 0.64; 16 °C / 79 % HR → ALTO 0.92 (dirección invertida) |
| `puntaje` | mapeo fijo ALTO→15, MEDIO→7, BAJO→3 |

Conclusión: el modelo actual no aprendió meteorología; aprendió la persistencia
de una serie muy autocorrelacionada y la identidad de cada región. El
reentrenamiento no es "el mismo modelo con otros datos", sino cambiar qué se
afirma.

## 3. Qué va a afirmar el nuevo modelo

**Sí:**

- "Probabilidad estimada de que un día tenga actividad alta (CONAF) dadas las
  condiciones meteorológicas de hoy y la tasa base histórica de la región."
- Discriminación medida con validación temporal: AUC 0.662, AP 0.523.
- El clima aporta sobre la tasa base regional: AUC 0.662 vs 0.632.
- La dirección del efecto climático es correcta y está garantizada por
  restricciones monótonas: frío/húmedo 0.024 → templado 0.198 → calor seco
  0.571.

**No:**

- No predice el número de incendios ni la ocurrencia de un incendio concreto.
- No predice por comuna ni por distrito. Es un modelo regional; las tres comunas
  comparten la tasa base de Valparaíso y solo se diferencian por el clima de su
  coordenada.
- No generaliza limpiamente a regiones no vistas: con clima solo,
  *leave-one-region-out* da AUC comparativa entre regiones de 0.615 y AUC dentro
  de cada región retenida de 0.734. Una región nueva exige reentrenar.
- No modela estacionalidad: la ventana de entrenamiento es jul–sep 2026. Fuera
  de esa ventana la salida es una extrapolación y la UI debe declararlo.
- La calibración fuera de septiembre no está validada (ver §7.3).

## 4. Datos

| Fuente | Uso |
|---|---|
| `ml-pipeline/conaf.csv` | Objetivo: 16 regiones × 90 días (2026-07-01 a 2026-09-28) |
| `ml-pipeline/emas.csv` | Coordenadas: promedio de estaciones EMAS por región |
| Open-Meteo histórico `ecmwf_ifs025` | Clima diario por región, 2026-06-01 a 2026-09-28 |
| `ml-pipeline/clima.csv` | **No se usa para entrenar**. Solo línea base EMAS del primer ciclo de ingesta |

La serie de Open-Meteo se versiona en el repositorio
(`ml-pipeline/openmeteo_entrenamiento.csv`, 16 regiones × 120 días) para que el
entrenamiento sea reproducible sin red. Se agrega una excepción explícita en
`.gitignore`.

Se verificó que `archive-api.open-meteo.com` y `api.open-meteo.com` con
`models=ecmwf_ifs025` devuelven **valores idénticos** para las mismas fechas. Por
eso se entrena y se infiere con la misma fuente, sin sesgo entre ambas.

Los nombres de región se cruzan con `parsers.normalizar_texto` (Unicode, sin
acentos) más un alias explícito:

```
"la araucania"                              -> "araucania"
"aysen del general carlos ibanez del campo" -> "aysen"
"magallanes y de la antartica chilena"      -> "magallanes"
"metropolitana de santiago"                 -> "metropolitana"
"libertador general bernardo o'higgins"     -> "o'higgins"
```

Esta normalización es obligatoria: un reemplazo literal de strings dejó 3
regiones fuera del cruce durante el experimento.

## 5. Objetivo y features

### 5.1 Objetivo

Binario, por región y día:

```python
y = 1 si (nro_inc_act > 5) or (sup_afec_act > 100)   # actividad alta
y = 0 en caso contrario
```

Prevalencia: 8.8 % (127/1440). Es el mismo corte que la clase ALTO actual, pero
se entrena como clasificación binaria para no fingir tres niveles ordenados.

### 5.2 Features (solo clima)

| Feature | Definición |
|---|---|
| `TEMP_MED` | temperatura media diaria (°C) |
| `TEMP_MAX` | temperatura máxima diaria (°C) |
| `HUMEDAD_REL` | humedad relativa media diaria (%) |
| `VEL_VIENTO` | viento máximo diario (km/h) |
| `PRECIPITACION` | precipitación del día (mm) |
| `precip_7d` | precipitación acumulada 7 días |
| `precip_30d` | precipitación acumulada 30 días |
| `temp_media_7d` | temperatura media de 7 días |
| `humedad_7d` | humedad media de 7 días |

Más 16 indicadores de región (one-hot), que codifican la **tasa base histórica**
de cada región. No se usa `mes` (no es identificable con 3 meses y no extrapola a
noviembre), ni `nro_inc_ant` / `sup_afec_ant` (convierten el modelo en
persistencia y pierden contra "hoy = ayer").

## 6. Modelo

```
CalibratedClassifierCV(
  RandomForestClassifier(
    n_estimators=500,
    min_samples_leaf=5,
    class_weight=None,
    monotonic_cst=[+1, +1, -1, 0, -1, -1, +1, -1, -1, 0, ..., 0],
    random_state=42
  ),
  method="sigmoid",
  cv=StratifiedKFold(5, shuffle=True, random_state=42)
)
```

Las restricciones monótonas se aplican a las 9 features de clima:

| Feature | Dirección permitida |
|---|---|
| `TEMP_MED`, `TEMP_MAX`, `temp_media_7d` | el riesgo no puede bajar al subir la temperatura |
| `HUMEDAD_REL`, `humedad_7d` | el riesgo no puede subir al subir la humedad |
| `PRECIPITACION`, `precip_7d`, `precip_30d` | el riesgo no puede subir al subir la lluvia |
| `VEL_VIENTO` | sin restricción (efecto no monotónico en los datos) |

Esto garantiza que el modelo no repita la inversión del modelo actual: con las
mismas features regionales, la probabilidad pasa de 0.024 (frío/húmedo) a 0.198
(templado) y 0.571 (calor seco). El caso extremo (40 °C, 15 %, 50 km/h) da 0.563,
prácticamente el mismo valor que "calor seco": el modelo satura, no se invierte.

### 6.1 Por qué Random Forest y no otro modelo

Se midió una regresión logística como referencia. Obtuvo mejor AUC (0.788 vs
0.662), pero la restricción del proyecto exige Random Forest. Dentro de RF, la
variante con monotonía es la que mejor combina discriminación, calibración y
dirección correcta:

| Modelo (temporal: jul–ago → sep) | AUC | AP | Brier |
|---|---|---|---|
| **RF monotónico + sigmoid (elegido)** | **0.662** | **0.523** | **0.191** |
| RF sin monotonía, `leaf=5` | 0.669 | 0.512 | 0.191 |
| RF monotónico con `class_weight=balanced` | 0.659 | 0.521 | 0.162* |
| RF monotónico + tasa base numérica | 0.669 | 0.503 | 0.184 |
| Logística (referencia, no permitida) | 0.788 | 0.615 | 0.197 |

\* El Brier de `class_weight=balanced` es artificialmente bajo porque septiembre
tuvo 23.7 % de positivos y el balanceo infla las probabilidades. Se descarta:
con `class_weight=None` la probabilidad refleja la frecuencia real (8.8 %) y
luego el calibrador sigmoid corrige el sesgo del bosque.

### 6.2 Umbrales

Se fijan con la distribución out-of-fold del modelo elegido y se guardan en el
artefacto:

| Nivel | Regla |
|---|---|
| ALTO | `probabilidad >= 0.20` |
| MEDIO | `0.08 <= probabilidad < 0.20` |
| BAJO | `probabilidad < 0.08` |

En OOF, el umbral 0.20 deja ~8 % de días en ALTO con precision 0.59 y recall
0.57 (vs prevalencia 8.8 %). Configurables por `UMBRAL_ALTO` y `UMBRAL_MEDIO`.
No se ajustan con el conjunto temporal de prueba.

`puntaje` deja de ser un mapeo fijo: `round(probabilidad * 15)`, con máximo 15.
Así el puntaje es monótono con la probabilidad y no una etiqueta disfrazada.

### 6.3 Ventana estacional y rango

El artefacto guarda los meses vistos (7, 8, 9) y el rango de cada feature. En
inferencia:

- Si el mes actual no está en la ventana → `fuera_ventana_estacional = true`.
- Si alguna feature cae fuera del rango de entrenamiento → `fuera_de_rango = true`.

Ninguna de las dos invalida la respuesta, pero la UI debe mostrar la advertencia.
Con el clima de noviembre de 2026, las 9 features caen dentro del rango
observado (18.8 °C, 68 %, 18.4 km/h, 60 mm en 30 días); aun así el mes está
fuera de la ventana y debe declararse.

## 7. Validación y métricas

### 7.1 Validación temporal (la métrica principal)

Entrenamiento 2026-07-01 a 2026-08-31; prueba septiembre 2026 (448 filas, 106
positivos):

| Métrica | Modelo | Tasa base regional |
|---|---|---|
| AUC | 0.662 | 0.632 |
| AP | 0.523 | — |
| Brier | 0.191 | 0.201 |

El clima mejora la discriminación sobre la tasa base, pero la calibración es
solo marginalmente mejor. Se reporta así, sin redondear a "bueno".

### 7.2 Fuera de región

Con clima solo (sin indicadores de región) y *leave-one-region-out* sobre las 16
regiones, se miden dos cosas distintas:

| Lectura | AUC | Qué mide |
|---|---|---|
| Media dentro de cada región retenida | 0.734 | Si el clima ordena los días de una región que nunca vio |
| *Pooled* sobre todas las predicciones retenidas | 0.615 | Si el modelo ordena regiones entre sí sin conocerlas |

El clima conserva señal, pero no reemplaza la tasa base regional: la lectura
*pooled* queda por debajo del AUC de la tasa base en septiembre (0.632) y muy por
debajo del modelo con identidad de región. Además la lectura *pooled* incluye
julio, donde ningún día fue positivo y la separación es fácil, así que **no es
comparable** con la AUC temporal de §7.1 (solo septiembre). Por eso los
indicadores de región son parte del modelo y por eso una región nueva exige
reentrenar en lugar de confiar en la extrapolación.

### 7.3 Calibración

Dos tablas, porque miden cosas distintas.

**Temporal (jul–ago → sep)**, donde el ideal es `p_media ≈ tasa_real`:

| Quintil | p media | Tasa real |
|---|---|---|
| 1 | 0.003 | 0.200 |
| 2 | 0.017 | 0.101 |
| 3 | 0.044 | 0.222 |
| 4 | 0.128 | 0.247 |
| 5 | 0.517 | 0.411 |

**Out-of-fold con 5 folds aleatorios** (optimista, solo para umbrales):

| Quintil | p media | Tasa real |
|---|---|---|
| 1 | 0.024 | 0.000 |
| 2 | 0.028 | 0.000 |
| 3 | 0.035 | 0.031 |
| 4 | 0.056 | 0.080 |
| 5 | 0.305 | 0.330 |

La calibración temporal es pobre en los quintiles bajos porque julio no tuvo
ningún día de actividad alta (0/496) y la prevalencia de entrenamiento fue
2.1 %, mientras septiembre tuvo 23.7 %. **No se puede calibrar para una estación
que no se observó.** El modelo final entrena con jul–sep completos (prevalencia
8.8 %) y queda calibrado a esa distribución; la UI debe decir "estimación para
condiciones similares a jul–sep 2026", no "probabilidad real de incendio".

El AUC 0.904 de validación cruzada aleatoria no se usa como titular: es
optimista porque mezcla días de la misma región y la actividad es persistente.

### 7.4 Lo que no se medirá

- Accuracy de 3 clases (elimina el problema de la clase mayoritaria y la
  inflación por persistencia).
- Comparación contra persistencia como si fuera un pronóstico: la persistencia
  no es un pronóstico y ya se documentó que gana (98.4 %).
- Métricas por comuna: no existen datos CONAF comunales.

## 8. Contrato de salida de `risk-service`

```json
{
  "nivel_riesgo": "ALTO",
  "probabilidad": 0.340,
  "probabilidad_alto": 0.340,
  "umbral_alto": 0.20,
  "umbral_medio": 0.08,
  "puntaje": 5,
  "puntaje_maximo": 15,
  "version_modelo": "rf-clima-region-v2",
  "tipo_estimacion": "riesgo_meteorologico_regional",
  "region": "Valparaíso",
  "region_conocida": true,
  "fuera_ventana_estacional": true,
  "fuera_de_rango": false,
  "factores_considerados": { "...": "..." },
  "advertencia": "Estimación de riesgo meteorológico regional; no es pronóstico de ocurrencia."
}
```

El artefacto `modelo_riesgo.pkl` pasa a ser un diccionario:

```python
{
  "modelo": pipeline_entrenado,          # CalibratedClassifierCV
  "features": [ ...9 features... ],
  "regiones": [ ...16 slugs... ],
  "monotonic_cst": [ ...25 valores... ],
  "umbrales": {"alto": 0.20, "medio": 0.08},
  "meses_entrenamiento": [7, 8, 9],
  "rango_features": {feature: [min, max]},
  "metadata": { ... }
}
```

## 9. Cambios por componente

### 9.1 `ml-pipeline/train_honesto.py` (nuevo)

1. Lee `openmeteo_entrenamiento.csv`; si no existe y se pasa `--actualizar`,
   descarga `ecmwf_ifs025` desde `archive-api.open-meteo.com`.
2. Cruza con `conaf.csv` y `emas.csv` usando normalización Unicode + alias.
3. Calcula las 9 features con rodadura por región.
4. Entrena el RF monotónico calibrado y calcula:
   - validación temporal jul–ago → sep,
   - *leave-one-region-out* con clima solo,
   - tabla de calibración temporal y OOF,
   - umbrales OOF.
5. Verifica la monotonía con el perfil frío → templado → calor seco → extremo.
6. Guarda `risk-service/modelo_riesgo.pkl` y `ml-pipeline/model_metadata.json`.

### 9.2 `data-service`

- `parsers.py`: reemplazar `construir_features` por
  `construir_features_clima(serie_diaria)`, que devuelve las 9 features.
  `parse_openmeteo` pasa a leer el bloque `daily` (no `current`).
- `ingestion.py`:
  - sincronizar con `daily`, `past_days=31`, `forecast_days=1`,
    `models=ecmwf_ifs025`, `timezone=UTC`.
  - persistir en `variable_ambiental` los agregados diarios del último día
    (`temperatura` = media, `humedad` = media, `velocidad_viento` = máximo,
    `precipitacion` = suma) y las 9 features en `features JSONB`.
  - el fallback si Open-Meteo falla se mantiene: no se inserta nada y se
    conserva el último registro válido.
- `db.py`: `ALTER TABLE variable_ambiental ADD COLUMN IF NOT EXISTS features
  JSONB`; `ultima_medicion` devuelve `features`; agregar `probabilidad FLOAT` a
  `resultado_riesgo`.
- `main.py`: `_leer_zona` devuelve `region` y las features persistidas; si no
  hay features (registro EMAS antiguo), las deriva del último dato y marca
  `calidad='degradado'`.

### 9.3 `risk-service`

- Cargar el bundle, validar `features`, `regiones`, `monotonic_cst` y umbrales.
- Construir la fila: features + one-hot de `region` (normalizada).
- `predict_proba`, umbrales, `puntaje` derivado y advertencias.
- Si `region` no está en el bundle: `region_conocida=false`, usar el intercepto
  global y advertir.
- Mantener compatibilidad con payloads sin `features` (derivar con valores
  neutros y marcar `calidad='degradado'`).
- `GET /` reporta `version_modelo`, métricas y limitaciones.

### 9.4 `api-service`

- Insertar `probabilidad` en `resultado_riesgo`.
- Exponer `probabilidad`, `tipo_estimacion` y `fuera_ventana_estacional` en
  `/api/riesgo/todas` y `/api/riesgo/{zona}`.
- Incluir `probabilidad` en el evento `riesgo.calculado`.
- Sin cambios en autenticación ni en el consumo de RabbitMQ.

### 9.5 `frontend`

- En la lista: `ALTO · 34 %` (probabilidad) en lugar de `Puntaje: 15/15`.
- En el popup del distrito: "Riesgo meteorológico comunal (heredado del modelo
  regional)" y la probabilidad; aclarar que no es inferencia distrital.
- Etiqueta de sección: "Riesgo meteorológico por zona".
- Aviso visible: "Estimación del modelo para condiciones similares a jul–sep
  2026. No es pronóstico de ocurrencia."
- Si `fuera_ventana_estacional`, mostrar "Fuera de la ventana estacional de
  entrenamiento".

### 9.6 Documentación

- Este spec.
- `README.md`: nueva sección de modelo, métricas, limitaciones y cómo
  reentrenar.
- `ml-pipeline/model_metadata.json` versionado con métricas, umbrales, fecha,
  commit y fuentes.

## 10. Reproducibilidad

- `ml-pipeline/openmeteo_entrenamiento.csv` versionado (excepción en
  `.gitignore`).
- `train_honesto.py` no requiere red si el CSV existe.
- `model_metadata.json` registra fecha, commit, fuentes, métricas y umbrales.
- El `.pkl` se regenera con un comando documentado y se compara con el
  metadata.

## 11. Riesgos y limitaciones

| Riesgo | Mitigación |
|---|---|
| 90 días de datos y actividad concentrada en septiembre | Declarar la ventana; no prometer estacionalidad; reentrenar cuando haya más historia |
| Probabilidad calibrada solo en jul–sep | UI rotula "estimación"; `fuera_ventana_estacional` |
| Las 3 comunas dan casi el mismo valor | Declarar que es regional; el distrito hereda el riesgo comunal |
| Región nueva sin indicador | `region_conocida=false`, usar intercepto global y advertir |
| `class_weight` infla probabilidades | Se usa `class_weight=None` + calibración sigmoid |
| RF podría invertir el efecto climático | Restricciones monótonas verificadas en entrenamiento y con perfil de sensibilidad |
| Open-Meteo cambia de modelo | Fijar `ecmwf_ifs025`; el metadata registra el modelo usado |
| El clima de una coordenada no representa la comuna | Documentar; es la misma limitación que ya tiene la ingesta |

## 12. Criterios de aceptación

1. `train_honesto.py` corre sin red con el CSV versionado y regenera el `.pkl`
   de forma determinista (`random_state=42`).
2. El artefacto es un `RandomForestClassifier` (calibrado) con
   `monotonic_cst`; no se reemplaza por otro algoritmo.
3. El metadata contiene AUC/AP/Brier temporal, AUC *leave-one-region-out*,
   tabla de calibración, umbrales y limitaciones.
4. `POST /datos/sincronizar` persiste 9 features por zona y sigue conservando el
   último registro válido si Open-Meteo falla.
5. `risk-service` devuelve `probabilidad` y `nivel_riesgo` coherentes con los
   umbrales; el `puntaje` es `round(probabilidad * 15)`.
6. El perfil de sensibilidad frío → templado → calor seco es creciente y no se
   invierte en el extremo.
7. `/api/riesgo/todas` y la UI muestran probabilidad, etiqueta de riesgo
   meteorológico y advertencia.
8. La UI no muestra "Puntaje: 15/15" ni sugiere inferencia distrital.
9. Playwright verifica lista, popup y colores con el stack completo.
10. `README.md` y el spec describen métricas y limitaciones sin exagerar.

## 13. Verificación end-to-end

1. `docker compose up -d --build`.
2. Esperar `healthy` y una sincronización.
3. Consultar `data-service` y comprobar `features` de 9 claves y `region`.
4. Consultar `/api/riesgo/todas` y verificar `probabilidad` y `version_modelo`.
5. Playwright: lista, popup, disclaimer y 7 polígonos.
6. Fallback: `OPEN_METEO_URL` inválida → `ok=false`, filas antes/después iguales,
   `ingesta_control` en `error` y `ultimo_exito` preservado.
7. Reentrenar desde cero y comparar el `.pkl`/metadata.

## 14. Fuera de alcance

- Datos CONAF comunales o distritales (no existen en el dataset).
- Modelos de series de tiempo o pronóstico de número de incendios.
- Estacionalidad más allá de jul–sep 2026.
- Nuevos microservicios o cambios de arquitectura.
- Autenticación, TLS o gateway.
