import numpy as np
import pandas as pd

import train_comunal as t


def _df():
    fechas = pd.date_range("2024-01-01", periods=4, freq="D")
    filas = []
    for comuna in ("quilpue", "vina del mar"):
        for i, fecha in enumerate(fechas):
            filas.append(
                {
                    "fecha": fecha,
                    "comuna_norm": comuna,
                    "y": int(i % 2),
                    **{f: float(i + 1) for f in t.FEATURES_CLIMA},
                }
            )
    return pd.DataFrame(filas)


def test_normalizar_comuna():
    assert t.normalizar_comuna("Viña del Mar") == "vina del mar"
    assert t.normalizar_comuna("  Quilpué  ") == "quilpue"


def test_construir_matriz():
    df = _df()
    X, y, features = t.construir_matriz(df, ["quilpue", "vina del mar"])
    assert len(X) == len(df)
    assert len(features) == len(t.FEATURES_CLIMA) + len(t.FEATURES_TIEMPO) + 2
    assert "com_quilpue" in X.columns
    assert "mes_sin" in X.columns
    assert set(y.unique()) == {0, 1}


def test_monotonic_cst_alineado():
    features = t.FEATURES_CLIMA + t.FEATURES_TIEMPO + ["com_quilpue"]
    restricciones = t.monotonic_cst(features)
    assert len(restricciones) == len(features)
    assert restricciones[features.index("TEMP_MED")] == 1
    assert restricciones[features.index("HUMEDAD_REL")] == -1
    assert restricciones[features.index("VEL_VIENTO")] == 0


def test_elegir_umbrales():
    y = pd.Series([0, 0, 0, 1, 0, 1, 0, 1, 0, 1])
    p = np.array([0.01, 0.02, 0.03, 0.4, 0.05, 0.5, 0.06, 0.6, 0.07, 0.7])
    umbrales = t.elegir_umbrales(y, p)
    assert 0 < umbrales["medio"] < umbrales["alto"] < 1


def test_features_clima_desde_shared():
    from shared import riesgo

    # Sin duplicación local: la lista debe ser el MISMO objeto de shared para
    # evitar drift entre entrenamiento e inferencia.
    assert t.FEATURES_CLIMA is riesgo.FEATURES_CLIMA
