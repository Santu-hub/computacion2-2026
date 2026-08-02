import time
from procfs import obtener_pids, leer_memoria

def analizador_memoria(snapshot, intervalo, evento_salida):
    """Proceso independiente que recolecta datos para la vista Memoria."""
    while not evento_salida.is_set():
        pids = obtener_pids()
        datos = []
        
        for pid in pids:
            mem = leer_memoria(pid)
            if mem:
                datos.append(mem)
        
        # Escribir en la pizarra compartida
        snapshot['memoria'] = datos
        
        # Dormir el tiempo que diga la configuración
        inicio = time.time()
        while time.time() - inicio < intervalo.value and not evento_salida.is_set():
            time.sleep(0.1)