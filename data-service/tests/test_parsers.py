from datetime import datetime, timedelta

import parsers


def test_normalizar_texto_y_slugify():
    assert parsers.normalizar_texto("Valparaíso") == "valparaiso"
    assert parsers.slugify("Viña del Mar") == "vina_del_mar"
    assert parsers.slugify("  Quilpué  ") == "quilpue"


def test_leer_conaf(tmp_path):
    ruta = tmp_path / "conaf.csv"
    ruta.write_text(
        '"region","nro_inc_act","nro_inc_ant","nro_inc_prom_quint","sup_afec_act",'
        '"sup_afec_ant","fecha_creacion","fecha"\n'
        'Valparaíso,11,10,4.0,8.56,28.46,2026-09-28,2026-09-28\n'
        'Maule,2,1,1.0,3.0,4.0,2026-09-28,2026-09-28\n',
        encoding="utf-8",
    )
    registros = parsers.leer_conaf(str(ruta))
    assert len(registros) == 2
    valparaiso = parsers.filtrar_region(registros)
    assert len(valparaiso) == 1
    assert valparaiso[0]["nro_incendios"] == 11
    assert valparaiso[0]["nro_incendios_ant"] == 10
    assert valparaiso[0]["superficie_afectada_ant"] == 28.46
    assert valparaiso[0]["periodo"] == datetime(2026, 9, 28).date()


def test_leer_clima_agrupa_por_estacion(tmp_path):
    ruta = tmp_path / "clima.csv"
    filas = [
        '"ema_ia_id","rtd_c_variableid","rtd_f_valor","rtd_dt_marcatiempo"',
        "174,TEMP_MED,10.0,2026-09-28 09:00:00.000 -0300",
        "174,TEMP_MED,12.0,2026-09-28 09:00:00.000 -0300",
        "174,HUMEDAD_REL,80.0,2026-09-28 09:00:00.000 -0300",
    ]
    ruta.write_text("\n".join(filas) + "\n", encoding="utf-8")
    lecturas, fecha = parsers.leer_clima(str(ruta))
    assert lecturas["174"]["TEMP_MED"] == 11.0
    assert lecturas["174"]["HUMEDAD_REL"] == 80.0
    assert fecha == datetime(2026, 9, 28, 12, 0)


def test_estacion_mas_cercana_prefiere_cercania_y_completitud():
    estaciones = [
        {"ema_ia_id": "1", "nombre": "Cerca", "region": "Valparaíso", "latitud": -33.05, "longitud": -71.44, "activa": True},
        {"ema_ia_id": "2", "nombre": "Lejos", "region": "Valparaíso", "latitud": -32.0, "longitud": -71.0, "activa": True},
        {"ema_ia_id": "3", "nombre": "Otra region", "region": "Maule", "latitud": -33.05, "longitud": -71.44, "activa": True},
    ]
    lecturas = {
        "1": {"TEMP_MED": 16.0, "HUMEDAD_REL": 75.0, "VEL_VIENTO": 3.0, "PRECIPITACION": 0.2},
        "2": {"TEMP_MED": 30.0, "HUMEDAD_REL": 20.0, "VEL_VIENTO": 9.0},
        "3": {"TEMP_MED": 99.0, "HUMEDAD_REL": 99.0, "VEL_VIENTO": 99.0},
    }
    elegida = parsers.estacion_mas_cercana(-33.0486, -71.4425, estaciones, lecturas)
    assert elegida["estacion"]["ema_ia_id"] == "1"
    assert elegida["temperatura"] == 16.0


def test_parse_openmeteo_diario_lista():
    payload = [
        {
            "latitude": -33.05,
            "longitude": -71.44,
            "daily": {
                "time": ["2026-11-17", "2026-11-18"],
                "temperature_2m_mean": [17.4, 18.2],
                "temperature_2m_max": [22.1, 23.0],
                "relative_humidity_2m_mean": [70, 68],
                "wind_speed_10m_max": [15.2, 18.4],
                "precipitation_sum": [0.0, 1.2],
            },
        }
    ]
    [item] = parsers.parse_openmeteo_diario(payload)
    assert item["valido"] is True
    assert len(item["serie"]) == 2
    assert item["serie"][-1]["TEMP_MED"] == 18.2
    assert item["serie"][-1]["TEMP_MAX"] == 23.0
    assert item["serie"][-1]["HUMEDAD_REL"] == 68.0
    assert item["serie"][-1]["VEL_VIENTO"] == 18.4
    assert item["serie"][-1]["PRECIPITACION"] == 1.2


def test_parse_openmeteo_diario_nulos_no_rompe():
    payload = {
        "latitude": -33.0,
        "longitude": -71.0,
        "daily": {
            "time": ["2026-11-18"],
            "temperature_2m_mean": [None],
            "temperature_2m_max": [None],
            "relative_humidity_2m_mean": [None],
            "wind_speed_10m_max": [None],
            "precipitation_sum": [None],
        },
    }
    [item] = parsers.parse_openmeteo_diario(payload)
    assert item["valido"] is False
    assert item["serie"] == []


def test_parse_openmeteo_diario_sin_daily():
    [item] = parsers.parse_openmeteo_diario({"latitude": -33.0, "longitude": -71.0})
    assert item["valido"] is False
    assert item["serie"] == []


def test_construir_features_clima_rodadura():
    serie = [
        {
            "fecha": datetime(2026, 9, 1) + timedelta(days=i),
            "TEMP_MED": 10 + i,
            "TEMP_MAX": 15 + i,
            "HUMEDAD_REL": 80 - i,
            "VEL_VIENTO": 5.0,
            "PRECIPITACION": 1.0,
        }
        for i in range(31)
    ]
    f = parsers.construir_features_clima(serie)
    assert set(f) == {
        "TEMP_MED",
        "TEMP_MAX",
        "HUMEDAD_REL",
        "VEL_VIENTO",
        "PRECIPITACION",
        "precip_7d",
        "temp_media_7d",
        "humedad_7d",
        "precip_30d",
    }
    assert f["TEMP_MED"] == 40.0
    assert f["precip_7d"] == 7.0
    assert f["precip_30d"] == 30.0
    assert abs(f["temp_media_7d"] - 37.0) < 1e-6
    assert abs(f["humedad_7d"] - 53.0) < 1e-6


def test_construir_features_clima_con_un_dia_no_falla():
    f = parsers.construir_features_clima(
        [
            {
                "fecha": datetime(2026, 11, 18),
                "TEMP_MED": 16.8,
                "TEMP_MAX": 22.0,
                "HUMEDAD_REL": 75.0,
                "VEL_VIENTO": 2.7,
                "PRECIPITACION": 0.4,
            }
        ]
    )
    assert f["TEMP_MED"] == 16.8
    assert f["precip_7d"] == 0.4
    assert all(isinstance(v, float) for v in f.values())


def test_distrito_desde_feature():
    feature = {
        "geometry": {"type": "Point", "coordinates": [-71.0, -33.0]},
        "properties": {
            "CODIGO_C17": "580107",
            "COMUNA": "5801",
            "NOM_COMUNA": "QUILPUÉ",
            "TIPO_DISTR": "RURAL",
            "T_POB": 28,
            "SUPERFICIE": 84.9,
        },
    }
    distrito = parsers.distrito_desde_feature(feature)
    assert distrito["codigo_distrito"] == "580107"
    assert distrito["codigo_comuna"] == "5801"
    assert distrito["poblacion"] == 28
    assert "coordinates" in distrito["geom_geojson"]
