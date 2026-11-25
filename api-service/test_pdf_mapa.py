"""Regresiones del mapa local de la ficha PDF.

El GeoJSON de distritos incluye features con coordenadas muy alejadas de la
región; si entran al encuadre, el mapa queda reducido a unos pocos píxeles.
"""

from __future__ import annotations

import main


def _features() -> list[dict]:
    return list(main._cargar_geojson().get("features") or [])


def test_punto_wgs84_acepta_grados_y_mercator():
    assert main._punto_wgs84([-71.6, -33.0]) == (-71.6, -33.0)

    lon, lat = main._punto_wgs84([-7970000.0, -3910000.0])
    assert -73.5 < lon < -69.5
    assert -35.0 < lat < -31.5

    assert main._punto_wgs84(None) is None


def test_mapa_local_excluye_distritos_fuera_de_region():
    features = _features()
    assert features, "falta frontend/data/distritos_filtrados.geojson"

    fuera = [
        feature
        for feature in features
        if (feature.get("properties") or {}).get("CODIGO_C17") == "510124"
    ]
    assert fuera, "el fixture debe conservar el distrito outlier 510124"
    assert main._anillos_regionales(fuera[0]) == []

    for feature in features:
        if feature is fuera[0]:
            continue
        assert main._anillos_regionales(feature), (
            (feature.get("properties") or {}).get("CODIGO_C17")
        )


def test_mapa_local_ocupa_el_recuadro():
    """El bounding box dibujado debe cubrir buena parte del área disponible."""
    anillos = [anillo for feature in _features() for anillo in main._anillos_regionales(feature)]
    puntos = [punto for anillo in anillos for punto in anillo]
    assert puntos

    min_x = min(p[0] for p in puntos)
    max_x = max(p[0] for p in puntos)
    min_y = min(p[1] for p in puntos)
    max_y = max(p[1] for p in puntos)

    ancho, alto = 511.0, 205.0
    escala = min((ancho - 16) / (max_x - min_x), (alto - 16) / (max_y - min_y))
    assert escala * (max_x - min_x) > ancho * 0.9
