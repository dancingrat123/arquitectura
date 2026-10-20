import concurrent.futures
import requests
from requests.auth import HTTPBasicAuth

url = "http://localhost:9080/api/riesgo/todas"
auth = HTTPBasicAuth("admin", "sirif2026")

def hacer_peticion(_):
    try:
        requests.get(url, auth=auth, timeout=2)
    except:
        pass

# Enviar 200 peticiones concurrentes (50 hilos a la vez)
with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
    executor.map(hacer_peticion, range(200))