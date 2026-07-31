import time
from procfs import obtener_pids, leer_stat

def analizador_resumen(snapshot, intervalo, evento_salida):
    """Proceso independiente que recolecta datos para la vista Resumen."""
    while not evento_salida.is_set():
        pids = obtener_pids()
        datos = []
        
        for pid in pids:
            stat = leer_stat(pid)
            if stat:
                datos.append(stat)
        
        # Escribir en la memoria compartida
        snapshot['resumen'] = datos
        
        # Dormir según el intervalo, pero chequeando si hay que salir
        inicio = time.time()
        while time.time() - inicio < intervalo.value and not evento_salida.is_set():
            time.sleep(0.1)