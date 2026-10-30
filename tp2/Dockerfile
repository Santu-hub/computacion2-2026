FROM python:3.12-slim

# Sin buffering: los logs salen al instante en `docker compose logs`.
ENV PYTHONUNBUFFERED=1
# Evita que se generen __pycache__ dentro de la imagen.
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY bench/ bench/

# Acá se vuelca estado_<timestamp>.json; compose lo monta en ./datos.
ENV DIR_ESTADO=/datos
RUN mkdir -p /datos

EXPOSE 8000

# Forma exec (sin shell): python es PID 1 y recibe SIGTERM directamente,
# que es lo que dispara el apagado ordenado.
CMD ["python", "-m", "app.main"]
