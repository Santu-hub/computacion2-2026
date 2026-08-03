import argparse
import multiprocessing as mp
import os
import signal
import sys
import time

import curses

import senales as mod_senales
from agregador import agregador
from display import tui_loop
from recolector import recolector

from analizadores.fds import analizador_fds
from analizadores.memoria import analizador_memoria
from analizadores.resumen import analizador_resumen
from analizadores.scheduling import analizador_scheduling
from analizadores.senales import analizador_senales
from analizadores.sistema import analizador_sistema
from analizadores.threads import analizador_threads

ANALIZADORES = {
    "resumen": analizador_resumen,
    "memoria": analizador_memoria,
    "fds": analizador_fds,
    "threads": analizador_threads,
    "senales": analizador_senales,
    "scheduling": analizador_scheduling,
    "sistema": analizador_sistema,
}


def lanzar_analizador(nombre, cola_pedidos, cola_trabajo, cola_resultados,
                      intervalo, verbose, evento_salida):
    p = mp.Process(
        target=ANALIZADORES[nombre],
        args=(cola_pedidos, cola_trabajo, cola_resultados,
              intervalo, verbose, evento_salida),
        name=f"analizador-{nombre}",
        daemon=True,  # si el padre muere de golpe, no quedan huérfanos
    )
    p.start()
    return p


def main():
    parser = argparse.ArgumentParser(description="Monitor de procesos y threads vía /proc")
    parser.add_argument("--config", default="config.json", help="ruta del archivo de config")
    parser.add_argument("--dumps", default="dumps", help="directorio para los dumps de SIGUSR1")
    args = parser.parse_args()

    # fork es el default en Linux y nos sirve: los hijos heredan el estado ya
    # armado (colas, Values) sin tener que re-importar y re-serializar todo.
    try:
        mp.set_start_method("fork")
    except RuntimeError:
        pass

    config, encontrada = mod_senales.cargar_config(args.config)

    manager = mp.Manager()
    snapshot = manager.dict()
    evento_salida = mp.Event()

    # --- IPC ---
    # Queue: pedidos de trabajo (N analizadores -> 1 recolector)
    cola_pedidos = mp.Queue(maxsize=64)
    # Queue: una por analizador, para la respuesta del recolector
    colas_trabajo = {n: mp.Queue(maxsize=2) for n in ANALIZADORES}
    # Queue: resultados (7 analizadores -> 1 agregador)
    cola_resultados = mp.Queue(maxsize=32)

    # Value: intervalo de cada vista, escrito por el display, leído por
    # cada analizador en cada vuelta de su bucle.
    intervalos = {n: mp.Value("d", float(config["intervalos"].get(n, 2.0)))
                  for n in ANALIZADORES}
    minimos = {n: float(config["intervalos_minimos"].get(n, 0.5)) for n in ANALIZADORES}
    # Value: flag de verbose (lo togglea SIGUSR2, lo lee el analizador de fds)
    verbose = mp.Value("i", 1 if config.get("verbose") else 0)

    # --- señales (self-pipe) ---
    fd_senales = mod_senales.instalar_handlers()

    # --- lanzar hijos ---
    hijos = {}

    hijos["recolector"] = mp.Process(
        target=recolector,
        args=(cola_pedidos, colas_trabajo, evento_salida),
        name="recolector", daemon=True)
    hijos["recolector"].start()

    hijos["agregador"] = mp.Process(
        target=agregador,
        args=(cola_resultados, snapshot, evento_salida),
        name="agregador", daemon=True)
    hijos["agregador"].start()

    for nombre in ANALIZADORES:
        hijos[nombre] = lanzar_analizador(
            nombre, cola_pedidos, colas_trabajo[nombre], cola_resultados,
            intervalos[nombre], verbose, evento_salida)

    def supervisar():
        """
        Watchdog: si un analizador muere (lo mataron con kill, o explotó),
        lo relanzamos. Se llama desde el loop de la TUI cada ~2s.
        Devuelve la lista de nombres que hubo que revivir, para avisar en pantalla.
        """
        revividos = []
        if evento_salida.is_set():
            return revividos
        for nombre in list(ANALIZADORES):
            p = hijos.get(nombre)
            if p is not None and not p.is_alive():
                p.join(timeout=0)
                hijos[nombre] = lanzar_analizador(
                    nombre, cola_pedidos, colas_trabajo[nombre], cola_resultados,
                    intervalos[nombre], verbose, evento_salida)
                revividos.append(nombre)
        for nombre, proceso in (("recolector", recolector), ("agregador", agregador)):
            p = hijos.get(nombre)
            if p is not None and not p.is_alive():
                p.join(timeout=0)
                if nombre == "recolector":
                    args = (cola_pedidos, colas_trabajo, evento_salida)
                else:
                    args = (cola_resultados, snapshot, evento_salida)
                nuevo = mp.Process(target=proceso, args=args, name=nombre, daemon=True)
                nuevo.start()
                hijos[nombre] = nuevo
                revividos.append(nombre)
        return revividos

    contexto = {
        "snapshot": snapshot,
        "supervisar": supervisar,
        "intervalos": intervalos,
        "minimos": minimos,
        "verbose": verbose,
        "evento_salida": evento_salida,
        "fd_senales": fd_senales,
        "config": config,
        "ruta_config": args.config,
        "dir_dumps": args.dumps,
    }

    salida_limpia = True
    try:
        curses.wrapper(tui_loop, contexto)
    except KeyboardInterrupt:
        evento_salida.set()
    except (BrokenPipeError, EOFError, ConnectionError):
        # Pasa cuando la señal fue al grupo entero y el Manager murió
        # primero. No es un error nuestro: es parte del apagado.
        evento_salida.set()
    except Exception as e:
        salida_limpia = False
        evento_salida.set()
        # curses ya restauró la terminal vía wrapper, así que se puede imprimir
        print(f"Error en la interfaz: {e}", file=sys.stderr)
    finally:
        evento_salida.set()
        apagar(hijos, manager, cola_pedidos, colas_trabajo, cola_resultados)

    if not encontrada:
        print(f"(aviso: no se encontró {args.config}, se usaron los valores por defecto)")
    print("Monitor cerrado.")
    sys.exit(0 if salida_limpia else 1)


def apagar(hijos, manager, cola_pedidos, colas_trabajo, cola_resultados):
    """
    Apagado limpio: primero pedimos por las buenas (evento_salida ya está
    seteado, los bucles lo chequean), después SIGTERM, y recién al final
    SIGKILL. Vaciamos las Queues porque un proceso que dejó datos sin
    consumir en una Queue no puede terminar de morir (el feeder thread
    queda esperando para escribir en el pipe interno).
    """
    for cola in list(colas_trabajo.values()) + [cola_pedidos, cola_resultados]:
        try:
            while True:
                cola.get_nowait()
        except Exception:
            pass

    limite = time.time() + 3.0
    for p in hijos.values():
        restante = max(0.1, limite - time.time())
        p.join(timeout=restante)

    for p in hijos.values():
        if p.is_alive():
            p.terminate()
            p.join(timeout=1.0)
        if p.is_alive():
            os.kill(p.pid, signal.SIGKILL)

    for cola in list(colas_trabajo.values()) + [cola_pedidos, cola_resultados]:
        try:
            cola.close()
            cola.join_thread()
        except Exception:
            pass

    try:
        manager.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    main()
