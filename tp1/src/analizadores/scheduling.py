import time
from procfs import obtener_pids, leer_scheduling

def analizador_scheduling(snapshot, intervalo, evento_salida):
    """Proceso que recolecta datos del planificador (Scheduler)."""
    while not evento_salida.is_set():
        pids = obtener_pids()
        datos = []
        
        for pid in pids:
            sched = leer_scheduling(pid)
            if sched:
                datos.append(sched)
        
        snapshot['scheduling'] = datos
        
        inicio = time.time()
        while time.time() - inicio < intervalo.value and not evento_salida.is_set():
            time.sleep(0.1)