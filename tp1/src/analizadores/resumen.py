"""Analizador de la vista Resumen: estado, CPU%, RSS, threads, comando."""

import queue
import signal
import time

from procfs import leer_resumen


def _procesar(pids, estado, verbose):
    # estado['cpu'] guarda {pid: (jiffies, timestamp)} de la vuelta anterior.
    # Sin esto no se puede calcular CPU%: /proc no da un porcentaje, da un
    # contador acumulado de jiffies desde que arrancó el proceso.
    cache_cpu = estado.setdefault("cpu", {})

    datos = []
    vivos = set()
    for pid in pids:
        info = leer_resumen(pid, cache_cpu)
        if info:
            datos.append(info)
            vivos.add(pid)

    # Limpieza del cache: si no lo hacemos, el dict crece para siempre a
    # medida que nacen y mueren procesos (fuga de memoria lenta).
    for pid in list(cache_cpu):
        if pid not in vivos:
            del cache_cpu[pid]

    return datos


def analizador_resumen(cola_pedidos, cola_trabajo, cola_resultados,
                       intervalo, verbose, evento_salida):
    # Ver comentario en recolector.py sobre por qué ignoramos SIGINT acá.
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    estado = {}

    while not evento_salida.is_set():
        inicio = time.monotonic()

        # --- 1. pedir trabajo al recolector ---
        try:
            cola_pedidos.put("resumen", timeout=1.0)
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
            cola_resultados.put(("resumen", datos, time.time()), timeout=1.0)
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
