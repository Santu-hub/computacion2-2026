FROM python:3.11-slim
# Instalamos procps solo por si querés comparar con comandos reales adentro
RUN apt-get update && apt-get install -y procps && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY src/ /app/
# El comando por defecto
CMD ["python3", "main.py"]