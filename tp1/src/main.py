import multiprocessing
import signal
import sys
import curses
import os
from analizadores.resumen import analizador_resumen
from display import tui_loop

# Variables globales para el shutdown limpio
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
        # Agregar los demás acá...
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