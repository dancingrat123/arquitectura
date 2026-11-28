# API de SIRIF — especificaciones OpenAPI

Especificaciones estáticas exportadas desde las aplicaciones FastAPI del proyecto. Permiten revisar el contrato completo (rutas, parámetros, esquemas y códigos de respuesta) sin levantar el stack.

| Servicio | Especificación | Rutas | Documentación interactiva (stack activo) |
|---|---|---|---|
| `api-service` | [`openapi-api-service.json`](openapi-api-service.json) | 36 | `http://localhost:8000/docs` (red interna) |
| `data-service` | [`openapi-data-service.json`](openapi-data-service.json) | 9 | `http://localhost:8000/docs` |
| `risk-service` | [`openapi-risk-service.json`](openapi-risk-service.json) | 3 | `http://localhost:8001/docs` |

El gateway (Nginx) publica solo rutas `/api/`; por eso `/docs` no está expuesto en `https://localhost:9443`. Para consultar Swagger en vivo:

```bash
# comprobar que el contrato interno responde
docker compose exec -T api-service \
  python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/openapi.json').status)"
```

Para abrir Swagger UI de forma interactiva, agrega temporalmente `ports: ["8000:8000"]` al servicio en `docker-compose.yml` y visita `http://localhost:8000/docs`. En el despliegue normal se mantiene sin puerto público.

Las rutas de negocio, roles y ejemplos de uso están documentados en el [README principal](../../README.md#endpoints-principales-api-service).

## Regenerar

```bash
python docs/api/generar_openapi.py
```

El script importa cada `main.app` con variables de entorno ficticias y escribe los tres JSON en este directorio. No requiere base de datos, RabbitMQ ni red.
