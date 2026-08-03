
import queue
import signal
import time


def agregador(cola_resultados, snapshot, evento_salida):
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    while not evento_salida.is_set():
        try:
            nombre, datos, ts = cola_resultados.get(timeout=0.5)
        except queue.Empty:
            continue

        # Cada clave guarda los datos + el timestamp de cuándo se tomaron,
        # así la TUI puede mostrar qué tan "vieja" está cada vista.
        #
        # El try es por el apagado: cuando la señal va a TODO el grupo de
        # procesos (Ctrl+C en la terminal, o `kill` al grupo), el proceso
        # servidor del Manager puede morir antes que nosotros. Ahí el
        # snapshot deja de existir y escribirlo tira BrokenPipeError. No es
        # un error: es que ya nos estamos apagando.
        try:
            snapshot[nombre] = {"datos": datos, "ts": ts}
        except (BrokenPipeError, EOFError, ConnectionError, OSError):
            break

        # Los tops se derivan del resumen, que es el único que tiene
        # CPU% y RSS de todos los procesos juntos.
        if nombre == "resumen" and datos:
            por_cpu = sorted(datos, key=lambda p: p.get("cpu_pct", 0), reverse=True)[:3]
            por_mem = sorted(datos, key=lambda p: p.get("rss", 0), reverse=True)[:3]
            try:
                snapshot["tops"] = {
                    "cpu": [(p["pid"], p["comando"], p.get("cpu_pct", 0)) for p in por_cpu],
                    "mem": [(p["pid"], p["comando"], p.get("rss", 0)) for p in por_mem],
                    "ts": time.time(),
                }
            except (BrokenPipeError, EOFError, ConnectionError, OSError):
                break
