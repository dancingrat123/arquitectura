# -*- coding: utf-8 -*-
"""Entrena un Random Forest comunal diario para Valparaíso.

Entrada:
  datos_externos/conaf/dataset_comunal_diario_valparaiso_2017_2025.csv

Salidas:
  risk-service/modelo_riesgo_comunal.pkl
  ml-pipeline/model_metadata_comunal.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from shared.riesgo import FEATURES_CLIMA  # noqa: E402

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold, StratifiedKFold, cross_val_predict

BASE = Path(__file__).resolve().parent
DATASET = BASE.parent / "datos_externos" / "conaf" / "dataset_comunal_diario_valparaiso_2017_2025.csv"
MODELO = BASE.parent / "risk-service" / "modelo_riesgo_comunal.pkl"
METADATA = BASE / "model_metadata_comunal.json"

VERSION_MODELO = "rf-comunal-clima-v1"
CORTE_TEMPORAL = pd.Timestamp("2024-01-01")

FEATURES_TIEMPO = ["mes", "mes_sin", "mes_cos"]
UMBRAL_ALTO_INICIAL = 0.20
UMBRAL_MEDIO_INICIAL = 0.08


def normalizar_comuna(valor: object) -> str:
    import unicodedata

    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.lower().split())


def comunas_desde_datos(df: pd.DataFrame) -> list[str]:
    return sorted(df["comuna_norm"].dropna().unique())


def construir_matriz(
    df: pd.DataFrame,
    comunas: list[str],
    incluir_comuna: bool = True,
) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    datos = df.copy()
    datos["fecha"] = pd.to_datetime(datos["fecha"])
    datos["mes"] = datos["fecha"].dt.month
    datos["mes_sin"] = np.sin(2 * np.pi * datos["mes"] / 12)
    datos["mes_cos"] = np.cos(2 * np.pi * datos["mes"] / 12)

    columnas = FEATURES_CLIMA + FEATURES_TIEMPO
    X = datos[columnas].copy()
    if incluir_comuna:
        for comuna in comunas:
            X[f"com_{comuna}"] = (datos["comuna_norm"] == comuna).astype(int)
    y = datos["y"].astype(int)
    return X, y, list(X.columns)


def monotonic_cst(features: list[str]) -> list[int]:
    restricciones: list[int] = []
    for feature in features:
        if feature in ("TEMP_MED", "TEMP_MAX", "temp_media_7d"):
            restricciones.append(1)
        elif feature in (
            "HUMEDAD_REL",
            "humedad_7d",
            "PRECIPITACION",
            "precip_7d",
            "precip_30d",
        ):
            restricciones.append(-1)
        else:
            restricciones.append(0)
    return restricciones


def metricas(y: pd.Series, p: np.ndarray) -> dict[str, float]:
    return {
        "auc": float(roc_auc_score(y, p)),
        "ap": float(average_precision_score(y, p)),
        "brier": float(brier_score_loss(y, p)),
    }


def tabla_calibracion(y: pd.Series, p: np.ndarray, quintiles: int = 5) -> list[dict]:
    df = pd.DataFrame({"y": y, "p": p})
    df["quintil"] = pd.qcut(df["p"], quintiles, labels=False, duplicates="drop")
    tabla = (
        df.groupby("quintil")
        .agg(p_media=("p", "mean"), tasa_real=("y", "mean"), n=("y", "size"))
        .reset_index()
    )
    return tabla.to_dict(orient="records")


def elegir_umbrales(
    y: pd.Series,
    p: np.ndarray,
    q_alto: float = 0.95,
    q_medio: float = 0.80,
) -> dict[str, float]:
    """Elige umbrales por cuantiles de las probabilidades OOF.

    ALTO queda en el top 5 % de días-comuna y MEDIO en el top 20 %. Es un
    criterio transparente y no depende del conjunto temporal de prueba.
    """
    alto = float(np.quantile(p, q_alto))
    medio = float(np.quantile(p, q_medio))
    if medio >= alto:
        medio = alto * 0.75
    return {"alto": round(alto, 3), "medio": round(medio, 3)}


def modelo_base(n_estimators: int = 500, monotonic: list[int] | None = None) -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=n_estimators,
        min_samples_leaf=5,
        class_weight=None,
        random_state=42,
        n_jobs=-1,
        monotonic_cst=monotonic,
    )


def validacion_temporal(df: pd.DataFrame, comunas: list[str], features: list[str]) -> dict:
    train = df[df["fecha"] < CORTE_TEMPORAL]
    test = df[df["fecha"] >= CORTE_TEMPORAL]
    X_train, y_train, _ = construir_matriz(train, comunas)
    X_test, y_test, _ = construir_matriz(test, comunas)
    X_train = X_train[features]
    X_test = X_test[features]

    modelo = CalibratedClassifierCV(
        modelo_base(monotonic=monotonic_cst(features)),
        method="sigmoid",
        cv=StratifiedKFold(5, shuffle=True, random_state=42),
    )
    modelo.fit(X_train, y_train)
    p = modelo.predict_proba(X_test)[:, 1]
    resultado = metricas(y_test, p)
    resultado["tabla_calibracion"] = tabla_calibracion(y_test, p)
    resultado["n_train"] = int(len(y_train))
    resultado["n_test"] = int(len(y_test))
    resultado["positivos_test"] = int(y_test.sum())
    return resultado


def validacion_espacial(df: pd.DataFrame) -> dict:
    """Validación con clima y mes solamente, sin identidad de comuna."""
    comunas = comunas_desde_datos(df)
    X, y, features = construir_matriz(df, comunas, incluir_comuna=False)
    grupos = df["comuna_norm"].values
    oof = np.zeros(len(y))
    for train_idx, test_idx in GroupKFold(5).split(X, y, groups=grupos):
        modelo = modelo_base(monotonic=monotonic_cst(features))
        modelo.fit(X.iloc[train_idx], y.iloc[train_idx])
        oof[test_idx] = modelo.predict_proba(X.iloc[test_idx])[:, 1]
    return metricas(y, oof)


def oof_calibrado(df: pd.DataFrame, comunas: list[str], features: list[str]) -> np.ndarray:
    X, y, _ = construir_matriz(df, comunas)
    X = X[features]
    base = modelo_base(monotonic=monotonic_cst(features))
    p_oof = cross_val_predict(
        base,
        X,
        y,
        cv=StratifiedKFold(5, shuffle=True, random_state=42),
        method="predict_proba",
        n_jobs=-1,
    )[:, 1]
    calibrador = LogisticRegression().fit(p_oof.reshape(-1, 1), y)
    return calibrador.predict_proba(p_oof.reshape(-1, 1))[:, 1]


def commit_actual() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=BASE.parent,
            text=True,
        ).strip()
    except Exception:
        return "desconocido"


def _sha256(ruta: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(ruta, "rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def entrenar() -> dict:
    df = pd.read_csv(DATASET, parse_dates=["fecha"])
    df["comuna_norm"] = df["comuna_norm"].map(normalizar_comuna)
    comunas = comunas_desde_datos(df)
    X, y, features = construir_matriz(df, comunas)

    print(f"dataset={len(df)} filas | comunas={len(comunas)} | features={len(features)}")
    print(f"prevalencia={y.mean():.4f} | positivos={int(y.sum())}")

    print("validación temporal ...")
    temporal = validacion_temporal(df, comunas, features)
    print(
        f"  AUC={temporal['auc']:.3f} AP={temporal['ap']:.3f} Brier={temporal['brier']:.3f} "
        f"(train={temporal['n_train']} test={temporal['n_test']})"
    )

    print("validación espacial (clima+mes, sin comuna) ...")
    espacial = validacion_espacial(df)
    print(f"  AUC={espacial['auc']:.3f} AP={espacial['ap']:.3f} Brier={espacial['brier']:.3f}")

    print("probabilidades OOF para umbrales ...")
    p_oof = oof_calibrado(df, comunas, features)
    umbrales = elegir_umbrales(y, p_oof)
    print(f"  umbrales={umbrales}")

    print("entrenando modelo final ...")
    modelo = CalibratedClassifierCV(
        modelo_base(monotonic=monotonic_cst(features)),
        method="sigmoid",
        cv=StratifiedKFold(5, shuffle=True, random_state=42),
    )
    modelo.fit(X, y)

    rango_features = {
        feature: [float(df[feature].min()), float(df[feature].max())]
        for feature in FEATURES_CLIMA
    }

    bundle = {
        "modelo": modelo,
        "features": features,
        "features_clima": FEATURES_CLIMA,
        "features_tiempo": FEATURES_TIEMPO,
        "comunas": comunas,
        "monotonic_cst": monotonic_cst(features),
        "umbrales": umbrales,
        "meses_entrenamiento": list(range(1, 13)),
        "rango_features": rango_features,
        "metadata": {
            "version_modelo": VERSION_MODELO,
            "fecha_entrenamiento": datetime.now(timezone.utc).isoformat(),
            "commit": commit_actual(),
            "dataset": str(DATASET.relative_to(BASE.parent)),
            "filas": int(len(df)),
            "comunas": comunas,
            "features": features,
            "prevalencia": float(y.mean()),
            "positivos": int(y.sum()),
            "ventana": {
                "inicio": df["fecha"].min().date().isoformat(),
                "fin": df["fecha"].max().date().isoformat(),
                "corte_temporal": CORTE_TEMPORAL.date().isoformat(),
            },
            "fuentes": {
                "incendios": "CONAF - SHP puntos georreferenciados (IDE Minagri)",
                "clima": "Open-Meteo Archive models=era5",
            },
            "validacion_temporal": temporal,
            "validacion_espacial": espacial,
            "umbrales": umbrales,
            "meses_entrenamiento": list(range(1, 13)),
            "rango_features": rango_features,
            "limitaciones": [
                "Modelo comunal diario; no es inferencia distrital.",
                "Estima ocurrencia de al menos un incendio en la comuna, no el número ni la superficie.",
                "No es un pronóstico operacional de incendios.",
                "Cobertura 2017-2025; no extrapola a regímenes climáticos no observados.",
                "Clima histórico ERA5; la ingesta operacional usa ecmwf_ifs025.",
            ],
        },
    }

    MODELO.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, MODELO)
    print(f"modelo -> {MODELO}")

    metadata = bundle["metadata"]
    metadata["sha256_modelo"] = _sha256(MODELO)
    METADATA.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"metadata -> {METADATA}")
    return metadata


if __name__ == "__main__":
    entrenar()
