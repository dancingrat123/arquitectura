from shared import riesgo


def test_direcciones_monotonic():
    assert riesgo.DIRECCIONES_MONOTONAS["TEMP_MAX"] == "+1"
    assert riesgo.DIRECCIONES_MONOTONAS["HUMEDAD_REL"] == "-1"
    assert riesgo.DIRECCIONES_MONOTONAS["VEL_VIENTO"] == "0"


def test_features_y_umbrales():
    assert len(riesgo.FEATURES_CLIMA) == 9
    assert riesgo.UMBRAL_ALTO == 0.20
    assert riesgo.UMBRAL_MEDIO == 0.08


def test_componer_explicacion_baja_humedad():
    factores = [
        {
            "variable": "HUMEDAD_REL",
            "etiqueta": "Humedad relativa",
            "valor": 15.0,
            "unidad": "%",
            "direccion": "aumenta",
            "peso": 0.89,
            "monotonia": "-1",
        },
        {
            "variable": "TEMP_MAX",
            "etiqueta": "Temperatura máxima",
            "valor": 40.0,
            "unidad": "°C",
            "direccion": "aumenta",
            "peso": 1.0,
            "monotonia": "+1",
        },
    ]
    texto = riesgo.componer_explicacion("ALTO", factores)
    # La humedad BAJA aumenta el riesgo (monotonía -1); el adjetivo del valor
    # debe ser "baja", no "elevada".
    assert "humedad relativa baja" in texto.lower()
    assert "temperatura máxima elevada" in texto.lower()
