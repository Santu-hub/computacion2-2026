"""Analizador de la vista Sistema: stats globales de la máquina."""

import queue
import signal
import time

from procfs import leer_sistema


def _procesar(pids, estado, verbose):
    datos = leer_sistema(pids)

    # CPU global: /proc/stat da contadores acumulados de jiffies por modo.
    # El porcentaje sale de comparar contra la lectura anterior, igual que
    # el CPU% por proceso.
    tiempos = datos.get("cpu_tiempos", [])
    previos = estado.get("cpu_tiempos")

    porcentajes = {"user": 0.0, "system": 0.0, "idle": 0.0, "iowait": 0.0, "uso": 0.0}
    if previos and len(tiempos) >= 5 and len(previos) >= 5:
        deltas = [a - b for a, b in zip(tiempos, previos)]
        total = sum(deltas)
        if total > 0:
            porcentajes["user"] = 100.0 * (deltas[0] + deltas[1]) / total  # user + nice
            porcentajes["system"] = 100.0 * deltas[2] / total
            porcentajes["idle"] = 100.0 * deltas[3] / total
            porcentajes["iowait"] = 100.0 * deltas[4] / total
            porcentajes["uso"] = 100.0 - porcentajes["idle"] - porcentajes["iowait"]

    estado["cpu_tiempos"] = tiempos
    datos["cpu"] = porcentajes
    # Los contadores crudos no le sirven a la TUI y ocupan lugar en la Queue.
    datos.pop("cpu_tiempos", None)
    return datos


def analizador_sistema(cola_pedidos, cola_trabajo, cola_resultados,
                       intervalo, verbose, evento_salida):
    # Ver comentario en recolector.py sobre por qué ignoramos SIGINT acá.
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    estado = {}

    while not evento_salida.is_set():
        inicio = time.monotonic()

        # --- 1. pedir trabajo al recolector ---
        try:
            cola_pedidos.put("sistema", timeout=1.0)
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
            cola_resultados.put(("sistema", datos, time.time()), timeout=1.0)
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
