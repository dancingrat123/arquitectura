"""Autenticación y autorización de SIRIF.

Los usuarios viven en la tabla `usuario` (sembrada por `data-service` desde
`ADMIN_PASSWORD_HASH` y los hashes opcionales de desarrollo). Los JWT incluyen
el rol para que el frontend lo muestre, pero cada endpoint vuelve a validar el
rol contra la base de datos mediante `require_rol`.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg2
import psycopg2.pool
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from psycopg2.extras import RealDictCursor

def _secret_key() -> str:
    valor = os.getenv("JWT_SECRET")
    if not valor:
        raise RuntimeError(
            "Falta JWT_SECRET: define la clave para firmar los tokens (fail-fast)"
        )
    return valor


SECRET_KEY = _secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

ROLES_VALIDOS = ("admin", "analista", "visualizador")
ROLES_LECTURA = ("admin", "analista", "visualizador")
ROLES_ESCRITURA = ("admin", "analista")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except (ValueError, TypeError):
        return False


def _database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("Falta DATABASE_URL para autenticar usuarios")
    return url


_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Pool perezoso de conexiones para la autenticación (evita una conexión
    nueva por request)."""
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 5, _database_url())
    return _pool


def buscar_usuario(username: str) -> dict | None:
    """Lee el usuario activo desde PostgreSQL (nunca desde memoria)."""
    if not username:
        return None
    conn = _get_pool().getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id_usuario, username, nombre_completo, rol, hash_password, activo
                FROM usuario
                WHERE username = %s
                LIMIT 1
                """,
                (username,),
            )
            fila = cur.fetchone()
            return dict(fila) if fila else None
    finally:
        _get_pool().putconn(conn)


def autenticar_usuario(username: str, password: str) -> dict | None:
    usuario = buscar_usuario(username)
    if not usuario or not usuario.get("activo", True):
        return None
    if not verify_password(password, usuario.get("hash_password") or ""):
        return None
    return usuario


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=15)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido o expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        if not username:
            raise credentials_exception
    except JWTError as error:
        raise credentials_exception from error

    usuario = buscar_usuario(username)
    if not usuario or not usuario.get("activo", True):
        raise credentials_exception
    return usuario


def require_rol(*roles: str):
    """Dependencia que exige uno de los roles indicados (403 si no aplica)."""

    def verificador(usuario: dict = Depends(get_current_user)) -> dict:
        if usuario.get("rol") not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Rol insuficiente para esta operación",
            )
        return usuario

    return verificador
