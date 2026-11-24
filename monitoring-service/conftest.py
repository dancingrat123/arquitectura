import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# El servicio exige configuración explícita (sin defaults) al importar.
os.environ.setdefault("DATABASE_URL", "postgresql://sirif:sirif2026@db:5432/sirif")
os.environ.setdefault("RABBITMQ_HOST", "rabbitmq")
os.environ.setdefault("RABBITMQ_USER", "sirif")
os.environ.setdefault("RABBITMQ_PASS", "sirif2026")
