import time
from procfs import obtener_pids, leer_threads

def analizador_threads(snapshot, intervalo, evento_salida):
    """Proceso independiente que recolecta datos de los Hilos (Threads)."""
    while not evento_salida.is_set():
        pids = obtener_pids()
        datos = []
        
        for pid in pids:
            hilos = leer_threads(pid)
            if hilos:
                datos.append(hilos)
        
        snapshot['threads'] = datos
        
        inicio = time.time()
        while time.time() - inicio < intervalo.value and not evento_salida.is_set():
            time.sleep(0.1)