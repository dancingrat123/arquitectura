# -*- coding: utf-8 -*-
"""Descarga la serie diaria de Open-Meteo usada para entrenar el modelo.

Uso:
    python ml-pipeline/descargar_clima.py

Genera `ml-pipeline/openmeteo_entrenamiento.csv` con una fila por region y dia.
La fuente es ecmwf_ifs025, la misma que usa la ingesta en produccion.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
URL = "https://archive-api.open-meteo.com/v1/archive"
INICIO = "2026-06-01"
FIN = "2026-09-28"
DAILY = (
    "temperature_2m_mean,temperature_2m_max,relative_humidity_2m_mean,"
    "wind_speed_10m_max,precipitation_sum"
)
SALIDA = BASE / "openmeteo_entrenamiento.csv"


def _get(params: dict, reintentos: int = 3) -> dict:
    query = urllib.parse.urlencode(params, safe=",")
    for intento in range(reintentos):
        try:
            with urllib.request.urlopen(f"{URL}?{query}", timeout=90) as respuesta:
                return json.load(respuesta)
        except Exception:
            if intento == reintentos - 1:
                raise
            time.sleep(2)
    raise RuntimeError("descarga fallida")


def main() -> None:
    emas = pd.read_csv(BASE / "emas.csv")
    coords = (
        emas.groupby("region_nombre")[["ema_f_latitudwgs84", "ema_f_longitudwgs84"]]
        .mean()
        .reset_index()
    )
    filas: list[dict] = []
    for _, region in coords.iterrows():
        payload = _get(
            {
                "latitude": f"{region['ema_f_latitudwgs84']:.4f}",
                "longitude": f"{region['ema_f_longitudwgs84']:.4f}",
                "daily": DAILY,
                "start_date": INICIO,
                "end_date": FIN,
                "timezone": "UTC",
                "models": "ecmwf_ifs025",
            }
        )
        daily = payload["daily"]
        for i, fecha in enumerate(daily["time"]):
            filas.append(
                {
                    "region_emas": region["region_nombre"],
                    "region_nombre": region["region_nombre"],
                    "fecha": fecha,
                    "TEMP_MED": daily["temperature_2m_mean"][i],
                    "TEMP_MAX": daily["temperature_2m_max"][i],
                    "HUMEDAD_REL": daily["relative_humidity_2m_mean"][i],
                    "VEL_VIENTO": daily["wind_speed_10m_max"][i],
                    "PRECIPITACION": daily["precipitation_sum"][i],
                }
            )

    df = pd.DataFrame(filas).sort_values(["region_emas", "fecha"]).reset_index(drop=True)
    df.to_csv(SALIDA, index=False, encoding="utf-8")
    print(f"regiones={df['region_emas'].nunique()} filas={len(df)} -> {SALIDA}")
    print(f"nulos={int(df[['TEMP_MED', 'TEMP_MAX', 'HUMEDAD_REL', 'VEL_VIENTO', 'PRECIPITACION']].isna().sum().sum())}")


if __name__ == "__main__":
    main()
