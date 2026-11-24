# -*- coding: utf-8 -*-
"""Entrena el Random Forest monotónico calibrado del riesgo meteorológico.

Uso (sin red si existe `openmeteo_entrenamiento.csv`):

    python ml-pipeline/train_honesto.py [--actualizar]

Genera:
    risk-service/modelo_riesgo.pkl
    ml-pipeline/model_metadata.json

El objetivo es CONAF: un día de actividad alta por región.
y = 1 si (nro_inc_act > 5) or (sup_afec_act > 100)
"""

from __future__ import annotations

import json
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from shared.riesgo import (  # noqa: E402
    ALIAS_REGIONES,
    FEATURES_CLIMA,
    MONOTONIC_CST_CLIMA,
    UMBRAL_ALTO,
    UMBRAL_MEDIO,
)

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (average_precision_score, brier_score_loss,
                             roc_auc_score)
from sklearn.model_selection import StratifiedKFold, cross_val_predict

BASE = Path(__file__).resolve().parent
RUTA_CLIMA = BASE / "openmeteo_entrenamiento.csv"
RUTA_CONAF = BASE / "conaf.csv"
RUTA_EMAS = BASE / "emas.csv"
RUTA_MODELO = RAIZ / "risk-service" / "modelo_riesgo.pkl"
RUTA_METADATA = BASE / "model_metadata.json"

FEATURES_CLIMA = list(FEATURES_CLIMA)
MONOTONIC_CST_CLIMA = list(MONOTONIC_CST_CLIMA)
ALIAS_REGIONES = dict(ALIAS_REGIONES)

UMBRALES = {"alto": UMBRAL_ALTO, "medio": UMBRAL_MEDIO}
MESES_ENTRENAMIENTO = [7, 8, 9]
CORTE_VALIDACION = pd.Timestamp("2026-09-01")


def normalizar_region(valor: str) -> str:
    """Minúsculas, sin acentos y con los alias de EMAS/CONAF aplicados."""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = " ".join(texto.lower().split())
    return ALIAS_REGIONES.get(texto, texto)


def _a_fecha(valor) -> pd.Timestamp:
    return pd.Timestamp(valor)


def _num(valor) -> float:
    if valor is None:
        return 0.0
    try:
        if pd.isna(valor):
            return 0.0
    except (TypeError, ValueError):
        pass
    return float(valor)


def construir_features_clima(serie: list[dict]) -> dict[str, float]:
    """Devuelve las 9 features del último día de la serie, sin NaN."""
    filas = sorted(serie, key=lambda r: _a_fecha(r["fecha"])) if serie else []
    if not filas:
        return {f: 0.0 for f in FEATURES_CLIMA}

    n = len(filas)
    precip = pd.Series([_num(r.get("PRECIPITACION")) for r in filas])
    temp = pd.Series([_num(r.get("TEMP_MED")) for r in filas])
    temp_max = pd.Series([_num(r.get("TEMP_MAX")) for r in filas])
    humedad = pd.Series([_num(r.get("HUMEDAD_REL")) for r in filas])
    viento = pd.Series([_num(r.get("VEL_VIENTO")) for r in filas])

    def rodar(serie_: pd.Series, ventana: int, operacion: str) -> float:
        ventana_real = max(min(ventana, n), 1)
        ventana_rodante = serie_.rolling(ventana_real, min_periods=ventana_real)
        resultado = (
            ventana_rodante.sum() if operacion == "sum" else ventana_rodante.mean()
        )
        return _num(resultado.iloc[-1])

    return {
        "TEMP_MED": float(temp.iloc[-1]),
        "TEMP_MAX": float(temp_max.iloc[-1]),
        "HUMEDAD_REL": float(humedad.iloc[-1]),
        "VEL_VIENTO": float(viento.iloc[-1]),
        "PRECIPITACION": float(precip.iloc[-1]),
        "precip_7d": rodar(precip, 7, "sum"),
        "temp_media_7d": rodar(temp, 7, "mean"),
        "humedad_7d": rodar(humedad, 7, "mean"),
        "precip_30d": rodar(precip, 30, "sum"),
    }


def construir_matriz(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Devuelve (X, features de clima, columnas de región)."""
    columnas_region = sorted({f"reg_{r}" for r in df["region_slug"].unique()})
    dummies = pd.get_dummies(df["region_slug"], prefix="reg").astype(float)
    dummies = dummies.reindex(columns=columnas_region, fill_value=0.0)
    X = pd.concat(
        [df[FEATURES_CLIMA].reset_index(drop=True), dummies.reset_index(drop=True)],
        axis=1,
    )
    return X, list(FEATURES_CLIMA), columnas_region


def _features_por_region(clima: pd.DataFrame) -> pd.DataFrame:
    """Calcula las 9 features para todos los días (no solo el último)."""
    df = clima.copy()
    df["fecha"] = pd.to_datetime(df["fecha"])
    df["region_slug"] = df["region_emas"].map(normalizar_region)
    for columna in ["TEMP_MED", "TEMP_MAX", "HUMEDAD_REL", "VEL_VIENTO", "PRECIPITACION"]:
        df[columna] = pd.to_numeric(df[columna], errors="coerce")
    df = df.dropna(
        subset=["TEMP_MED", "TEMP_MAX", "HUMEDAD_REL", "VEL_VIENTO", "PRECIPITACION"]
    ).sort_values(["region_slug", "fecha"])

    grupos = df.groupby("region_slug")
    df["precip_7d"] = grupos["PRECIPITACION"].transform(
        lambda s: s.rolling(7, min_periods=7).sum()
    )
    df["precip_30d"] = grupos["PRECIPITACION"].transform(
        lambda s: s.rolling(30, min_periods=30).sum()
    )
    df["temp_media_7d"] = grupos["TEMP_MED"].transform(
        lambda s: s.rolling(7, min_periods=7).mean()
    )
    df["humedad_7d"] = grupos["HUMEDAD_REL"].transform(
        lambda s: s.rolling(7, min_periods=7).mean()
    )
    return df


def _dataset() -> pd.DataFrame:
    clima = _features_por_region(pd.read_csv(RUTA_CLIMA))
    conaf = pd.read_csv(RUTA_CONAF)
    conaf["region_slug"] = conaf["region"].map(normalizar_region)
    conaf["fecha"] = pd.to_datetime(conaf["fecha"])
    conaf["y"] = ((conaf["nro_inc_act"] > 5) | (conaf["sup_afec_act"] > 100)).astype(int)

    df = pd.merge(
        conaf[["region_slug", "region", "fecha", "y"]],
        clima[["region_slug", "fecha", *FEATURES_CLIMA]],
        on=["region_slug", "fecha"],
        how="inner",
    )
    df = (
        df.dropna(subset=[*FEATURES_CLIMA, "y"])
        .sort_values(["fecha", "region_slug"])
        .reset_index(drop=True)
    )
    if df.empty:
        raise RuntimeError("El cruce clima/CONAF quedó vacío: revisa los nombres de región")
    return df


def _rf(n_features: int) -> RandomForestClassifier:
    """RF monotónico; la restricción se rellena con 0 para las columnas de región."""
    monotonic_cst = MONOTONIC_CST_CLIMA + [0] * (n_features - len(MONOTONIC_CST_CLIMA))
    return RandomForestClassifier(
        n_estimators=500,
        min_samples_leaf=5,
        class_weight=None,
        monotonic_cst=monotonic_cst,
        random_state=42,
    )


def _modelo(n_features: int):
    return CalibratedClassifierCV(
        _rf(n_features),
        method="sigmoid",
        cv=StratifiedKFold(5, shuffle=True, random_state=42),
    )


def _umbral_oof(oof: np.ndarray, y: pd.Series) -> dict:
    tabla = {}
    for nombre, umbral in UMBRALES.items():
        pred = (oof >= umbral).astype(int)
        tp = int(((pred == 1) & (y == 1)).sum())
        fp = int(((pred == 1) & (y == 0)).sum())
        fn = int(((pred == 0) & (y == 1)).sum())
        tabla[nombre] = {
            "umbral": umbral,
            "precision": round(tp / max(tp + fp, 1), 3),
            "recall": round(tp / max(tp + fn, 1), 3),
            "dias_alerta": int(pred.sum()),
            "fraccion_dias": round(float(pred.mean()), 3),
        }
    return tabla


def _calibracion(probabilidades: np.ndarray, y: pd.Series) -> list[dict]:
    quintil = pd.qcut(probabilidades, 5, labels=False, duplicates="drop")
    tabla = (
        pd.DataFrame({"p": probabilidades, "y": y.values, "q": quintil})
        .groupby("q")
        .agg(n=("y", "size"), p_media=("p", "mean"), tasa_real=("y", "mean"))
    )
    return [
        {
            "quintil": int(indice) + 1,
            "n": int(fila["n"]),
            "p_media": round(float(fila["p_media"]), 3),
            "tasa_real": round(float(fila["tasa_real"]), 3),
        }
        for indice, fila in tabla.iterrows()
    ]


def _sensibilidad(modelo, df: pd.DataFrame, columnas_region: list[str]) -> dict[str, float]:
    base = df[FEATURES_CLIMA].median().to_dict()
    perfiles = {
        "frio_humedo": dict(TEMP_MED=8, TEMP_MAX=12, HUMEDAD_REL=90, VEL_VIENTO=5,
                            PRECIPITACION=10, precip_7d=40, precip_30d=120,
                            temp_media_7d=8, humedad_7d=90),
        "templado": dict(TEMP_MED=16, TEMP_MAX=22, HUMEDAD_REL=79, VEL_VIENTO=5,
                         PRECIPITACION=0, precip_7d=0, precip_30d=20,
                         temp_media_7d=16, humedad_7d=79),
        "calor_seco": dict(TEMP_MED=32, TEMP_MAX=38, HUMEDAD_REL=25, VEL_VIENTO=20,
                           PRECIPITACION=0, precip_7d=0, precip_30d=0,
                           temp_media_7d=30, humedad_7d=30),
        "extremo": dict(TEMP_MED=40, TEMP_MAX=45, HUMEDAD_REL=15, VEL_VIENTO=50,
                        PRECIPITACION=0, precip_7d=0, precip_30d=0,
                        temp_media_7d=38, humedad_7d=18),
    }
    resultado = {}
    for nombre, valores in perfiles.items():
        fila = {**base, **valores}
        registro = {**fila, **{col: (1.0 if col == "reg_valparaiso" else 0.0)
                              for col in columnas_region}}
        X = pd.DataFrame([registro])[FEATURES_CLIMA + columnas_region]
        resultado[nombre] = round(float(modelo.predict_proba(X)[:, 1][0]), 3)
    return resultado


def _loro_auc(df: pd.DataFrame) -> dict[str, float]:
    """AUC sin identidad de región (solo clima), en dos lecturas distintas.

    - `por_region`: media de la AUC dentro de cada región retenida. Mide si el
      clima ordena los días de una región, aunque nunca la haya visto.
    - `pooled`: AUC sobre todas las predicciones retenidas juntas. Mide si el
      modelo ordena regiones entre sí sin conocerlas; es la lectura pesimista y
      la que explica por qué una región nueva exige reentrenar.
    """
    aucs: list[float] = []
    pooled_prob = np.full(len(df), np.nan)
    for region in sorted(df["region_slug"].unique()):
        mascara = (df["region_slug"] == region).to_numpy()
        resto = df[~mascara]
        objetivo = df[mascara]
        if objetivo["y"].nunique() < 2 or resto["y"].nunique() < 2:
            continue
        modelo = CalibratedClassifierCV(
            RandomForestClassifier(
                n_estimators=150, min_samples_leaf=5, class_weight=None,
                monotonic_cst=MONOTONIC_CST_CLIMA, random_state=42,
            ),
            method="sigmoid",
            cv=StratifiedKFold(3, shuffle=True, random_state=42),
        )
        modelo.fit(resto[FEATURES_CLIMA], resto["y"])
        prob = modelo.predict_proba(objetivo[FEATURES_CLIMA])[:, 1]
        pooled_prob[mascara] = prob
        aucs.append(roc_auc_score(objetivo["y"], prob))
    cubiertos = ~np.isnan(pooled_prob)
    return {
        "por_region": round(float(np.mean(aucs)), 3) if aucs else float("nan"),
        "pooled": round(
            float(roc_auc_score(df["y"][cubiertos], pooled_prob[cubiertos])), 3
        ),
    }


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=RAIZ, capture_output=True, text=True, check=False,
        ).stdout.strip() or "desconocido"
    except Exception:
        return "desconocido"


def _actualizar_clima() -> None:
    subprocess.run(["python", str(BASE / "descargar_clima.py")], cwd=RAIZ, check=True)


def entrenar(actualizar_clima: bool = False) -> dict:
    if actualizar_clima or not RUTA_CLIMA.exists():
        _actualizar_clima()

    df = _dataset()
    X, features, columnas_region = construir_matriz(df)
    monotonic_cst = MONOTONIC_CST_CLIMA + [0] * len(columnas_region)
    y = df["y"]

    print(
        f"dataset: {df.shape[0]} filas | {df['region_slug'].nunique()} regiones | "
        f"prevalencia ALTO: {y.mean():.3f}"
    )

    # 1) Validación temporal: entrena jul-ago, prueba septiembre.
    entrenamiento = df["fecha"] < CORTE_VALIDACION
    modelo_temporal = _modelo(X.shape[1]).fit(X[entrenamiento], y[entrenamiento])
    prob_test = modelo_temporal.predict_proba(X[~entrenamiento])[:, 1]
    y_test = y[~entrenamiento]
    temporal = {
        "metodo": "entrena 2026-07-01..2026-08-31, prueba 2026-09-01..2026-09-28",
        "n_entrenamiento": int(entrenamiento.sum()),
        "n_prueba": int((~entrenamiento).sum()),
        "positivos_prueba": int(y_test.sum()),
        "prevalencia_prueba": round(float(y_test.mean()), 3),
        "auc": round(float(roc_auc_score(y_test, prob_test)), 3),
        "ap": round(float(average_precision_score(y_test, prob_test)), 3),
        "brier": round(float(brier_score_loss(y_test, prob_test)), 3),
        "calibracion": _calibracion(prob_test, y_test),
    }
    print(
        f"temporal: AUC={temporal['auc']:.3f} AP={temporal['ap']:.3f} "
        f"Brier={temporal['brier']:.3f}"
    )

    # 2) Out-of-fold solo para fijar umbrales (optimista: mezcla días de la misma región).
    oof = cross_val_predict(
        _modelo(X.shape[1]), X, y,
        cv=StratifiedKFold(5, shuffle=True, random_state=42),
        method="predict_proba",
    )[:, 1]
    oof_metricas = {
        "auc": round(float(roc_auc_score(y, oof)), 3),
        "ap": round(float(average_precision_score(y, oof)), 3),
        "brier": round(float(brier_score_loss(y, oof)), 3),
        "calibracion": _calibracion(oof, y),
        "umbrales": _umbral_oof(oof, y),
    }

    # 3) Límite explícito: sin identidad de región el clima generaliza poco.
    auc_loro = _loro_auc(df)
    print(
        f"leave-one-region-out (solo clima): "
        f"por_region={auc_loro['por_region']:.3f} pooled={auc_loro['pooled']:.3f}"
    )

    # 4) Modelo final sobre toda la ventana jul-sep.
    modelo = _modelo(X.shape[1]).fit(X, y)
    sensibilidad = _sensibilidad(modelo, df, columnas_region)
    print(f"sensibilidad: {sensibilidad}")

    if not (sensibilidad["frio_humedo"] < sensibilidad["templado"] < sensibilidad["calor_seco"]):
        raise RuntimeError(
            "El perfil de sensibilidad no es creciente; revisa monotonic_cst"
        )

    rango_features = {
        f: [round(float(df[f].min()), 2), round(float(df[f].max()), 2)]
        for f in FEATURES_CLIMA
    }

    metadata = {
        "version_modelo": "rf-clima-region-v2",
        "fecha_entrenamiento": datetime.now(timezone.utc).isoformat(),
        "commit": _commit(),
        "objetivo": "y = (nro_inc_act > 5) | (sup_afec_act > 100)",
        "prevalencia": round(float(y.mean()), 3),
        "ventana": {
            "inicio": str(df["fecha"].min().date()),
            "fin": str(df["fecha"].max().date()),
        },
        "fuentes": {
            "objetivo": "ml-pipeline/conaf.csv (CONAF)",
            "clima": "ml-pipeline/openmeteo_entrenamiento.csv (Open-Meteo ecmwf_ifs025)",
            "coordenadas": "ml-pipeline/emas.csv (EMAS, promedio regional)",
        },
        "modelo": {
            "algoritmo": "RandomForestClassifier + CalibratedClassifierCV(sigmoid)",
            "n_estimators": 500,
            "min_samples_leaf": 5,
            "class_weight": None,
            "monotonic_cst": dict(zip(features + columnas_region, monotonic_cst)),
        },
        "features": features,
        "regiones": [c.replace("reg_", "", 1) for c in columnas_region],
        "umbrales": UMBRALES,
        "meses_entrenamiento": MESES_ENTRENAMIENTO,
        "rango_features": rango_features,
        "validacion": {
            "temporal": temporal,
            "out_of_fold": oof_metricas,
            "leave_one_region_out": auc_loro,
            "sensibilidad": sensibilidad,
        },
        "limitaciones": [
            "Modelo regional: las comunas heredan la tasa base de su región.",
            "No modela estacionalidad: ventana jul-sep 2026; fuera de ella la salida es extrapolación.",
            "No generaliza a regiones no vistas: sin identidad de región el AUC pooled es "
            f"{auc_loro['pooled']} (la discriminación dentro de una región retenida es "
            f"{auc_loro['por_region']}). Una región nueva exige reentrenar.",
            "No predice número de incendios ni ocurrencia de un incendio concreto.",
            "Calibración validada solo en la ventana de entrenamiento.",
        ],
    }

    bundle = {
        "modelo": modelo,
        "features": features,
        "regiones": [c.replace("reg_", "", 1) for c in columnas_region],
        "columnas_region": columnas_region,
        "monotonic_cst": monotonic_cst,
        "umbrales": UMBRALES,
        "meses_entrenamiento": MESES_ENTRENAMIENTO,
        "rango_features": rango_features,
        "metadata": metadata,
    }

    joblib.dump(bundle, RUTA_MODELO)
    RUTA_METADATA.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"artefacto: {RUTA_MODELO}")
    print(f"metadata:  {RUTA_METADATA}")
    return bundle


if __name__ == "__main__":
    import sys

    entrenar(actualizar_clima="--actualizar" in sys.argv)
