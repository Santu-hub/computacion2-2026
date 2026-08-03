import time
from procfs import obtener_pids, leer_senales

def analizador_senales(snapshot, intervalo, evento_salida):
    """Proceso independiente que recolecta las señales de los procesos."""
    while not evento_salida.is_set():
        pids = obtener_pids()
        datos = []
        
        for pid in pids:
            sig = leer_senales(pid)
            if sig:
                datos.append(sig)
        
        snapshot['senales'] = datos
        
        inicio = time.time()
        while time.time() - inicio < intervalo.value and not evento_salida.is_set():
            time.sleep(0.1)