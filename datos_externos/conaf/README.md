# Dataset CONAF de incendios (2010–2025)

Generado con `construir_dataset.py` a partir de los SHP de puntos georreferenciados
de IDE Minagri (CONAF).

## Archivos

| Archivo | Contenido | Filas |
|---|---|---|
| `incendios_conaf_2010_2025.csv` | Todos los eventos descargados; `fecha` puede ser nula en temporadas sin fecha. | 91.802 |
| `incendios_conaf_diario_2017_2025.csv` | Eventos con fecha válida (base diaria). | 46.241 |
| `incendios_conaf_valparaiso_2010_2025.csv` | Eventos de la región de Valparaíso (todos). | 10.266 |
| `incendios_conaf_valparaiso_diario_2017_2025.csv` | Eventos de Valparaíso con fecha válida. | 3.685 |
| `incendios_comunal_diario_valparaiso_2017_2025.csv` | Agregado diario por comuna (solo días con incendios). | 3.166 |
| `incendios_comunal_diario_valparaiso_2017_2025_zeros.csv` | Grilla completa comuna-día con ceros y `y`. | 103.428 |
| `dataset_comunal_diario_valparaiso_2017_2025.csv` | Dataset combinado con clima diario (Open-Meteo ERA5). | 103.428 |

## Columnas principales (eventos)

- `fecha`, `fecha_fin`: fecha/hora de inicio y extinción.
- `temporada`: temporada CONAF (ej. `2024-2025`).
- `region`, `region_norm`, `provincia`, `comuna`, `comuna_norm`.
- `lat`, `lon`: coordenadas WGS84 (transformadas desde UTM).
- `utm_e`, `utm_n`, `huso`: coordenadas originales.
- `superficie`: hectáreas afectadas.
- `causa`, `causa_especifica`, `combustible`.
- `fuente`: `CONAF`.

## Columnas del agregado diario

- `fecha`, `region_norm`, `comuna_norm`, `comuna`.
- `lat`, `lon`: centroide aproximado de los eventos de la comuna.
- `n_incendios`, `superficie_total`, `causa_principal`.
- `y`: 1 si hubo al menos un incendio ese día en la comuna.

## Dataset combinado con clima

`dataset_comunal_diario_valparaiso_2017_2025.csv` agrega a la grilla comuna-día:

- `TEMP_MED`, `TEMP_MAX`, `HUMEDAD_REL`, `VEL_VIENTO`, `PRECIPITACION`
- `precip_7d`, `precip_30d`, `temp_media_7d`, `humedad_7d`

El clima histórico se descargó de Open-Meteo Archive con `models=era5` (2017-2025).
Se usa ERA5 porque `ecmwf_ifs025` no tiene cobertura histórica en el archivo;
la ingesta operacional del proyecto sigue usando `ecmwf_ifs025`.

## Cobertura temporal

- **Con fecha diaria:** 2017-07 a 2025-06 (temporadas 2017-2018 a 2024-2025).
- Las temporadas 2010-2011 a 2016-2017 y 2018-2019 **no traen fecha** en el SHP;
  se conservan en el archivo de eventos pero no en la base diaria.

## Fuente

- IDE Minagri – Localización de Incendios Forestales (CONAF):
  https://ide.minagri.gob.cl/descarga-de-capas-shp/desastres/

## Reproducir

```powershell
cd datos_externos/conaf
python construir_dataset.py
```
