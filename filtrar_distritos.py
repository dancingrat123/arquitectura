import json
import unicodedata

def normalizar(s):
    if not s:
        return ''
    s = s.lower()
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return s

with open('distritos_censales.geojson', 'r', encoding='utf-8') as f:
    data = json.load(f)

comunas_interes = {
    normalizar('Quilpué'): 'ALTO',
    normalizar('Viña del Mar'): 'BAJO',
    normalizar('Valparaíso'): 'BAJO'
}

def get_nombre_comuna(props):
    for key in ['NOM_COMUNA', 'NOMBRE_COMUNA', 'nombre_comuna', 'Comuna', 'COMUNA']:
        if key in props and props[key]:
            val = str(props[key])
            if val.isdigit():
                continue
            return val
    return None

filtrados = []
riesgo_por_distrito = {}

for f in data['features']:
    nombre = get_nombre_comuna(f['properties'])
    if nombre and normalizar(nombre) in comunas_interes:
        filtrados.append(f)
        codigo = str(f['properties'].get('CODIGO_C17') or f['properties'].get('COD_DISTRI'))
        riesgo_por_distrito[codigo] = comunas_interes[normalizar(nombre)]

data['features'] = filtrados

# 1. GeoJSON solo con geometría (sin campo riesgo)
with open('frontend/data/distritos_filtrados.geojson', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False)

# 2. Mapping código -> riesgo (lo consume el Data Service)
with open('data-service/distritos_riesgo.json', 'w', encoding='utf-8') as f:
    json.dump(riesgo_por_distrito, f, ensure_ascii=False, indent=2)

print(f"Distritos filtrados: {len(filtrados)}")
print(f"Mapping de riesgo generado: {len(riesgo_por_distrito)} distritos")