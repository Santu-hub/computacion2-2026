import queue
import signal
import time

from procfs import obtener_pids


def recolector(cola_pedidos, colas_trabajo, evento_salida, ttl=0.5):
    # El padre coordina el apagado con evento_salida; si dejamos el handler
    # heredado, un Ctrl+C (que va a TODO el grupo de procesos) ejecutaría el
    # handler del padre acá adentro sin sentido.
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    pids_cache = []
    ts_cache = 0.0

    while not evento_salida.is_set():
        try:
            # timeout corto para poder chequear evento_salida seguido
            nombre = cola_pedidos.get(timeout=0.5)
        except queue.Empty:
            continue

        ahora = time.monotonic()
        if ahora - ts_cache > ttl or not pids_cache:
            pids_cache = obtener_pids()
            ts_cache = ahora

        cola = colas_trabajo.get(nombre)
        if cola is not None:
            try:
                cola.put(list(pids_cache), timeout=1.0)
            except queue.Full:
                # El analizador se colgó o murió: descartamos el lote en vez
                # de bloquear al recolector entero.
                pass
