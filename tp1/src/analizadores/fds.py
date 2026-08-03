"""Analizador de la vista File Descriptors."""

import queue
import signal
import time

from procfs import leer_fds

# En modo normal recortamos la lista de FDs por proceso para no llenar la
# Queue de datos que la TUI ni va a mostrar. Con SIGUSR2 (verbose) subimos
# el tope: ese es el "más FDs visibles" que pide la consigna.
TOPE_NORMAL = 12
TOPE_VERBOSE = 128


def _procesar(pids, estado, verbose):
    tope = TOPE_VERBOSE if verbose else TOPE_NORMAL
    datos = []
    for pid in pids:
        info = leer_fds(pid)
        if not info:
            continue
        total = len(info["fds"])
        info["fds"] = sorted(info["fds"], key=lambda f: int(f["fd"]))[:tope]
        info["total_fds"] = total
        datos.append(info)
    return datos


def analizador_fds(cola_pedidos, cola_trabajo, cola_resultados,
                   intervalo, verbose, evento_salida):
    # Ver comentario en recolector.py sobre por qué ignoramos SIGINT acá.
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    estado = {}

    while not evento_salida.is_set():
        inicio = time.monotonic()

        # --- 1. pedir trabajo al recolector ---
        try:
            cola_pedidos.put("fds", timeout=1.0)
        except queue.Full:
            continue
        except (BrokenPipeError, EOFError, OSError, ValueError):
            # Las colas se cerraron: el padre está apagando todo.
            break

        # --- 2. recibir la lista de PIDs ---
        try:
            pids = cola_trabajo.get(timeout=3.0)
        except queue.Empty:
            # El recolector no contestó (murió o está saturado). No nos
            # colgamos: reintentamos en la próxima vuelta.
            continue
        except (BrokenPipeError, EOFError, OSError, ValueError):
            break

        # --- 3. leer /proc y publicar al agregador ---
        try:
            datos = _procesar(pids, estado, bool(verbose.value))
        except Exception:
            # Un error leyendo un /proc raro no debe matar al analizador
            # entero: el monitor tiene que seguir vivo.
            datos = []

        try:
            cola_resultados.put(("fds", datos, time.time()), timeout=1.0)
        except queue.Full:
            # El agregador se atrasó. Descartamos esta muestra en vez de
            # acumular: es un monitor, los datos viejos no sirven.
            pass
        except (BrokenPipeError, EOFError, OSError, ValueError):
            break

        # --- 4. dormir el resto del intervalo ---
        # Se relee intervalo.value en cada vuelta del while: por eso el
        # cambio con +/- desde la TUI tiene efecto casi inmediato sin
        # necesidad de reiniciar el analizador.
        while not evento_salida.is_set():
            transcurrido = time.monotonic() - inicio
            objetivo = intervalo.value
            if transcurrido >= objetivo:
                break
            time.sleep(min(0.1, objetivo - transcurrido))
