import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
import joblib
import seaborn as sns
import matplotlib.pyplot as plt

print("1. Cargando y limpiando datos...")
df_conaf = pd.read_csv('conaf.csv')
df_conaf = df_conaf.rename(columns={'region': 'region_nombre'})
df_emas = pd.read_csv('emas.csv')
cols_clima = ['ema_ia_id', 'rtd_c_variableid', 'rtd_f_valor']
df_clima = pd.read_csv('clima.csv', usecols=cols_clima)

# 2. Procesamiento Climático
df_clima_pivot = df_clima.pivot_table(index='ema_ia_id', columns='rtd_c_variableid', values='rtd_f_valor', aggfunc='mean').reset_index()
df_emas_clean = df_emas[['ema_ia_id', 'region_nombre']]
df_merged_clima = pd.merge(df_clima_pivot, df_emas_clean, on='ema_ia_id', how='inner')
df_clima_region = df_merged_clima.groupby('region_nombre').mean(numeric_only=True).reset_index()

# 3. Merge final
df = pd.merge(df_conaf, df_clima_region, on='region_nombre', how='left')

# 4. Feature Engineering
print("2. Creando nuevas variables (Feature Engineering)...")
df['fecha'] = pd.to_datetime(df['fecha'])
df['mes'] = df['fecha'].dt.month  # Variable de temporalidad

# Severidad histórica (evitando división por cero)
df['severidad_ant'] = df['sup_afec_ant'] / (df['nro_inc_ant'] + 1)

# Tendencia vs el promedio histórico
df['delta_incendios'] = df['nro_inc_ant'] - df['nro_inc_prom_quint']

# 5. Crear el Target (Riesgo)
def calcular_riesgo(row):
    # Usamos incendios ACTIVOS solo para definir el target, NO como feature.
    if row['nro_inc_act'] > 5 or row['sup_afec_act'] > 100: return 2 # ALTO
    elif row['nro_inc_act'] > 0 or row['sup_afec_act'] > 0: return 1 # MEDIO
    else: return 0 # BAJO

df['riesgo_target'] = df.apply(calcular_riesgo, axis=1)

# 6. Matriz de Correlación (Análisis de variables)
print("3. Generando Matriz de Correlación...")
# Seleccionamos variables numéricas predictivas (excluyendo el presente y el id)
features_num = ['mes', 'severidad_ant', 'delta_incendios', 'nro_inc_ant', 'sup_afec_ant', 
                'TEMP_MED', 'HUMEDAD_REL', 'VEL_VIENTO', 'PRECIPITACION', 'riesgo_target']

# Rellenar nulos
df_corr = df[features_num].fillna(0)

# Crear Heatmap
plt.figure(figsize=(10, 8))
sns.heatmap(df_corr.corr(), annot=True, cmap='coolwarm', fmt=".2f")
plt.title("Matriz de Correlación - Variables Predictivas vs Riesgo")
plt.tight_layout()
plt.savefig('matriz_correlacion.png')
print("   -> Gráfico guardado como 'matriz_correlacion.png'")

# 7. Entrenamiento
print("\n4. Entrenando Random Forest...")
# Variables predictoras finales (sin fuga de datos)
features = ['mes', 'severidad_ant', 'delta_incendios', 'nro_inc_ant', 'sup_afec_ant', 
            'TEMP_MED', 'HUMEDAD_REL', 'VEL_VIENTO', 'PRECIPITACION']

X = df[features].fillna(0)
y = df['riesgo_target']

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

clf = RandomForestClassifier(n_estimators=100, random_state=42, class_weight='balanced')
clf.fit(X_train, y_train)

y_pred = clf.predict(X_test)
print(f"Precisión (Accuracy): {accuracy_score(y_test, y_pred):.2f}")
print("\nReporte de Clasificación:\n", classification_report(y_test, y_pred, target_names=['BAJO', 'MEDIO', 'ALTO']))

# Importancia de las variables
print("\nImportancia de las variables según el modelo:")
for feature, importance in zip(features, clf.feature_importances_):
    print(f"- {feature}: {importance:.4f}")

# Guardar modelo
joblib.dump(clf, 'modelo_riesgo.pkl')
print("\n¡Modelo guardado como 'modelo_riesgo.pkl'!")