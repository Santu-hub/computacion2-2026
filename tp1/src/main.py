import multiprocessing
import signal
import sys
import curses
import os
from analizadores.resumen import analizador_resumen
from display import tui_loop
from analizadores.memoria import analizador_memoria
from analizadores.fds import analizador_fds
from analizadores.threads import analizador_threads
from analizadores.senales import analizador_senales
from analizadores.scheduling import analizador_scheduling
from analizadores.sistema import analizador_sistema

procesos = []
evento_salida = None

def signal_handler(signum, frame):
    """Captura Ctrl+C (SIGINT) o SIGTERM y avisa a los procesos que terminen."""
    if evento_salida:
        evento_salida.set()

def main():
    global evento_salida
    
    # 1. Configurar concurrencia
    manager = multiprocessing.Manager()
    snapshot = manager.dict()
    evento_salida = multiprocessing.Event()
    
    # Diccionario de intervalos (Value) para cada vista
    intervalos = {
        'resumen': multiprocessing.Value('d', 2.0),
        'memoria': multiprocessing.Value('d', 3.0),
        'fds': multiprocessing.Value('d', 3.0),
        'threads': multiprocessing.Value('d', 2.5),
        'senales': multiprocessing.Value('d', 10),
        'scheduling': multiprocessing.Value('d', 10.0),
        'sistema': multiprocessing.Value('d', 2.0),
    }
    
    # 2. Registrar Señales
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 3. Lanzar Analizadores
    p_resumen = multiprocessing.Process(
        target=analizador_resumen,
        args=(snapshot, intervalos['resumen'], evento_salida)
    )
    p_resumen.start()
    procesos.append(p_resumen)

    p_memoria = multiprocessing.Process(
        target=analizador_memoria,
        args=(snapshot, intervalos['memoria'], evento_salida)
    )
    p_memoria.start()
    procesos.append(p_memoria)

    p_fds = multiprocessing.Process(
        target=analizador_fds,
        args=(snapshot, intervalos['fds'], evento_salida)
    )
    p_fds.start()
    procesos.append(p_fds)

    p_threads = multiprocessing.Process(
        target=analizador_threads,
        args=(snapshot, intervalos['threads'], evento_salida)
    )
    p_threads.start()
    procesos.append(p_threads)

    p_senales = multiprocessing.Process(
        target=analizador_senales,
        args=(snapshot, intervalos['senales'], evento_salida)
    )
    p_senales.start()
    procesos.append(p_senales)

    p_scheduling = multiprocessing.Process(
        target=analizador_scheduling,
        args=(snapshot, intervalos['scheduling'], evento_salida)
    )
    p_scheduling.start()
    procesos.append(p_scheduling)

    p_sistema = multiprocessing.Process(
        target=analizador_sistema,
        args=(snapshot, intervalos['sistema'], evento_salida)
    )
    p_sistema.start()
    procesos.append(p_sistema)

    # Lanzar display (bloquea el hilo principal hasta que termine)
    try:
        curses.wrapper(tui_loop, snapshot, intervalos, evento_salida)
    except KeyboardInterrupt:
        evento_salida.set()
        
    # 4. Apagado limpio
    print("\nApagando monitor y cerrando analizadores limpiamente...")
    for p in procesos:
        p.join(timeout=2)
        if p.is_alive():
            p.terminate()
            
    manager.shutdown()
    sys.exit(0)

if __name__ == "__main__":
    main()