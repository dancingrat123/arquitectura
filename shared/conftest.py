import os
import sys

# Permite `import riesgo` desde shared/ y `from shared.riesgo import ...` desde
# las suites de cada servicio.
AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
for ruta in (AQUI, RAIZ):
    if ruta not in sys.path:
        sys.path.insert(0, ruta)
