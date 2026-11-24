import json

import main


class CursorFalso:
    def __init__(self):
        self.ejecutadas = []

    def execute(self, sql, params=None):
        self.ejecutadas.append((sql, params))


def test_heartbeat_estado():
    cur = CursorFalso()

    main.guardar_estado(
        cur,
        "api-service",
        "ok",
        latencia_ms=12,
        detalle={"http_status": 200},
    )

    assert len(cur.ejecutadas) == 1
    sql, params = cur.ejecutadas[0]
    assert "estado_servicio" in sql
    assert "ON CONFLICT (servicio)" in sql
    assert params[0] == "api-service"
    assert params[1] == "ok"
    assert json.loads(params[2]) == {"http_status": 200}
    assert params[3] == 12


def test_medir_postgres_ok():
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            pass

    class Conexion:
        def cursor(self, cursor_factory=None):
            return Cursor()

    estado = main._medir_postgres(Conexion())

    assert estado["servicio"] == "postgres"
    assert estado["estado"] == "ok"
    assert isinstance(estado["latencia_ms"], int)


def test_medir_postgres_caido():
    class Conexion:
        def cursor(self, cursor_factory=None):
            raise RuntimeError("conexión caída")

    estado = main._medir_postgres(Conexion())

    assert estado["servicio"] == "postgres"
    assert estado["estado"] == "caido"
