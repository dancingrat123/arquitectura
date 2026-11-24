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


def test_puntaje_comunal_normalizado():
    # En el umbral ALTO comunal (0.07) el puntaje debe ser 15, no round(0.07*15)=1.
    assert main.calcular_puntaje(0.07, 0.07) == 15
    assert main.calcular_puntaje(0.031, 0.07) == 7
    assert main.calcular_puntaje(0.11, 0.07) == 15  # satura en 15


def test_normalizar_region_aliases():
    assert main.normalizar_region("Valparaíso") == "valparaiso"
    assert main.normalizar_region("La Araucanía") == "araucania"


def test_region_desconocida_no_falla():
    fila, conocida = main.construir_fila(
        {f: 1.0 for f in main.FEATURES_CLIMA}, "Región Inventada", ["valparaiso"]
    )
    assert conocida is False
    assert fila.shape == (1, len(main.FEATURES_CLIMA) + 1)


def test_normalizar_comuna():
    assert main.normalizar_comuna("Viña del Mar") == "vina del mar"
    assert main.normalizar_comuna("  Quilpué  ") == "quilpue"


def test_region_modelo_comunal():
    assert main.REGION_MODELO_COMUNAL == "valparaiso"
    assert main.normalizar_region("Valparaíso") == main.REGION_MODELO_COMUNAL


def test_bundle_comunal_umbrales_default():
    # Umbrales operativos (probabilidad ABSOLUTA diaria) del modelo comunal:
    # ALTO >= 15 %, MEDIO >= 5 %. No son los percentiles entrenados (0.07/0.031).
    bundle = main._bundle_comunal()
    assert bundle["umbrales"]["alto"] == main.UMBRAL_ALTO_COMUNAL == 0.15
    assert bundle["umbrales"]["medio"] == main.UMBRAL_MEDIO_COMUNAL == 0.05


def test_umbrales_operativos_comunal():
    assert main.umbrales_operativos("rf-comunal-clima-v1") == {"alto": 0.15, "medio": 0.05}
    assert main.umbrales_operativos("rf-clima-region-v2") is None


def test_nivel_comunal_operativo():
    umbrales = main.umbrales_operativos("rf-comunal-clima-v1")
    # Viña 7.18 % ya NO es ALTO (antes lo era con el percentil 0.07); es MEDIO.
    assert main.nivel_desde_probabilidad(0.0718, umbrales["alto"], umbrales["medio"]) == "MEDIO"
    assert main.nivel_desde_probabilidad(0.68, umbrales["alto"], umbrales["medio"]) == "ALTO"
    assert main.nivel_desde_probabilidad(0.03, umbrales["alto"], umbrales["medio"]) == "BAJO"


def test_construir_fila_comunal():
    from datetime import datetime

    bundle = {
        "features": main.FEATURES_CLIMA + ["mes", "mes_sin", "mes_cos", "com_quilpue"],
        "comunas": ["quilpue"],
    }
    fila, conocida = main.construir_fila_comunal(
        {f: 1.0 for f in main.FEATURES_CLIMA},
        "Quilpué",
        datetime(2026, 1, 15),
        bundle,
    )
    assert conocida is True
    assert fila.shape == (1, len(bundle["features"]))
    assert fila.iloc[0]["com_quilpue"] == 1
    assert fila.iloc[0]["mes"] == 1


def test_construir_fila_comunal_desconocida():
    from datetime import datetime

    bundle = {
        "features": main.FEATURES_CLIMA + ["mes", "mes_sin", "mes_cos", "com_quilpue"],
        "comunas": ["quilpue"],
    }
    fila, conocida = main.construir_fila_comunal(
        {f: 1.0 for f in main.FEATURES_CLIMA},
        "Comuna Inventada",
        datetime(2026, 1, 15),
        bundle,
    )
    assert conocida is False
    assert fila.iloc[0]["com_quilpue"] == 0


RANGO_CONOCIDO = {
    "TEMP_MED": [-13.8, 26.1],
    "TEMP_MAX": [-7.0, 32.1],
    "HUMEDAD_REL": [5.0, 99.0],
    "VEL_VIENTO": [1.5, 45.7],
    "PRECIPITACION": [0.0, 117.6],
    "precip_7d": [0.0, 344.6],
    "temp_media_7d": [-10.21, 24.39],
    "humedad_7d": [9.0, 97.0],
    "precip_30d": [0.0, 643.2],
}

FEATURES_EJEMPLO = {
    "TEMP_MED": 15.0,
    "TEMP_MAX": 40.0,
    "HUMEDAD_REL": 15.0,
    "VEL_VIENTO": 20.0,
    "PRECIPITACION": 50.0,
    "precip_7d": 100.0,
    "temp_media_7d": 10.0,
    "humedad_7d": 50.0,
    "precip_30d": 100.0,
}


def test_factores_predominantes_monotono():
    factores = main.calcular_factores_predominantes(
        FEATURES_EJEMPLO, RANGO_CONOCIDO, main.DIRECCIONES_MONOTONAS
    )
    por_variable = {factor["variable"]: factor for factor in factores}

    assert len(factores) == 5
    # TEMP_MAX 40 °C (t=1.0) y HUMEDAD_REL 15 % (riesgo_dir≈0.89) AMBAS aumentan
    # el riesgo: la dirección refleja el efecto sobre el riesgo, no el signo
    # monótono de la feature.
    assert por_variable["TEMP_MAX"]["direccion"] == "aumenta"
    assert por_variable["HUMEDAD_REL"]["direccion"] == "aumenta"
    assert por_variable["HUMEDAD_REL"]["monotonia"] == "-1"
    assert por_variable["TEMP_MAX"]["monotonia"] == "+1"
    assert por_variable["TEMP_MAX"]["peso"] == 1.0
    assert por_variable["HUMEDAD_REL"]["peso"] == 0.89
    assert "VEL_VIENTO" not in por_variable
    assert 0.0 <= por_variable["TEMP_MAX"]["peso"] <= 1.0


def test_explicacion_sin_rango():
    factores = main.calcular_factores_predominantes(
        FEATURES_EJEMPLO, {}, main.DIRECCIONES_MONOTONAS
    )
    assert factores == []
    assert "no disponible" in main.componer_explicacion("ALTO", factores)


# --- S9: checksum SHA-256 del artefacto -----------------------------------


def test_checksum_modelo_valido(tmp_path):
    import hashlib
    import json

    artefacto = tmp_path / "modelo.pkl"
    artefacto.write_bytes(b"contenido-del-modelo")
    metadata = tmp_path / "model_metadata.json"
    metadata.write_text(
        json.dumps({"sha256_modelo": hashlib.sha256(artefacto.read_bytes()).hexdigest()}),
        encoding="utf-8",
    )

    ok, detalle = main.verificar_checksum_modelo(str(artefacto), str(metadata))

    assert ok is True
    assert "sha256 verificado" in detalle


def test_checksum_modelo_invalido(tmp_path):
    import json

    artefacto = tmp_path / "modelo.pkl"
    artefacto.write_bytes(b"contenido-del-modelo")
    metadata = tmp_path / "model_metadata.json"
    metadata.write_text(json.dumps({"sha256_modelo": "0" * 64}), encoding="utf-8")

    ok, detalle = main.verificar_checksum_modelo(str(artefacto), str(metadata))

    assert ok is False
    assert "no coincide" in detalle


def test_load_model_rechaza_checksum_invalido(tmp_path, monkeypatch):
    import json

    artefacto = tmp_path / "modelo.pkl"
    artefacto.write_bytes(b"contenido-del-modelo")
    metadata = tmp_path / "model_metadata.json"
    metadata.write_text(json.dumps({"sha256_modelo": "0" * 64}), encoding="utf-8")
    monkeypatch.setattr(main, "MODEL_PATH", str(artefacto))
    monkeypatch.setattr(main, "MODEL_METADATA_PATH", str(metadata))
    monkeypatch.setattr(main, "model", object())
    monkeypatch.setattr(main, "bundle", {})
    monkeypatch.setattr(main, "checksum_verificado", None)
    monkeypatch.setattr(main, "checksum_detalle", "")

    main.load_model()

    assert main.model is None
    assert main.bundle is None
    assert main.checksum_verificado is False


def test_load_model_comunal_rechaza_checksum_invalido(tmp_path, monkeypatch):
    import json

    artefacto = tmp_path / "modelo_comunal.pkl"
    artefacto.write_bytes(b"contenido-comunal")
    metadata = tmp_path / "model_metadata_comunal.json"
    metadata.write_text(json.dumps({"sha256_modelo": "0" * 64}), encoding="utf-8")
    monkeypatch.setattr(main, "MODELO_COMUNAL_PATH", str(artefacto))
    monkeypatch.setattr(main, "MODEL_METADATA_COMUNAL_PATH", str(metadata))
    monkeypatch.setattr(main, "modelo_comunal", object())
    monkeypatch.setattr(main, "bundle_comunal", {})
    monkeypatch.setattr(main, "comunal_detalle", "")

    main.load_model_comunal()

    assert main.modelo_comunal is None
    assert main.bundle_comunal is None

