import main


class CursorFalso:
    def __init__(self):
        self.ejecutadas = []

    def execute(self, sql, params=None):
        self.ejecutadas.append((sql, params))

    def fetchone(self):
        return {"id_notificacion": 1, "leida": False, "created_at": None}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ConexionFalsa:
    def __init__(self):
        self.cursor_falso = CursorFalso()
        self.commit_ejecutado = False

    def cursor(self, cursor_factory=None):
        return self.cursor_falso

    def commit(self):
        self.commit_ejecutado = True


def test_persiste_notificacion():
    conexion = ConexionFalsa()
    evento = {
        "id_alerta": 7,
        "zona": "quilpue",
        "tipo": "cambio_nivel",
        "nivel_anterior": "MEDIO",
        "nivel_nuevo": "ALTO",
        "probabilidad": 0.34,
        "mensaje": "Quilpue cambió de MEDIO a ALTO.",
    }

    fila = main.persistir_evento("alerta.generada", evento, conexion)

    assert conexion.commit_ejecutado is True
    sql, params = conexion.cursor_falso.ejecutadas[0]
    assert "INSERT INTO notificacion" in sql
    assert params[0] == "alerta"
    assert params[1] == "Cambio de nivel en Quilpue"
    assert params[3] == "quilpue"
    assert params[4] == "ALTO"
    assert fila["id_notificacion"] == 1
    assert fila["titulo"] == "Cambio de nivel en Quilpue"


def test_riesgo_calculado_genera_notificacion():
    datos = main.notificacion_desde_evento(
        "riesgo.calculado",
        {"zona": "valparaiso", "nivel_riesgo": "MEDIO", "probabilidad": 0.12},
    )

    assert datos["tipo"] == "cambio_riesgo"
    assert datos["nivel"] == "MEDIO"
    assert "Valparaiso" in datos["titulo"]


def test_notificacion_nueva_no_se_duplica():
    assert main.notificacion_desde_evento("notificacion.nueva", {"zona": "quilpue"}) is None


def test_bitacora_genera_evento():
    datos = main.notificacion_desde_evento(
        "bitacora.nueva",
        {
            "id_bitacora": 5,
            "usuario": "analista",
            "zona": "quilpue",
            "tipo_evento": "accion",
            "detalle": "Acción preventiva creada",
        },
    )

    assert datos["tipo"] == "evento"
    assert datos["usuario"] == "analista"
    assert "Quilpue" in datos["titulo"]
    assert datos["metadata"]["id_bitacora"] == 5


def test_no_consume_propia_notificacion_nueva():
    # El servicio publica `notificacion.nueva`; no debe re-consumir su propio
    # evento (evita un loop inútil de auto-consumo).
    assert "notificacion.nueva" not in main.ROUTING_KEYS


def test_registrar_estado_reutiliza_conexion():
    class ConexionReutilizable:
        def __init__(self):
            self.cursor_calls = 0
            self.commit_calls = 0
            self.closed = False

        def cursor(self):
            self.cursor_calls += 1
            return CursorFalso()

        def commit(self):
            self.commit_calls += 1

        def close(self):
            self.closed = True

    conexion = ConexionReutilizable()
    main.registrar_estado_servicio("ok", latencia_ms=5, conexion=conexion)

    assert conexion.cursor_calls == 1
    assert conexion.commit_calls == 1
    assert conexion.closed is False  # no debe cerrar una conexión ajena
