# -*- coding: utf-8 -*-
"""Tests de los helpers del pipeline de entrenamiento."""

import numpy as np
import pandas as pd

import train_honesto as t


def test_normalizar_region_aliases():
    assert t.normalizar_region("La Araucanía") == "araucania"
    assert t.normalizar_region("Aysén del General Carlos Ibáñez del Campo") == "aysen"
    assert t.normalizar_region("Magallanes y de la Antártica Chilena") == "magallanes"
    assert t.normalizar_region("Metropolitana de Santiago") == "metropolitana"
    assert t.normalizar_region("Libertador General Bernardo O'Higgins") == "o'higgins"
    assert t.normalizar_region("Valparaíso") == "valparaiso"


def test_features_rodadura():
    serie = [
        {
            "fecha": pd.Timestamp("2026-09-01") + pd.Timedelta(days=i),
            "TEMP_MED": 10.0 + i,
            "TEMP_MAX": 15.0 + i,
            "HUMEDAD_REL": 80 - i,
            "VEL_VIENTO": 5.0,
            "PRECIPITACION": 1.0,
        }
        for i in range(31)
    ]
    f = t.construir_features_clima(serie)
    assert f["TEMP_MED"] == 40.0
    assert f["precip_7d"] == 7.0
    assert f["precip_30d"] == 30.0
    assert abs(f["temp_media_7d"] - np.mean([34, 35, 36, 37, 38, 39, 40])) < 1e-6
    # HUMEDAD_REL = 80 - i; los últimos 7 días (i=24..30) van de 56 a 50.
    assert abs(f["humedad_7d"] - np.mean([56, 55, 54, 53, 52, 51, 50])) < 1e-6


def test_features_con_menos_de_30_dias_no_falla():
    serie = [
        {
            "fecha": pd.Timestamp("2026-09-28"),
            "TEMP_MED": 12.0,
            "TEMP_MAX": 18.0,
            "HUMEDAD_REL": 70.0,
            "VEL_VIENTO": 3.0,
            "PRECIPITACION": 0.0,
        }
    ]
    f = t.construir_features_clima(serie)
    assert set(f) == {
        "TEMP_MED",
        "TEMP_MAX",
        "HUMEDAD_REL",
        "VEL_VIENTO",
        "PRECIPITACION",
        "precip_7d",
        "temp_media_7d",
        "humedad_7d",
        "precip_30d",
    }
    assert all(np.isfinite(v) for v in f.values())
