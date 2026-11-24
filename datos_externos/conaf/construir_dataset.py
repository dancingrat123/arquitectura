# -*- coding: utf-8 -*-
"""Construye un dataset consolidado de incendios CONAF (2010-2025).

Descarga los SHP de puntos georreferenciados desde IDE Minagri, extrae los
atributos, transforma coordenadas UTM a WGS84 y genera:
  - incendios_conaf_2010_2025.csv          (eventos, todo Chile)
  - incendios_conaf_valparaiso_2010_2025.csv (eventos, Valparaíso)
  - incendios_comunal_diario_valparaiso_2010_2025.csv (agregado diario)
  - incendios_comunal_diario_valparaiso_2010_2025_zeros.csv (grilla completa)
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
import urllib.request
from pathlib import Path

import pandas as pd
import shapefile
from pyproj import Transformer

BASE = Path(__file__).resolve().parent
EXTRACCION = BASE / "extraido"
BASE_URL = (
    "https://ide.minagri.gob.cl/wp-content/uploads/DESCARGAS/CAPAS/DESASTRES/"
    "INCENDIOS_FORESTALES_CONAF/"
)

TEMPORADAS = [
    "2024_2025",
    "2023_2024",
    "2022_2023",
    "2021_2022",
    "2020_2021",
    "2019_2020",
    "2018_2019",
    "2017_2018",
    "2016_2017",
    "2015_2016",
    "2014_2015",
    "2013_2014",
    "2012_2013",
    "2011_2012",
    "2010_2011",
]

MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}

CAMPOS = {
    "region": ["REGION"],
    "provincia": ["PROVINCIA"],
    "comuna": ["COMUNA"],
    "nombre": ["NOMBRE", "NOMBRE_INC", "NOM_INCEN"],
    "utm_e": ["UTM_E", "UTM_ESTE", "ESTE"],
    "utm_n": ["UTM_N", "UTM_NORTE", "NORTE"],
    "huso": ["HUSO_OP_", "HUSO", "HUSO_OP"],
    "fecha_inicio": ["FH_INICIO", "FECHA_INICIO", "FECHA", "INICIO", "INICIO_IN"],
    "fecha_fin": ["FH_EXTINCI", "FECHA_EXTINCION", "FIN", "EXTINCION"],
    "superficie": ["SUPERFICIE", "SUP_AFECTADA", "SUPERFICIE_TOTAL"],
    "causa": ["CAUSA_GENE", "CAUSA", "CAUSA_GENERAL"],
    "causa_especifica": ["CAUSA_ESPE", "CAUSA_ESPECIFICA"],
    "combustible": ["COMBUSTIBL", "COMBUSTIBLE", "COMBUS_I"],
}

REGIONES_COD = {
    "15": "Arica y Parinacota",
    "01": "Tarapacá",
    "02": "Antofagasta",
    "03": "Atacama",
    "04": "Coquimbo",
    "05": "Valparaíso",
    "06": "O'Higgins",
    "07": "Maule",
    "08": "Biobío",
    "09": "La Araucanía",
    "10": "Los Lagos",
    "11": "Aysén",
    "12": "Magallanes",
    "13": "Metropolitana",
    "14": "Los Ríos",
    "16": "Ñuble",
}


def normalizar_texto(valor: object) -> str:
    if valor is None:
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.lower().split())


def _normalizar_columnas(columnas: list[str]) -> dict[str, str]:
    return {c: c.strip().upper().replace(" ", "_") for c in columnas}


def _buscar(columna: str, campos: dict[str, str], candidatos: list[str]) -> str | None:
    for nombre in candidatos:
        if nombre in campos:
            return campos[nombre]
    return None


def _parse_fecha(valor: object) -> pd.Timestamp | pd.NaT:
    if valor is None:
        return pd.NaT
    texto = str(valor).strip().lower()
    if not texto or texto in ("nan", "none", "nat"):
        return pd.NaT
    m = re.match(
        r"(\d{1,2})[-/]([a-záéíóú]{3,})[-/](\d{2,4})(?:\s+(\d{1,2}):(\d{2}))?",
        texto,
    )
    if m:
        dia, mes, anio, hora, minuto = m.groups()
        mes_num = MESES.get(mes[:3])
        if mes_num:
            anio = int(anio)
            if anio < 100:
                anio += 2000
            return pd.Timestamp(
                anio, mes_num, int(dia),
                int(hora) if hora else 0, int(minuto) if minuto else 0,
            )
    # Formatos numéricos tipo 05/12/2024 o 2024-12-05
    for dayfirst in (True, False):
        try:
            fecha = pd.to_datetime(texto, dayfirst=dayfirst, errors="raise")
            if pd.notna(fecha):
                return fecha
        except Exception:
            pass
    return pd.NaT


def _huso_a_zona(valor: object) -> int:
    if valor is None:
        return 19
    m = re.search(r"(\d{1,2})", str(valor))
    if not m:
        return 19
    zona = int(m.group(1))
    return zona if 17 <= zona <= 19 else 19


def descargar_y_extraer(temporada: str) -> Path:
    destino = EXTRACCION / temporada
    if list(destino.glob("*.shp")):
        return destino
    destino.mkdir(parents=True, exist_ok=True)
    rar = BASE / f"if_temporada_{temporada}.rar"
    if not rar.exists():
        url = f"{BASE_URL}if_temporada_{temporada}.rar"
        print(f"Descargando {temporada} ...")
        with urllib.request.urlopen(url, timeout=120) as respuesta, open(rar, "wb") as archivo:
            shutil.copyfileobj(respuesta, archivo)
    print(f"Extrayendo {temporada} ...")
    subprocess.run(["tar", "-xf", str(rar), "-C", str(destino)], check=True)
    return destino


def leer_temporada(temporada: str) -> pd.DataFrame:
    carpeta = descargar_y_extraer(temporada)
    shps = list(carpeta.rglob("*.shp"))
    if not shps:
        raise RuntimeError(f"No se encontró SHP en {carpeta}")
    lector = shapefile.Reader(str(shps[0]))
    columnas = [campo[0] for campo in lector.fields[1:]]
    df = pd.DataFrame(lector.records(), columns=columnas)
    df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]

    campos = {c: c for c in df.columns}
    salida = pd.DataFrame(index=df.index)
    for clave, candidatos in CAMPOS.items():
        columna = _buscar(clave, campos, candidatos)
        salida[clave] = df[columna] if columna else None

    salida["temporada"] = temporada.replace("_", "-")
    salida["codreg"] = df["CODREG"] if "CODREG" in df.columns else None
    salida["fecha"] = salida["fecha_inicio"].map(_parse_fecha)
    salida["fecha_fin"] = salida["fecha_fin"].map(_parse_fecha)
    salida["superficie"] = pd.to_numeric(salida["superficie"], errors="coerce")
    salida["utm_e"] = pd.to_numeric(salida["utm_e"], errors="coerce")
    salida["utm_n"] = pd.to_numeric(salida["utm_n"], errors="coerce")

    # Transformar UTM -> WGS84 por zona.
    salida["lat"] = None
    salida["lon"] = None
    for zona, grupo in salida.groupby(salida["huso"].map(_huso_a_zona)):
        transformer = Transformer.from_crs(f"EPSG:327{zona}", "EPSG:4326", always_xy=True)
        lon, lat = transformer.transform(grupo["utm_e"].values, grupo["utm_n"].values)
        salida.loc[grupo.index, "lon"] = lon
        salida.loc[grupo.index, "lat"] = lat

    return salida


def main() -> None:
    EXTRACCION.mkdir(parents=True, exist_ok=True)
    partes: list[pd.DataFrame] = []
    for temporada in TEMPORADAS:
        try:
            df = leer_temporada(temporada)
            print(f"{temporada}: {len(df)} registros")
            partes.append(df)
        except Exception as error:  # noqa: BLE001
            print(f"{temporada}: ERROR {error}")

    if not partes:
        raise SystemExit("No se pudo descargar ninguna temporada")

    datos = pd.concat(partes, ignore_index=True)
    datos["region"] = datos["region"].astype(str).str.strip()
    datos["comuna"] = datos["comuna"].astype(str).str.strip()
    datos["provincia"] = datos["provincia"].astype(str).str.strip()

    # Completar regiones vacías en temporadas antiguas usando CODREG.
    codigos = datos["codreg"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    codigos = codigos.str.zfill(2)
    region_desde_codigo = codigos.map(REGIONES_COD)
    vacia = datos["region"].isin(["", "None", "nan"])
    datos.loc[vacia, "region"] = region_desde_codigo[vacia]

    datos["fuente"] = "CONAF"
    datos["region_norm"] = datos["region"].map(normalizar_texto)
    datos["comuna_norm"] = datos["comuna"].map(normalizar_texto)

    columnas = [
        "fecha", "temporada", "region", "region_norm", "provincia",
        "comuna", "comuna_norm", "nombre", "lat", "lon", "utm_e", "utm_n",
        "huso", "superficie", "causa", "causa_especifica", "combustible",
        "fecha_fin", "fuente",
    ]
    datos = datos[columnas].drop_duplicates(
        subset=["fecha", "region_norm", "comuna_norm", "nombre", "superficie"]
    )
    datos = datos.sort_values(["fecha", "region_norm", "comuna_norm"]).reset_index(drop=True)

    ruta_eventos = BASE / "incendios_conaf_2010_2025.csv"
    datos.to_csv(ruta_eventos, index=False, encoding="utf-8")
    print(f"Eventos totales: {len(datos)} -> {ruta_eventos}")

    # Eventos con fecha válida (base para el modelo diario).
    diarios = datos.dropna(subset=["fecha"]).copy()
    diarios["fecha"] = diarios["fecha"].dt.normalize()
    ruta_diarios = BASE / "incendios_conaf_diario_2017_2025.csv"
    diarios.to_csv(ruta_diarios, index=False, encoding="utf-8")
    print(f"Eventos con fecha: {len(diarios)} -> {ruta_diarios}")

    valparaiso = datos[datos["region_norm"].str.contains("valparaiso", na=False)].copy()
    ruta_valparaiso = BASE / "incendios_conaf_valparaiso_2010_2025.csv"
    valparaiso.to_csv(ruta_valparaiso, index=False, encoding="utf-8")
    print(f"Valparaíso (todos): {len(valparaiso)} -> {ruta_valparaiso}")

    valparaiso_diario = diarios[diarios["region_norm"].str.contains("valparaiso", na=False)].copy()
    ruta_valparaiso_diario = BASE / "incendios_conaf_valparaiso_diario_2017_2025.csv"
    valparaiso_diario.to_csv(ruta_valparaiso_diario, index=False, encoding="utf-8")
    print(f"Valparaíso con fecha: {len(valparaiso_diario)} -> {ruta_valparaiso_diario}")

    # Agregado diario por comuna (solo días con incendios).
    diario = (
        valparaiso_diario
        .groupby(["fecha", "region_norm", "comuna_norm"], as_index=False)
        .agg(
            comuna=("comuna", "first"),
            n_incendios=("nombre", "size"),
            superficie_total=("superficie", "sum"),
            causa_principal=("causa", lambda s: s.mode().iloc[0] if not s.mode().empty else ""),
            lat=("lat", "mean"),
            lon=("lon", "mean"),
        )
    )
    ruta_diario = BASE / "incendios_comunal_diario_valparaiso_2017_2025.csv"
    diario.to_csv(ruta_diario, index=False, encoding="utf-8")
    print(f"Agregado diario Valparaíso: {len(diario)} -> {ruta_diario}")

    # Grilla completa comuna-día con ceros para entrenamiento.
    if not diario.empty:
        fechas = pd.date_range(diario["fecha"].min(), diario["fecha"].max(), freq="D")
        comunas = (
            diario.groupby(["region_norm", "comuna_norm"], as_index=False)
            .agg(comuna=("comuna", "first"), lat=("lat", "mean"), lon=("lon", "mean"))
        )
        grilla = comunas.merge(pd.DataFrame({"fecha": fechas}), how="cross")
        completo = grilla.merge(
            diario[["fecha", "region_norm", "comuna_norm", "n_incendios",
                    "superficie_total", "causa_principal"]],
            on=["fecha", "region_norm", "comuna_norm"],
            how="left",
        )
        completo["n_incendios"] = completo["n_incendios"].fillna(0).astype(int)
        completo["superficie_total"] = completo["superficie_total"].fillna(0.0)
        completo["causa_principal"] = completo["causa_principal"].fillna("")
        completo["y"] = (completo["n_incendios"] > 0).astype(int)
        ruta_completo = BASE / "incendios_comunal_diario_valparaiso_2017_2025_zeros.csv"
        completo.to_csv(ruta_completo, index=False, encoding="utf-8")
        print(f"Grilla completa: {len(completo)} -> {ruta_completo}")


if __name__ == "__main__":
    main()
