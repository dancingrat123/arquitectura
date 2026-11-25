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
def test_serializar_zona_incluye_probabilidad():
    from main import serializar_zona
    fila = ("quilpue", "ALTO", 5, 15, None, "rf-clima-region-v2",
            "Quilpue", "5801", "Open-Meteo", None, None, 0.34, True)
    data = serializar_zona(fila)
    assert data["probabilidad"] == 0.34
    assert data["fuera_ventana_estacional"] is True
    assert data["tipo_estimacion"] == "riesgo_meteorologico_regional"
    assert data["factores_predominantes"] == []
    assert data["origen"] == "automatico"


def test_serializar_zona_desde_dict():
    from main import serializar_zona
    fila = {
        "zona": "quilpue", "nivel_riesgo": "ALTO", "puntaje": 5,
        "puntaje_maximo": 15, "fecha_evaluacion": None,
        "version_modelo": "rf-clima-region-v2", "comuna": "Quilpué",
        "codigo_comuna": "5801", "fuente": "Open-Meteo", "fecha_medicion": None,
        "antiguedad_minutos": None, "probabilidad": 0.34,
        "fuera_ventana_estacional": True,
        "factores_predominantes": [{"variable": "TEMP_MAX"}],
        "origen": "on-demand", "usuario": "admin",
    }
    data = serializar_zona(fila)
    assert data["factores_predominantes"] == [{"variable": "TEMP_MAX"}]
    assert data["origen"] == "on-demand"
    assert data["usuario"] == "admin"


# --- Fase 1: núcleo analítico -------------------------------------------


def _headers() -> dict:
    respuesta = client.post("/token", data={"username": "admin", "password": "sirif2026"})
    return {"Authorization": f"Bearer {respuesta.json()['access_token']}"}


def _fila_riesgo(zona: str, nivel: str, probabilidad: float, rn: int = 1) -> dict:
    from datetime import datetime

    return {
        "zona": zona,
        "nivel_riesgo": nivel,
        "probabilidad": probabilidad,
        "puntaje": round(probabilidad * 15),
        "fecha_evaluacion": datetime(2026, 11, 18, 10, 0),
        "version_modelo": "rf-clima-region-v2",
        "usuario": None,
        "origen": "automatico",
        "factores_predominantes": [
            {"variable": "TEMP_MAX", "direccion": "aumenta", "peso": 1.0}
        ]
        if nivel == "ALTO"
        else [],
        "variables": {"temperatura": 32.5, "humedad": 25.0},
        "comuna": zona.capitalize(),
        "codigo_comuna": "5801",
        "rn": rn,
    }


def test_historial_una_fila(monkeypatch):
    import main

    fila = _fila_riesgo("quilpue", "ALTO", 0.34)
    monkeypatch.setattr(main, "consultar_historial", lambda *args, **kwargs: [fila])
    monkeypatch.setattr(main, "consultar_ultimas_zonas", lambda *args, **kwargs: [fila])
    monkeypatch.setattr(main, "contar_alertas_activas", lambda: 0)

    respuesta = client.get("/api/riesgo/quilpue/historial", headers=_headers())

    assert respuesta.status_code == 200
    data = respuesta.json()
    assert data["total"] == 1
    assert len(data["serie"]) == 1
    assert data["serie"][0]["nivel_riesgo"] == "ALTO"

    panel = client.get("/api/panel/resumen", headers=_headers()).json()
    assert panel["variacion_reciente"] == []


def test_historial_fecha_invalida():
    respuesta = client.get(
        "/api/riesgo/quilpue/historial?desde=no-es-fecha", headers=_headers()
    )
    assert respuesta.status_code == 422
    assert "desde" in respuesta.json()["detail"]


def test_comparar_max_tres():
    respuesta = client.get(
        "/api/comparar?zonas=quilpue,valparaiso,vina_del_mar,concon",
        headers=_headers(),
    )
    assert respuesta.status_code == 422
    assert respuesta.json()["detail"] == "máximo 3 zonas"


def test_explicacion_expone_ventana_estacional(monkeypatch):
    import main

    fila = _fila_riesgo("quilpue", "ALTO", 0.34)
    fila["fuera_ventana_estacional"] = True
    fila["variables"] = {
        "temperatura": 32.5, "humedad": 25.0, "viento": 28.0,
        "precipitacion": 0.0, "vegetacion": "alta", "incendios_historicos": 12,
    }
    monkeypatch.setattr(main, "consultar_ultimas_zonas", lambda *args, **kwargs: [fila])

    respuesta = client.get("/api/riesgo/quilpue/explicacion", headers=_headers())

    assert respuesta.status_code == 200
    data = respuesta.json()
    assert data["fuera_ventana_estacional"] is True
    assert "riesgo meteorológico" in data["advertencia"]
    assert data["variables"]["vegetacion"] == "alta"


def test_panel_resumen_forma(monkeypatch):
    import main

    filas = [
        _fila_riesgo("quilpue", "ALTO", 0.34),
        _fila_riesgo("valparaiso", "MEDIO", 0.12),
        _fila_riesgo("vina_del_mar", "BAJO", 0.02),
    ]
    monkeypatch.setattr(main, "consultar_ultimas_zonas", lambda *args, **kwargs: filas)
    monkeypatch.setattr(main, "contar_alertas_activas", lambda: 0)

    respuesta = client.get("/api/panel/resumen", headers=_headers())

    assert respuesta.status_code == 200
    data = respuesta.json()
    assert data["resumen_niveles"] == {"ALTO": 1, "MEDIO": 1, "BAJO": 1}
    assert data["zonas_criticas"][0]["zona"] == "quilpue"
    assert data["variables_predominantes"][0]["variable"] == "TEMP_MAX"
    assert data["total_zonas"] == 3
    assert data["alertas_activas"] == 0


def test_panel_resumen_alertas_activas(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "consultar_ultimas_zonas",
        lambda *args, **kwargs: [_fila_riesgo("quilpue", "ALTO", 0.34)],
    )
    monkeypatch.setattr(main, "contar_alertas_activas", lambda: 2)

    respuesta = client.get("/api/panel/resumen", headers=_headers())

    assert respuesta.status_code == 200
    assert respuesta.json()["alertas_activas"] == 2


def test_comparar_zonas_duplicadas():
    respuesta = client.get(
        "/api/comparar?zonas=quilpue,quilpue", headers=_headers()
    )
    assert respuesta.status_code == 422
    assert respuesta.json()["detail"] == "zonas duplicadas"


# --- Fase 2: simulación, recomendaciones y alertas -----------------------


def _evaluacion_simulacion() -> dict:
    return {
        "zona": "quilpue",
        "nivel_riesgo": "ALTO",
        "probabilidad": 0.34,
        "puntaje": 5,
        "features": {
            "TEMP_MED": 30.0,
            "TEMP_MAX": 30.0,
            "HUMEDAD_REL": 40.0,
            "VEL_VIENTO": 10.0,
            "PRECIPITACION": 0.0,
            "precip_7d": 2.0,
            "temp_media_7d": 28.0,
            "humedad_7d": 45.0,
            "precip_30d": 5.0,
        },
        "variables": {
            "temperatura": 30.0,
            "humedad": 40.0,
            "viento": 10.0,
            "precipitacion": 0.0,
            "vegetacion": "alta",
            "incendios_historicos": 12,
        },
        "factores_predominantes": [],
        "fuera_ventana_estacional": True,
        "version_modelo": "rf-clima-region-v2",
        "id_zona": 1,
        "region": "Valparaíso",
        "comuna": "Quilpué",
    }


def _resultado_simulacion() -> dict:
    return {
        "nivel_riesgo": "ALTO",
        "probabilidad": 0.56,
        "puntaje": 8,
        "puntaje_maximo": 15,
        "factores_predominantes": [],
        "fuera_ventana_estacional": True,
    }


def test_simulacion_no_escribe_resultado(monkeypatch):
    import main

    escrituras = []
    eventos = []
    bitacoras = []
    monkeypatch.setattr(main, "_leer_evaluacion_para_simular", lambda zona: _evaluacion_simulacion())
    monkeypatch.setattr(main, "_clasificar_escenario", lambda *args, **kwargs: _resultado_simulacion())
    monkeypatch.setattr(main, "_persistir_simulacion", lambda datos: 1)
    monkeypatch.setattr(main, "insertar_resultado_riesgo", lambda *args: escrituras.append(args))
    monkeypatch.setattr(main, "publicar_evento_seguro", lambda rk, ev: eventos.append(rk))
    monkeypatch.setattr(main, "publicar_evento", lambda conn, rk, ev: eventos.append(rk))
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: bitacoras.append(args))

    respuesta = client.post(
        "/api/simular",
        json={"zona": "quilpue", "ajustes": {"temperatura": 40.0, "humedad": 15.0}},
        headers=_headers(),
    )

    assert respuesta.status_code == 200
    assert escrituras == []
    assert "riesgo.calculado" not in eventos
    assert respuesta.json()["advertencia"].startswith("Simulación sobre el modelo regional")
    assert bitacoras, "la simulación debe registrarse en la bitácora"
    assert bitacoras[0][2] == "consulta"


def test_simulacion_recalcula_features(monkeypatch):
    import main

    capturado = {}

    def fake_clasificar(zona, features, variables, region, comuna=None):
        capturado["features"] = features
        capturado["comuna"] = comuna
        return _resultado_simulacion()

    monkeypatch.setattr(main, "_leer_evaluacion_para_simular", lambda zona: _evaluacion_simulacion())
    monkeypatch.setattr(main, "_clasificar_escenario", fake_clasificar)
    monkeypatch.setattr(main, "_persistir_simulacion", lambda datos: 1)
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: None)

    respuesta = client.post(
        "/api/simular",
        json={
            "zona": "quilpue",
            "ajustes": {
                "temperatura": 37.0,
                "humedad": 20.0,
                "viento": 50.0,
                "precipitacion": 0.0,
            },
        },
        headers=_headers(),
    )

    assert respuesta.status_code == 200
    features = capturado["features"]
    assert features["TEMP_MED"] == 37.0
    assert features["TEMP_MAX"] == 37.0
    assert features["HUMEDAD_REL"] == 20.0
    assert features["VEL_VIENTO"] == 50.0
    assert features["temp_media_7d"] == round(28.0 + (37.0 - 30.0) / 7.0, 4)
    assert features["humedad_7d"] == round(45.0 + (20.0 - 40.0) / 7.0, 4)
    assert features["precip_7d"] == 2.0
    assert features["precip_30d"] == 5.0
    # El simulador debe propagar la comuna para que risk-service use el modelo
    # comunal cuando corresponde (A5).
    assert capturado["comuna"] == "Quilpué"


def test_visualizador_no_simula():
    respuesta = client.post(
        "/api/simular",
        json={"zona": "quilpue", "ajustes": {"temperatura": 40.0}},
        headers=_headers_rol("visualizador"),
    )
    assert respuesta.status_code == 403
    assert "Rol insuficiente" in respuesta.json()["detail"]


def test_simulacion_usa_advertencia_del_modelo(monkeypatch):
    import main

    resultado_comunal = {
        "nivel_riesgo": "ALTO",
        "probabilidad": 0.12,
        "puntaje": 15,
        "factores_predominantes": [],
        "fuera_ventana_estacional": False,
        "advertencia": "Estimación de riesgo meteorológico comunal; no es pronóstico de ocurrencia.",
        "tipo_estimacion": "riesgo_meteorologico_comunal",
    }
    monkeypatch.setattr(main, "_leer_evaluacion_para_simular", lambda zona: _evaluacion_simulacion())
    monkeypatch.setattr(main, "_clasificar_escenario", lambda *args, **kwargs: resultado_comunal)
    monkeypatch.setattr(main, "_persistir_simulacion", lambda datos: 1)
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: None)

    respuesta = client.post(
        "/api/simular", json={"zona": "quilpue", "ajustes": {}}, headers=_headers()
    )

    assert respuesta.status_code == 200
    data = respuesta.json()
    assert "comunal" in data["advertencia"]
    assert data["tipo_estimacion"] == "riesgo_meteorologico_comunal"


def test_recomendaciones_alto_viento(monkeypatch):
    import main

    fila = _fila_riesgo("quilpue", "ALTO", 0.34)
    fila["variables"] = {"vegetacion": "alta", "viento": 30.0, "temperatura": 33.0}
    fila["factores_predominantes"] = [
        {"variable": "VEL_VIENTO", "direccion": "aumenta", "peso": 0.9}
    ]
    monkeypatch.setattr(main, "consultar_ultimas_zonas", lambda *args, **kwargs: [fila])

    respuesta = client.get("/api/riesgo/quilpue/recomendaciones", headers=_headers())

    assert respuesta.status_code == 200
    recomendaciones = respuesta.json()["recomendaciones"]
    assert recomendaciones[0]["codigo"] == "ALTO_VIENTO"
    assert recomendaciones[0]["prioridad"] == 1
    assert recomendaciones[0]["categoria"] == "prevencion"
    codigos = [recomendacion["codigo"] for recomendacion in recomendaciones]
    assert "ALTO_VEGETACION" in codigos


def test_deteccion_cambio_nivel():
    import main

    registradas = []

    def fake_insertar(zona, id_zona, alerta):
        fila = {**alerta, "id_alerta": 7, "zona": zona, "created_at": None}
        registradas.append(fila)
        return fila

    resultado = main.detectar_alertas(
        "quilpue",
        1,
        "MEDIO",
        "ALTO",
        0.10,
        0.34,
        insertar=fake_insertar,
        publicar=lambda alerta: None,
    )

    assert len(resultado) == 1
    assert registradas[0]["tipo"] == "cambio_nivel"
    assert registradas[0]["nivel_anterior"] == "MEDIO"
    assert registradas[0]["nivel_nuevo"] == "ALTO"


def test_deteccion_umbral():
    import main

    registradas = []

    def fake_insertar(zona, id_zona, alerta):
        fila = {**alerta, "id_alerta": 8, "zona": zona, "created_at": None}
        registradas.append(fila)
        return fila

    main.detectar_alertas(
        "quilpue",
        1,
        "MEDIO",
        "MEDIO",
        0.30,
        0.60,
        insertar=fake_insertar,
        publicar=lambda alerta: None,
    )

    assert [alerta["tipo"] for alerta in registradas] == ["umbral"]
    assert registradas[0]["umbral"] == main.UMBRAL_ALERTA


def test_deteccion_umbral_comunal():
    import main

    registradas = []

    def fake_insertar(zona, id_zona, alerta):
        fila = {**alerta, "id_alerta": 9, "zona": zona, "created_at": None}
        registradas.append(fila)
        return fila

    main.detectar_alertas(
        "quilpue", 1, "MEDIO", "MEDIO", 0.05, 0.12,
        insertar=fake_insertar, publicar=lambda alerta: None, umbral=0.10,
    )

    assert [alerta["tipo"] for alerta in registradas] == ["umbral"]
    assert registradas[0]["umbral"] == 0.10


def test_umbral_alerta_por_modelo():
    import main

    assert main.umbral_alerta_para("rf-clima-region-v2") == main.UMBRAL_ALERTA
    assert main.umbral_alerta_para("rf-comunal-clima-v1") == main.UMBRAL_ALERTA_COMUNAL


# --- Fase 3: roles, priorización, gestión territorial y bitácora ----------


def _headers_rol(username: str) -> dict:
    respuesta = client.post(
        "/token", data={"username": username, "password": "sirif2026"}
    )
    assert respuesta.status_code == 200, respuesta.text
    return {"Authorization": f"Bearer {respuesta.json()['access_token']}"}


def test_token_devuelve_rol():
    respuesta = client.post(
        "/token", data={"username": "analista", "password": "sirif2026"}
    )
    assert respuesta.status_code == 200
    data = respuesta.json()
    assert data["rol"] == "analista"
    assert data["nombre_completo"] == "Analista SIRIF"
    assert data["access_token"]


def test_usuarios_no_expone_hash(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "_listar_usuarios_db",
        lambda: [
            {
                "id_usuario": 1,
                "username": "admin",
                "nombre_completo": "Administrador SIRIF",
                "rol": "admin",
                "hash_password": "secreto",
                "activo": True,
                "created_at": None,
            }
        ],
    )

    respuesta = client.get("/api/usuarios", headers=_headers_rol("admin"))

    assert respuesta.status_code == 200
    data = respuesta.json()
    assert data["total"] == 1
    assert "hash_password" not in data["usuarios"][0]
    assert set(data["usuarios"][0]) == {
        "id_usuario", "username", "nombre_completo", "rol", "activo", "created_at"
    }


def test_visualizador_no_crea_accion():
    respuesta = client.post(
        "/api/acciones",
        json={"zona": "quilpue", "descripcion": "Limpieza", "tipo": "limpieza"},
        headers=_headers_rol("visualizador"),
    )
    assert respuesta.status_code == 403
    assert "Rol insuficiente" in respuesta.json()["detail"]


class _CursorFalso:
    def __init__(self, filas=None):
        self.filas = list(filas or [])
        self.ejecutadas = []

    def execute(self, sql, params=None):
        self.ejecutadas.append((sql, params))

    def fetchone(self):
        return self.filas.pop(0) if self.filas else None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _ConexionFalsa:
    def __init__(self, filas=None):
        self.cursor_falso = _CursorFalso(filas)
        self.commits = 0

    def cursor(self, cursor_factory=None):
        return self.cursor_falso

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def test_analista_si_crea_observacion(monkeypatch):
    import main

    fila = {
        "id_observacion": 11,
        "zona": "quilpue",
        "tipo": "vegetacion_seca",
        "descripcion": "Vegetación seca en quebrada",
        "severidad": "alta",
        "estado": "abierta",
        "usuario": "analista",
        "latitud": -33.04,
        "longitud": -71.44,
        "created_at": None,
    }

    class _Cursor(_CursorFalso):
        def fetchone(self):
            return fila

    class _Ctx:
        def __enter__(self):
            return _Cursor()

        def __exit__(self, *args):
            return False

    registros = []
    monkeypatch.setattr(main, "cursor_db", lambda commit=False: _Ctx())
    monkeypatch.setattr(
        main, "registrar_bitacora", lambda *args, **kwargs: registros.append(args)
    )

    respuesta = client.post(
        "/api/observaciones",
        json={
            "zona": "quilpue",
            "tipo": "vegetacion_seca",
            "descripcion": "Vegetación seca en quebrada",
            "severidad": "alta",
            "latitud": -33.04,
            "longitud": -71.44,
        },
        headers=_headers_rol("analista"),
    )

    assert respuesta.status_code == 201, respuesta.text
    data = respuesta.json()
    assert data["id_observacion"] == 11
    assert data["usuario"] == "analista"
    assert registros and registros[0][2] == "observacion"


def _datos_prioridad_falsos():
    return [
        {
            "zona": "quilpue", "comuna": "Quilpué", "nivel_riesgo": "ALTO",
            "probabilidad": 0.34, "vegetacion": "alta",
            "viviendas_expuestas": 5000, "colegios": 4, "hospitales": 1,
            "hidrantes": 4, "infraestructura_total": 9, "poblacion": 150000,
        },
        {
            "zona": "valparaiso", "comuna": "Valparaíso", "nivel_riesgo": "MEDIO",
            "probabilidad": 0.12, "vegetacion": "baja",
            "viviendas_expuestas": 1000, "colegios": 1, "hospitales": 0,
            "hidrantes": 0, "infraestructura_total": 1, "poblacion": 300000,
        },
        {
            "zona": "vina_del_mar", "comuna": "Viña del Mar", "nivel_riesgo": "BAJO",
            "probabilidad": 0.02, "vegetacion": "media",
            "viviendas_expuestas": 0, "colegios": 0, "hospitales": 0,
            "hidrantes": 0, "infraestructura_total": 0, "poblacion": 330000,
        },
    ]


def test_prioridad_orden_desc(monkeypatch):
    import main

    monkeypatch.setattr(main, "_datos_prioridad", _datos_prioridad_falsos)
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: None)

    respuesta = client.get("/api/prioridad", headers=_headers_rol("visualizador"))

    assert respuesta.status_code == 200
    zonas = respuesta.json()["zonas"]
    assert [zona["zona"] for zona in zonas] == ["quilpue", "valparaiso", "vina_del_mar"]
    assert [zona["posicion"] for zona in zonas] == [1, 2, 3]
    assert zonas[0]["puntaje_prioridad"] >= zonas[1]["puntaje_prioridad"]


def test_prioridad_formula(monkeypatch):
    import main

    monkeypatch.setattr(main, "_datos_prioridad", _datos_prioridad_falsos)
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: None)

    respuesta = client.get("/api/prioridad", headers=_headers_rol("admin"))

    assert respuesta.status_code == 200
    zonas = {zona["zona"]: zona for zona in respuesta.json()["zonas"]}
    assert zonas["quilpue"]["puntaje_prioridad"] == 59
    assert zonas["quilpue"]["factores"] == {
        "riesgo": 0.34,
        "viviendas_expuestas": 5000,
        "colegios": 4,
        "hospitales": 1,
        "infraestructura_total": 9,
        "vegetacion": "alta",
        "poblacion": 150000,
    }
    esperado = main.calcular_puntaje_prioridad(0.34, 5000, 4, 1, 4, "alta")
    assert zonas["quilpue"]["puntaje_prioridad"] == esperado


def test_visualizador_no_dispara_inferencia(monkeypatch):
    import main

    class _Cursor:
        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class _Ctx:
        def __enter__(self):
            return _Cursor()

        def __exit__(self, *args):
            return False

    inferencias = []
    monkeypatch.setattr(main, "cursor_db", lambda commit=False: _Ctx())
    monkeypatch.setattr(main, "inferir_y_guardar", lambda *args, **kwargs: inferencias.append(1))
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: None)

    respuesta = client.get("/api/riesgo/quilpue", headers=_headers_rol("visualizador"))

    assert respuesta.status_code == 404
    assert inferencias == []


def test_bitacora_autoregistro(monkeypatch):
    import main

    fila_riesgo = {
        "nivel_riesgo": "ALTO",
        "puntaje": 5,
        "puntaje_maximo": 15,
        "fecha_evaluacion": None,
        "version_modelo": "rf-clima-region-v2",
        "fuente": "Open-Meteo",
        "fecha_medicion": None,
        "antiguedad_minutos": 12.0,
        "probabilidad": 0.34,
        "fuera_ventana_estacional": True,
        "factores_predominantes": [],
        "origen": "automatico",
    }

    class _Cursor(_CursorFalso):
        def fetchone(self):
            return fila_riesgo

    class _Ctx:
        def __enter__(self):
            return _Cursor()

        def __exit__(self, *args):
            return False

    registros = []
    monkeypatch.setattr(main, "cursor_db", lambda commit=False: _Ctx())
    monkeypatch.setattr(
        main, "registrar_bitacora", lambda *args, **kwargs: registros.append(args)
    )

    respuesta = client.get("/api/riesgo/quilpue", headers=_headers_rol("analista"))

    assert respuesta.status_code == 200
    assert registros, "la consulta debe registrarse en bitácora"
    assert registros[0][1] == "quilpue"
    assert registros[0][2] == "consulta"


# --- Fase 4: reportes, exportaciones y asistente --------------------------


def _fila_fase4(zona: str = "quilpue", nivel: str = "ALTO", probabilidad: float = 0.34) -> dict:
    from datetime import datetime

    return {
        "zona": zona,
        "nivel_riesgo": nivel,
        "probabilidad": probabilidad,
        "puntaje": round(probabilidad * 15),
        "fecha_evaluacion": datetime(2026, 11, 18, 10, 0),
        "version_modelo": "rf-clima-region-v2",
        "usuario": None,
        "origen": "automatico",
        "factores_predominantes": [
            {
                "variable": "TEMP_MAX",
                "etiqueta": "Temperatura máxima",
                "valor": 32.4,
                "unidad": "°C",
                "direccion": "aumenta",
                "peso": 0.87,
            },
            {
                "variable": "HUMEDAD_REL",
                "etiqueta": "Humedad relativa",
                "valor": 25.0,
                "unidad": "%",
                "direccion": "reduce",
                "peso": 0.71,
            },
        ],
        "variables": {
            "temperatura": 32.5,
            "humedad": 25.0,
            "viento": 28.0,
            "precipitacion": 0.0,
            "vegetacion": "alta",
            "incendios_historicos": 12,
        },
        "comuna": "Quilpué" if zona == "quilpue" else zona.capitalize(),
        "codigo_comuna": "5801",
        "fuera_ventana_estacional": True,
    }


def test_asistente_por_que(monkeypatch):
    import main

    monkeypatch.setattr(
        main, "consultar_ultimas_zonas", lambda *args, **kwargs: [_fila_fase4()]
    )
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: 1)

    respuesta = client.post(
        "/api/asistente/preguntar",
        json={"pregunta": "¿por qué quilpué tiene riesgo alto?"},
        headers=_headers_rol("visualizador"),
    )

    assert respuesta.status_code == 200, respuesta.text
    data = respuesta.json()
    assert data["fuente"] == "local-determinista"
    assert "riesgo meteorológico" in data["respuesta"]
    assert data["datos_utilizados"]["zona"] == "quilpue"
    assert data["advertencia"] == main.ADVERTENCIA_RIESGO


def test_asistente_zona_inexistente(monkeypatch):
    import main

    monkeypatch.setattr(
        main, "consultar_ultimas_zonas", lambda *args, **kwargs: [_fila_fase4()]
    )
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: 1)

    respuesta = client.post(
        "/api/asistente/preguntar",
        json={"pregunta": "¿por qué talca tiene riesgo alto?"},
        headers=_headers_rol("analista"),
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["respuesta"] == "No tengo datos para la zona 'talca'."
    assert respuesta.json()["fuente"] == "local-determinista"


def test_asistente_mayor_riesgo(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "consultar_ultimas_zonas",
        lambda *args, **kwargs: [
            _fila_fase4("quilpue", "ALTO", 0.34),
            _fila_fase4("valparaiso", "MEDIO", 0.12),
        ],
    )
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: 1)

    respuesta = client.post(
        "/api/asistente/preguntar",
        json={"pregunta": "¿qué zona tiene el mayor riesgo?"},
        headers=_headers_rol("visualizador"),
    )

    assert respuesta.status_code == 200, respuesta.text
    data = respuesta.json()
    assert data["fuente"] == "local-determinista"
    assert "Quilpué" in data["respuesta"]
    assert data["datos_utilizados"]["zona"] == "quilpue"


def test_asistente_mayor_riesgo_sin_palabra_zona(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "consultar_ultimas_zonas",
        lambda *args, **kwargs: [
            _fila_fase4("quilpue", "ALTO", 0.34),
            _fila_fase4("valparaiso", "MEDIO", 0.12),
        ],
    )
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: 1)

    respuesta = client.post(
        "/api/asistente/preguntar",
        json={"pregunta": "¿cuál tiene el mayor riesgo?"},
        headers=_headers_rol("visualizador"),
    )

    assert respuesta.status_code == 200, respuesta.text
    assert "Quilpué" in respuesta.json()["respuesta"]


def test_asistente_fuera_dominio(monkeypatch):
    import main

    monkeypatch.setattr(
        main, "consultar_ultimas_zonas", lambda *args, **kwargs: [_fila_fase4()]
    )
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: 1)

    def _cliente_prohibido(*args, **kwargs):
        raise AssertionError("no debe llamarse a un LLM/HTTP durante el request")

    monkeypatch.setattr(main.httpx, "Client", _cliente_prohibido)

    respuesta = client.post(
        "/api/asistente/preguntar",
        json={"pregunta": "¿cuál es el clima en Marte?"},
        headers=_headers_rol("visualizador"),
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["respuesta"] == main.RESPUESTA_FUERA_DOMINIO


def test_asistente_sin_llm_no_llama_externo(monkeypatch):
    import main

    monkeypatch.setattr(main, "LLM_URL", "")
    monkeypatch.setattr(main, "LLM_API_KEY", "")
    monkeypatch.setattr(
        main, "consultar_ultimas_zonas", lambda *args, **kwargs: [_fila_fase4()]
    )
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: 1)

    def _cliente_prohibido(*args, **kwargs):
        raise AssertionError("sin LLM configurado no debe haber HTTP externo")

    monkeypatch.setattr(main.httpx, "Client", _cliente_prohibido)

    respuesta = client.post(
        "/api/asistente/preguntar",
        json={"pregunta": "¿qué nivel de riesgo tiene quilpue?"},
        headers=_headers_rol("visualizador"),
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["fuente"] == "local-determinista"


def test_export_csv_columnas(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "consultar_ultimas_zonas",
        lambda *args, **kwargs: [
            _fila_fase4("quilpue", "ALTO", 0.34),
            _fila_fase4("valparaiso", "MEDIO", 0.12),
            _fila_fase4("vina_del_mar", "BAJO", 0.02),
        ],
    )
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: 1)

    respuesta = client.get("/api/exportar/csv", headers=_headers_rol("visualizador"))

    assert respuesta.status_code == 200
    assert respuesta.text.splitlines()[0] == (
        "zona,comuna,nivel_riesgo,probabilidad,puntaje,fecha_evaluacion,"
        "version_modelo,fuera_ventana_estacional,temperatura,humedad,viento,vegetacion"
    )
    assert len(respuesta.text.strip().splitlines()) == 4


def test_reporte_pdf_advertencias(monkeypatch):
    import main

    monkeypatch.setattr(
        main, "consultar_ultimas_zonas", lambda *args, **kwargs: [_fila_fase4()]
    )
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: 1)

    respuesta = client.get(
        "/api/reportes/quilpue/pdf", headers=_headers_rol("analista")
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.content.startswith(b"%PDF")
    contenido = _texto_pdf(respuesta.content)
    assert "riesgo meteorológico" in contenido
    assert "Estimación de riesgo meteorológico regional" in contenido
    assert "Fuera de la ventana estacional" in contenido


def _texto_pdf(contenido: bytes) -> str:
    """Desescapa las secuencias octales con que reportlab codifica los acentos."""
    import re

    texto = contenido.decode("latin-1")
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), texto)


def test_exportar_pdf_multizona(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "consultar_ultimas_zonas",
        lambda *args, **kwargs: [
            _fila_fase4("quilpue", "ALTO", 0.34),
            _fila_fase4("valparaiso", "MEDIO", 0.12),
        ],
    )
    monkeypatch.setattr(main, "registrar_bitacora", lambda *args, **kwargs: 1)

    respuesta = client.get("/api/exportar/pdf", headers=_headers_rol("visualizador"))

    assert respuesta.status_code == 200
    assert respuesta.content.startswith(b"%PDF")
    contenido = _texto_pdf(respuesta.content)
    assert "Resumen multi-zona" in contenido
    assert "Estimación de riesgo meteorológico regional" in contenido


def test_serializar_zona_comunal():
    from main import serializar_zona
    fila = ("quilpue", "ALTO", 2, 15, None, "rf-comunal-clima-v1",
            "Quilpue", "5801", "Open-Meteo", None, None, 0.13, False)
    data = serializar_zona(fila)
    assert data["tipo_estimacion"] == "riesgo_meteorologico_comunal"


def test_jwt_secret_fail_fast(monkeypatch):
    import pytest

    import auth

    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        auth._secret_key()


def test_pool_usuarios_singleton(monkeypatch):
    import auth
    import psycopg2.pool

    creadas = []

    monkeypatch.setattr(
        psycopg2.pool,
        "ThreadedConnectionPool",
        lambda *args, **kwargs: (creadas.append(1), object())[1],
    )
    monkeypatch.setattr(auth, "_pool", None)

    p1 = auth._get_pool()
    p2 = auth._get_pool()

    assert p1 is p2
    assert len(creadas) == 1


# --- Acceso público (mapa sin sesión) -------------------------------------


def _respuesta_zonas_publicas():
    return [
        {
            "zona": "quilpue",
            "comuna": "Quilpué",
            "codigo_comuna": "5801",
            "nivel_riesgo": "ALTO",
            "probabilidad": 0.21,
            "fecha_evaluacion": None,
            "fuente": "Open-Meteo",
            "fecha_medicion": None,
            "antiguedad_minutos": 12.5,
            "version_modelo": "rf-comunal-clima-v1",
            "fuera_ventana_estacional": False,
        }
    ]


def test_zonas_publicas_sin_token(monkeypatch):
    import main

    filas = _respuesta_zonas_publicas()

    class _Cursor:
        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return filas

    class _Ctx:
        def __enter__(self):
            return _Cursor()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(main, "cursor_db", lambda commit=False: _Ctx())

    respuesta = client.get("/api/publico/zonas")

    assert respuesta.status_code == 200
    data = respuesta.json()
    assert data["total"] == 1
    zona = data["zonas"][0]
    assert zona["zona"] == "quilpue"
    assert zona["nivel_riesgo"] == "ALTO"
    assert zona["tipo_estimacion"] == "riesgo_meteorologico_comunal"


def test_zonas_publicas_no_expone_datos_sensibles(monkeypatch):
    import main

    filas = _respuesta_zonas_publicas()

    class _Cursor:
        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return filas

    class _Ctx:
        def __enter__(self):
            return _Cursor()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(main, "cursor_db", lambda commit=False: _Ctx())

    zona = client.get("/api/publico/zonas").json()["zonas"][0]

    for campo in ("usuario", "origen", "factores_predominantes", "variables"):
        assert campo not in zona
    assert client.get("/api/riesgo/todas").status_code == 401


def test_distritos_publicos_sin_token(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "_data_service_get",
        lambda ruta: {"distritos": {"580101": "ALTO"}},
    )

    respuesta = client.get("/api/publico/distritos")

    assert respuesta.status_code == 200
    assert respuesta.json()["distritos"]["580101"] == "ALTO"


# --- Auto-registro público (rol visualizador) -----------------------------


def _ctx_registro(fila, capturado=None):
    class _Cursor:
        def execute(self, sql, params=None):
            if capturado is not None:
                capturado["sql"] = sql
                capturado["params"] = params

        def fetchone(self):
            return fila

    class _Ctx:
        def __enter__(self):
            return _Cursor()

        def __exit__(self, *args):
            return False

    return _Ctx()


def test_registro_publico_crea_visualizador(monkeypatch):
    import main

    capturado = {}
    fila = {
        "id_usuario": 9,
        "username": "nuevo.usuario",
        "nombre_completo": "Nuevo Usuario",
        "rol": "visualizador",
        "activo": True,
        "created_at": None,
    }
    monkeypatch.setattr(
        main, "cursor_db", lambda commit=False: _ctx_registro(fila, capturado)
    )

    respuesta = client.post(
        "/api/publico/registro",
        json={
            "username": "Nuevo.Usuario",
            "password": "clave-segura-123",
            "nombre_completo": "Nuevo Usuario",
        },
    )

    assert respuesta.status_code == 201
    data = respuesta.json()
    assert data["rol"] == "visualizador"
    assert data["activo"] is True
    assert capturado["params"][0] == "nuevo.usuario"
    assert capturado["params"][1] == "Nuevo Usuario"
    assert "visualizador" in capturado["sql"]


def test_registro_publico_ignora_rol_enviado(monkeypatch):
    import main

    capturado = {}
    fila = {
        "id_usuario": 10,
        "username": "otro.usuario",
        "nombre_completo": "otro.usuario",
        "rol": "visualizador",
        "activo": True,
        "created_at": None,
    }
    monkeypatch.setattr(
        main, "cursor_db", lambda commit=False: _ctx_registro(fila, capturado)
    )

    respuesta = client.post(
        "/api/publico/registro",
        json={
            "username": "otro.usuario",
            "password": "clave-segura-123",
            "rol": "admin",
            "activo": True,
        },
    )

    assert respuesta.status_code == 201
    assert respuesta.json()["rol"] == "visualizador"
    assert "admin" not in capturado["params"]


def test_registro_publico_duplicado(monkeypatch):
    import main
    import psycopg2.errors

    class _Cursor:
        def execute(self, sql, params=None):
            raise psycopg2.errors.UniqueViolation("duplicado")

        def fetchone(self):
            return None

    class _Ctx:
        def __enter__(self):
            return _Cursor()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(main, "cursor_db", lambda commit=False: _Ctx())

    respuesta = client.post(
        "/api/publico/registro",
        json={"username": "repetido", "password": "clave-segura-123"},
    )

    assert respuesta.status_code == 409
    assert "ya está registrado" in respuesta.json()["detail"]


def test_registro_publico_password_corta():
    respuesta = client.post(
        "/api/publico/registro",
        json={"username": "usuario.valido", "password": "corta"},
    )

    assert respuesta.status_code == 422
    assert "contraseña" in respuesta.json()["detail"]


def test_registro_publico_username_invalido():
    respuesta = client.post(
        "/api/publico/registro",
        json={"username": "usuario con espacios", "password": "clave-segura-123"},
    )

    assert respuesta.status_code == 422
    assert "usuario inválido" in respuesta.json()["detail"]
