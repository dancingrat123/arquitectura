import pandas as pd
import numpy as np

# Configuración para ver todas las columnas en consola
pd.set_option('display.max_columns', None)

print("="*50)
print(" INICIANDO ANÁLISIS EXPLORATORIO DE DATOS (EDA)")
print("="*50)

# 1. Cargar datos (usamos nrows para no colapsar la memoria si el clima es gigante)
print("\n[1] Cargando datasets...")
df_conaf = pd.read_csv('conaf.csv')
df_emas = pd.read_csv('emas.csv')
# El dataset de clima puede ser enorme. Lee solo las columnas que necesitamos.
cols_clima = ['ema_ia_id', 'rtd_c_variableid', 'rtd_dt_marcatiempo', 'rtd_f_valor']
df_clima = pd.read_csv('clima.csv', usecols=cols_clima)

# 2. Análisis de CONAF (Incendios)
print("\n" + "-"*50)
print(" [2] ANÁLISIS DE CONAF (Variable Objetivo)")
print("-"*50)
print(f"Total de registros: {len(df_conaf)}")
print(f"Rango de fechas: {df_conaf['fecha'].min()} a {df_conaf['fecha'].max()}")
print("\nTop 5 Regiones con más registros de incendios:")
print(df_conaf['region'].value_counts().head())
print("\nEstadísticas de incendios activos (nro_inc_act):")
print(df_conaf['nro_inc_act'].describe())

# 3. Análisis de EMAS (Estaciones)
print("\n" + "-"*50)
print(" [3] ANÁLISIS DE EMAS (Estaciones Meteorológicas)")
print("-"*50)
print(f"Total de estaciones: {len(df_emas)}")
print(f"Estaciones activas: {df_emas['ema_l_estacionactiva'].sum()} de {len(df_emas)}")
print("\nDistribución de estaciones por Región:")
print(df_emas['region_nombre'].value_counts())

# 4. Análisis de Clima (Telemetría)
print("\n" + "-"*50)
print(" [4] ANÁLISIS DE CLIMA (Variables Meteorológicas)")
print("-"*50)
print(f"Total de registros de telemetría: {len(df_clima)}")
print("\nTipos de variables medidas (rtd_c_variableid):")
print(df_clima['rtd_c_variableid'].value_counts().head(10))
print(f"Valores nulos en las mediciones (rtd_f_valor): {df_clima['rtd_f_valor'].isnull().sum()}")

# 5. PRUEBA DE CRUCE (El paso más crítico)
print("\n" + "-"*50)
print(" [5] PRUEBA DE CRUCE DE DATOS (Merge)")
print("-"*50)

# Limpiar fechas
df_clima['fecha_str'] = pd.to_datetime(df_clima['rtd_dt_marcatiempo']).dt.date.astype(str)
df_conaf['fecha_str'] = pd.to_datetime(df_conaf['fecha']).dt.date.astype(str)

# ¿Coinciden las regiones?
regiones_conaf = set(df_conaf['region'].unique())
regiones_emas = set(df_emas['region_nombre'].unique())
regiones_comunes = regiones_conaf.intersection(regiones_emas)

print(f"Regiones en CONAF: {len(regiones_conaf)}")
print(f"Regiones en EMAS: {len(regiones_emas)}")
print(f"Regiones que coinciden exactamente: {len(regiones_comunes)}")

if len(regiones_comunes) == 0:
    print("\nALERTA: ¡Los nombres de las regiones no coinciden!")
    print("Ejemplos en CONAF:", list(regiones_conaf)[:3])
    print("Ejemplos en EMAS:", list(regiones_emas)[:3])
else:
    print("¡Buenas noticias! Los nombres de las regiones coinciden.")
    
    # ¿Coinciden las fechas?
    fechas_conaf = set(df_conaf['fecha_str'].unique())
    fechas_clima = set(df_clima['fecha_str'].unique())
    fechas_comunes = fechas_conaf.intersection(fechas_clima)
    
    print(f"\nDías únicos en CONAF: {len(fechas_conaf)}")
    print(f"Días únicos en Clima: {len(fechas_clima)}")
    print(f"Días que coinciden exactamente: {len(fechas_comunes)}")
    
    if len(fechas_comunes) == 0:
        print("\nALERTA: Las fechas no coinciden. Es probable que el clima sea de 2026 y los incendios históricos de años anteriores.")
    else:
        print("¡Buenas noticias! Hay días en común para entrenar el modelo.")