import json
import os
import select
import signal
import time

# Señales que manejamos, mapeadas al byte que viaja por el pipe.
# Mandamos el número de señal como un byte: es un solo write() atómico
# (los writes de <= PIPE_BUF bytes lo son), así que nunca se mezclan.
SENALES_MANEJADAS = (
    signal.SIGINT,
    signal.SIGTERM,
    signal.SIGHUP,
    signal.SIGUSR1,
    signal.SIGUSR2,
    signal.SIGWINCH,
)

CONFIG_DEFAULT = {
    "intervalos": {
        "resumen": 2.0,
        "memoria": 3.0,
        "fds": 5.0,
        "threads": 2.0,
        "senales": 10.0,
        "scheduling": 10.0,
        "sistema": 2.0,
    },
    "intervalos_minimos": {
        "resumen": 0.5,
        "memoria": 1.0,
        "fds": 2.0,
        "threads": 0.5,
        "senales": 5.0,
        "scheduling": 5.0,
        "sistema": 1.0,
    },
    "filtro_comando": "",
    "filtro_usuario": "",
    "orden": "cpu",
    "verbose": False,
}

_pipe_lectura = None
_pipe_escritura = None


def instalar_handlers():
    """
    Crea el self-pipe y registra los handlers. Devuelve el fd de lectura,
    que el loop principal tiene que vigilar.
    """
    global _pipe_lectura, _pipe_escritura
    _pipe_lectura, _pipe_escritura = os.pipe()

    # No bloqueante en ambas puntas: si el pipe se llena (señales a
    # repetición), el handler NO se puede quedar colgado en write().
    os.set_blocking(_pipe_lectura, False)
    os.set_blocking(_pipe_escritura, False)

    for sig in SENALES_MANEJADAS:
        signal.signal(sig, _handler)

    return _pipe_lectura


def _handler(signum, frame):
    """
    Handler async-signal-safe: escribe un byte y se va. Nada más.
    Ni logging, ni json, ni print (print no es async-signal-safe).
    """
    try:
        os.write(_pipe_escritura, bytes([signum]))
    except (BlockingIOError, OSError):
        # Pipe lleno: ya hay señales pendientes de procesar, se pierde
        # esta notificación pero el loop igual va a despertar. Aceptable:
        # las señales estándar no se encolan de todos modos.
        pass


def leer_senales_pendientes(fd, timeout=0):
    """
    Lee del self-pipe todas las señales pendientes. Devuelve la lista de
    números de señal. No bloquea (timeout=0 por defecto).
    """
    listos, _, _ = select.select([fd], [], [], timeout)
    if not listos:
        return []
    try:
        datos = os.read(fd, 64)
    except (BlockingIOError, OSError):
        return []
    return list(datos)


# ---------------------------------------------------------------------------
# Acciones que dispara cada señal (se ejecutan en el loop principal)
# ---------------------------------------------------------------------------

def cargar_config(ruta="config.json"):
    """Lee config.json mezclándolo con los defaults."""
    config = json.loads(json.dumps(CONFIG_DEFAULT))  # copia profunda barata
    try:
        with open(ruta, "r") as f:
            del_archivo = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return config, False

    for clave, valor in del_archivo.items():
        if isinstance(valor, dict) and isinstance(config.get(clave), dict):
            config[clave].update(valor)
        else:
            config[clave] = valor
    return config, True


def aplicar_config(config, intervalos, verbose):
    """
    Vuelca la config a la memoria compartida (los Value). Como los
    analizadores releen intervalo.value en cada vuelta, el cambio se
    propaga a los 7 procesos sin reiniciar nada.
    """
    minimos = config.get("intervalos_minimos", {})
    for nombre, valor in config.get("intervalos", {}).items():
        if nombre in intervalos:
            minimo = minimos.get(nombre, 0.5)
            intervalos[nombre].value = max(float(minimo), float(valor))
    verbose.value = 1 if config.get("verbose") else 0


def volcar_snapshot(snapshot, directorio="dumps"):
    """
    SIGUSR1: escribe el snapshot actual a dumps/dump_<timestamp>.json.
    Ojo: esto se llama desde el loop principal, NO desde el handler.
    """
    os.makedirs(directorio, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    ruta = os.path.join(directorio, f"dump_{ts}.json")

    # dict(snapshot) hace una copia local del Manager.dict; a partir de ahí
    # trabajamos sobre datos propios y no sobre el objeto compartido.
    copia = {}
    for clave in list(snapshot.keys()):
        try:
            copia[clave] = snapshot[clave]
        except KeyError:
            continue

    with open(ruta, "w") as f:
        json.dump(copia, f, indent=2, default=str)
    return ruta
