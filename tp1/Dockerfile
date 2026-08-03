FROM python:3.11-slim

# procps es opcional: sirve para comparar la salida del monitor contra
# ps/top dentro del mismo contenedor mientras se desarrolla.
RUN apt-get update \
    && apt-get install -y --no-install-recommends procps ncurses-base \
    && rm -rf /var/lib/apt/lists/*

# Necesario para que curses sepa dibujar; se puede pisar desde compose.
ENV TERM=xterm-256color
# Sin buffering: en modo --daemon los logs salen al instante.
ENV PYTHONUNBUFFERED=1
# Evita que se generen __pycache__ dentro de la imagen.
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt /app/
COPY config.json /app/
COPY src/ /app/

RUN mkdir -p /app/dumps

CMD ["python3", "main.py"]
