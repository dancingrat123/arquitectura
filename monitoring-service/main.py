import httpx
import time
from datetime import datetime

API_SERVICE_URL = "http://api-service:8000"
ZONAS = ["quilpue", "vina_del_mar", "valparaiso"]
INTERVALO = 30

def monitorear():
    print(f"[{datetime.now().isoformat()}] Monitoring Service iniciado", flush=True)
    print(f"Zonas: {ZONAS}", flush=True)
    print(f"Intervalo: {INTERVALO} segundos\n", flush=True)
    
    while True:
        print(f"[{datetime.now().isoformat()}] Ciclo de monitoreo...", flush=True)
        for zona in ZONAS:
            try:
                response = httpx.get(
                    f"{API_SERVICE_URL}/api/riesgo/{zona}",
                    timeout=10.0
                )
                data = response.json()
                print(f"  ✓ {zona}: {data.get('nivel_riesgo')} (fuente: {data.get('fuente')})", flush=True)
            except Exception as e:
                print(f"  ✗ {zona}: error - {e}", flush=True)
        print(f"Esperando {INTERVALO} segundos...\n", flush=True)
        time.sleep(INTERVALO)

if __name__ == "__main__":
    monitorear()