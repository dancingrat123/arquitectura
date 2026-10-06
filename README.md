# SIRIF - Microservicios

Implementación simplificada de microservicios para el **Sistema Inteligente de Riesgo de Incendios Forestales (SIRIF)**, desarrollado para la asignatura **ITY1102 – Arquitectura de Sistemas de Inteligencia Artificial** de Duoc UC.

---

##  Descripción

Este proyecto implementa una arquitectura de microservicios con **dos servicios independientes** y un **API Gateway** como punto de entrada único. El objetivo es demostrar la separación de responsabilidades, la comunicación entre servicios y el uso de contenedores Docker.

---


##  Responsabilidad de cada microservicio

### Data Service (`:8000`)

**Responsabilidad:** Obtener y preparar las variables necesarias para evaluar una zona de la Región de Valparaíso.

**Endpoint principal:** `GET /datos/{zona}`

**Variables que entrega:**
- Temperatura (°C)
- Humedad (%)
- Velocidad del viento (km/h)
- Cobertura vegetal (alta/media/baja)
- Incendios históricos (cantidad)

**En producción, este servicio se conectaría a:**
- IDE Chile (capas territoriales)
- CONAF (históricos de incendios)
- Servicio Meteorológico (temperatura, humedad, viento)
- Dataset privado de Adevcom (solo entrenamiento)

---

### Risk Service (`:8001`)

**Responsabilidad:** Recibir las variables de una zona y entregar una clasificación de riesgo (Bajo, Medio, Alto).

**Endpoint principal:** `POST /clasificar`

**Lógica actual:** reglas heurísticas simples (para la demo).
**En producción:** el modelo Random Forest entrenado con scikit-learn.

**Factores considerados:**
- Temperatura
- Humedad
- Velocidad del viento
- Cobertura vegetal
- Incendios históricos

---

### API Gateway (Nginx `:8080`)

**Responsabilidad:** Punto de entrada único al sistema. Recibe todas las solicitudes del usuario y las redirige al microservicio correcto según la ruta.

**Rutas:**
- `GET /` → health del Gateway
- `GET /api/datos/{zona}` → redirige al Data Service
- `POST /api/clasificar` → redirige al Risk Service

**Ventajas:**
- Oculta la complejidad interna del sistema al usuario.
- Centraliza el enrutamiento y la seguridad (SSL/TLS en producción).
- Facilita cambiar servicios internos sin afectar a los clientes.

---

##  Cómo levantar el proyecto

### Requisitos previos

- Docker Desktop instalado y corriendo.

### Pasos

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-usuario/sirif-microservicios.git
cd sirif-microservicios

# 2. Levantar los servicios
docker compose up --build -d

# 3. Verificar que están activos
docker compose ps
