import os
from datetime import datetime

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)

HEADERS = {"X-Internal-Token": os.environ["INTERNAL_TOKEN"]}


def _fila(temperatura: float, fecha: datetime) -> dict:
    return {
        "fecha_medicion": fecha,
        "temperatura": temperatura,
        "humedad": 25.0,
        "velocidad_viento": 28.0,
        "precipitacion": 0.0,
        "cobertura_vegetal": "alta",
        "fuente": "Open-Meteo",
        "calidad": "ok",
        "features": {"TEMP_MED": 30.1},
    }


def test_datos_exigen_token_interno():
    respuesta = client.get("/datos/quilpue/historico")
    assert respuesta.status_code == 401


def test_datos_token_invalido():
    respuesta = client.get(
        "/datos/quilpue/historico", headers={"X-Internal-Token": "otro"}
    )
    assert respuesta.status_code == 401


def test_historico_serie(monkeypatch):
    filas = [
        _fila(32.5, datetime(2026, 11, 18, 9, 0)),
        _fila(30.0, datetime(2026, 11, 17, 9, 0)),
    ]
    monkeypatch.setattr(main, "_historico_zona", lambda *args, **kwargs: filas)

    respuesta = client.get("/datos/quilpue/historico", headers=HEADERS)

    assert respuesta.status_code == 200
    data = respuesta.json()
    assert data["zona"] == "quilpue"
    assert data["total"] == 2
    assert len(data["serie"]) == 2
    assert data["serie"][0]["temperatura"] == 32.5
    assert data["serie"][0]["features"] == {"TEMP_MED": 30.1}
    assert data["serie"][0]["fecha_medicion"].startswith("2026-11-18T09:00:00")


def test_historico_fecha_invalida():
    respuesta = client.get("/datos/quilpue/historico?hasta=ayer", headers=HEADERS)
    assert respuesta.status_code == 422
    assert "hasta" in respuesta.json()["detail"]


def test_historico_zona_inexistente(monkeypatch):
    monkeypatch.setattr(main, "_historico_zona", lambda *args, **kwargs: None)

    respuesta = client.get("/datos/talca/historico", headers=HEADERS)

    assert respuesta.status_code == 404
    assert "talca" in respuesta.json()["detail"]


def test_vegetacion_vacia(monkeypatch):
    """Sin capas persistidas devuelve lista vacía y fuente 'no disponible'."""

    class ConexionFalsa:
        def close(self):
            pass

    monkeypatch.setattr(main.db, "get_conn", lambda: ConexionFalsa())
    monkeypatch.setattr(
        main.db,
        "zona_por_clave",
        lambda conn, clave: {"id_zona": 1, "slug": "quilpue", "comuna": "Quilpué"},
    )
    monkeypatch.setattr(main.db, "capas_vegetacion", lambda conn, id_zona: [])

    respuesta = client.get("/datos/quilpue/vegetacion", headers=HEADERS)

    assert respuesta.status_code == 200
    data = respuesta.json()
    assert data["capas"] == []
    assert data["fuente"] == "no disponible"


def test_vegetacion_con_capas(monkeypatch):
    class ConexionFalsa:
        def close(self):
            pass

    monkeypatch.setattr(main.db, "get_conn", lambda: ConexionFalsa())
    monkeypatch.setattr(
        main.db,
        "zona_por_clave",
        lambda conn, clave: {"id_zona": 1, "slug": "quilpue", "comuna": "Quilpué"},
    )
    monkeypatch.setattr(
        main.db,
        "capas_vegetacion",
        lambda conn, id_zona: [
            {
                "indice": "NDVI",
                "valor": 0.18,
                "categoria": "seco",
                "fecha_captura": datetime(2026, 11, 10),
                "fuente": "Sentinel-2 (ingesta batch)",
            }
        ],
    )

    respuesta = client.get("/datos/quilpue/vegetacion", headers=HEADERS)

    assert respuesta.status_code == 200
    data = respuesta.json()
    assert data["capas"][0]["indice"] == "NDVI"
    assert data["capas"][0]["categoria"] == "seco"
    assert data["fuente"].startswith("Sentinel-2")
