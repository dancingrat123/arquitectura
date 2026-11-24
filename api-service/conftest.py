import os
import sys

import pytest
from passlib.context import CryptContext

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

# El entorno de tests no tiene PostgreSQL: se usa un repositorio de usuarios falso.
os.environ.setdefault("DATABASE_URL", "postgresql://sirif:sirif2026@localhost:5432/sirif")
os.environ.setdefault("JWT_SECRET", "secreto-de-tests")
os.environ.setdefault("INTERNAL_TOKEN", "token-de-tests")
# El asistente debe usar siempre el fallback local en la suite de tests.
os.environ["LLM_URL"] = ""
os.environ["LLM_API_KEY"] = ""

_HASH = CryptContext(schemes=["pbkdf2_sha256"]).hash("sirif2026")

USUARIOS_FALSOS = {
    "admin": {
        "id_usuario": 1,
        "username": "admin",
        "nombre_completo": "Administrador SIRIF",
        "rol": "admin",
        "hash_password": _HASH,
        "activo": True,
    },
    "analista": {
        "id_usuario": 2,
        "username": "analista",
        "nombre_completo": "Analista SIRIF",
        "rol": "analista",
        "hash_password": _HASH,
        "activo": True,
    },
    "visualizador": {
        "id_usuario": 3,
        "username": "visualizador",
        "nombre_completo": "Visualizador SIRIF",
        "rol": "visualizador",
        "hash_password": _HASH,
        "activo": True,
    },
}


@pytest.fixture(autouse=True)
def usuarios_falsos(monkeypatch):
    import auth

    monkeypatch.setattr(auth, "buscar_usuario", lambda username: USUARIOS_FALSOS.get(username))
