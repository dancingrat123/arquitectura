from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_health_check():
    """Prueba que el endpoint de salud responda 200"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_acceso_sin_token():
    """Prueba que la API bloquee el acceso sin token JWT"""
    response = client.get("/api/riesgo/todas")
    assert response.status_code == 401

def test_login_incorrecto():
    """Prueba que el login falle con contraseña incorrecta"""
    response = client.post("/token", data={"username": "admin", "password": "clave_mala"})
    assert response.status_code == 401

def test_login_correcto():
    """Prueba que el login devuelva un token válido"""
    response = client.post("/token", data={"username": "admin", "password": "sirif2026"})
    assert response.status_code == 200
    assert "access_token" in response.json()