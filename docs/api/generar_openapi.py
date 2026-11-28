"""Genera las especificaciones OpenAPI de los servicios FastAPI de SIRIF.

Uso:
    python docs/api/generar_openapi.py

No necesita el stack levantado: importa cada app y vuelca `app.openapi()`.
Las variables de entorno se rellenan con valores ficticios solo para permitir
el import; no se conecta a la base ni a RabbitMQ.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SALIDA = Path(__file__).resolve().parent

SERVICIOS = [
    (
        "api-service",
        "openapi-api-service.json",
        {
            "JWT_SECRET": "export-openapi",
            "DATABASE_URL": "postgresql://usuario:clave@localhost:5432/sirif",
            "INTERNAL_TOKEN": "export-openapi",
            "DATA_SERVICE_URL": "http://data-service:8000",
            "RISK_SERVICE_URL": "http://risk-service:8001",
        },
    ),
    (
        "data-service",
        "openapi-data-service.json",
        {
            "DATABASE_URL": "postgresql://usuario:clave@localhost:5432/sirif",
            "INTERNAL_TOKEN": "export-openapi",
        },
    ),
    ("risk-service", "openapi-risk-service.json", {}),
]

CODIGO = (
    "import json, main; "
    "print(json.dumps(main.app.openapi(), ensure_ascii=False, indent=2))"
)


def main() -> int:
    fallos = 0
    for servicio, archivo, extra in SERVICIOS:
        entorno = os.environ.copy()
        entorno.update(extra)
        entorno["PYTHONPATH"] = str(RAIZ) + os.pathsep + entorno.get("PYTHONPATH", "")
        resultado = subprocess.run(
            [sys.executable, "-c", CODIGO],
            cwd=RAIZ / servicio,
            env=entorno,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if resultado.returncode != 0 or not resultado.stdout.lstrip().startswith("{"):
            print(f"[error] {servicio}: {resultado.stderr[-800:]}", file=sys.stderr)
            fallos += 1
            continue
        datos = json.loads(resultado.stdout)
        (SALIDA / archivo).write_text(
            json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"[ok] {archivo}: {len(datos.get('paths', {}))} rutas · "
            f"{datos['info'].get('title')} {datos['info'].get('version', '')}"
        )
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
