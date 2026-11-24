# -*- coding: utf-8 -*-
"""Combina el dataset comunal de incendios CONAF con clima diario de Open-Meteo.

Entrada:
  incendios_comunal_diario_valparaiso_2017_2025_zeros.csv
Salida:
  dataset_comunal_diario_valparaiso_2017_2025.csv
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
ENTRADA = BASE / "incendios_comunal_diario_valparaiso_2017_2025_zeros.csv"
SALIDA = BASE / "dataset_comunal_diario_valparaiso_2017_2025.csv"

URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY = (
    "temperature_2m_mean,temperature_2m_max,relative_humidity_2m_mean,"
    "wind_speed_10m_max,precipitation_sum"
)
MODELO_CLIMA = "era5"


def _get(params: dict, reintentos: int = 5) -> dict:
    query = urllib.parse.urlencode(params, safe=",")
    for intento in range(reintentos):
        try:
            with urllib.request.urlopen(f"{URL}?{query}", timeout=120) as respuesta:
                return json.load(respuesta)
        except urllib.error.HTTPError as error:
            if intento == reintentos - 1:
                raise
            espera = 30 if error.code == 429 else 2 ** intento
            print(f"  HTTP {error.code}; reintento en {espera}s")
            time.sleep(espera)
        except Exception:
            if intento == reintentos - 1:
                raise
            time.sleep(2 ** intento)
    raise RuntimeError("descarga fallida")


def clima_lote(comunas: pd.DataFrame, inicio: str, fin: str) -> list[pd.DataFrame]:
    payload = _get(
        {
            "latitude": ",".join(f"{v:.4f}" for v in comunas["lat"]),
            "longitude": ",".join(f"{v:.4f}" for v in comunas["lon"]),
            "daily": DAILY,
            "start_date": inicio,
            "end_date": fin,
            "timezone": "UTC",
            "models": MODELO_CLIMA,
        }
    )
    items = payload if isinstance(payload, list) else [payload]
    if len(items) != len(comunas):
        raise RuntimeError(f"Open-Meteo devolvió {len(items)} ubicaciones para {len(comunas)} comunas")
    salida: list[pd.DataFrame] = []
    for (_, fila), item in zip(comunas.iterrows(), items):
        daily = item.get("daily") or {}
        df = pd.DataFrame(
            {
                "fecha": pd.to_datetime(daily.get("time") or []),
                "TEMP_MED": daily.get("temperature_2m_mean"),
                "TEMP_MAX": daily.get("temperature_2m_max"),
                "HUMEDAD_REL": daily.get("relative_humidity_2m_mean"),
                "VEL_VIENTO": daily.get("wind_speed_10m_max"),
                "PRECIPITACION": daily.get("precipitation_sum"),
            }
        )
        for columna in ("TEMP_MED", "TEMP_MAX", "HUMEDAD_REL", "VEL_VIENTO", "PRECIPITACION"):
            df[columna] = pd.to_numeric(df[columna], errors="coerce")

        df["precip_7d"] = df["PRECIPITACION"].rolling(7, min_periods=1).sum()
        df["precip_30d"] = df["PRECIPITACION"].rolling(30, min_periods=1).sum()
        df["temp_media_7d"] = df["TEMP_MED"].rolling(7, min_periods=1).mean()
        df["humedad_7d"] = df["HUMEDAD_REL"].rolling(7, min_periods=1).mean()
        df["comuna_norm"] = fila["comuna_norm"]
        salida.append(df)
    return salida


def main() -> None:
    base = pd.read_csv(ENTRADA, parse_dates=["fecha"])
    inicio = base["fecha"].min().strftime("%Y-%m-%d")
    fin = base["fecha"].max().strftime("%Y-%m-%d")
    comunas = (
        base.groupby("comuna_norm", as_index=False)
        .agg(comuna=("comuna", "first"), lat=("lat", "mean"), lon=("lon", "mean"))
        .dropna(subset=["lat", "lon"])
    )
    print(f"Comunas: {len(comunas)} | rango: {inicio} .. {fin}")

    climas: list[pd.DataFrame] = []
    tamano_lote = 6
    for i in range(0, len(comunas), tamano_lote):
        lote = comunas.iloc[i : i + tamano_lote]
        print(f"[{i + 1}-{i + len(lote)}/{len(comunas)}] clima {', '.join(lote['comuna_norm'])}")
        climas.extend(clima_lote(lote, inicio, fin))
        time.sleep(4)

    clima = pd.concat(climas, ignore_index=True)
    combinado = base.merge(
        clima,
        on=["fecha", "comuna_norm"],
        how="left",
    )
    combinado = combinado.sort_values(["fecha", "comuna_norm"]).reset_index(drop=True)

    columnas = [
        "fecha", "region_norm", "comuna_norm", "comuna", "lat", "lon",
        "TEMP_MED", "TEMP_MAX", "HUMEDAD_REL", "VEL_VIENTO", "PRECIPITACION",
        "precip_7d", "precip_30d", "temp_media_7d", "humedad_7d",
        "n_incendios", "superficie_total", "causa_principal", "y",
    ]
    combinado = combinado[columnas]
    combinado.to_csv(SALIDA, index=False, encoding="utf-8")
    print(f"Dataset combinado: {len(combinado)} -> {SALIDA}")
    print(f"Filas con clima completo: {combinado[['TEMP_MED','TEMP_MAX','HUMEDAD_REL','VEL_VIENTO','PRECIPITACION']].notna().all(axis=1).sum()}")
    print(f"Prevalencia y: {combinado['y'].mean():.4f}")


if __name__ == "__main__":
    main()
