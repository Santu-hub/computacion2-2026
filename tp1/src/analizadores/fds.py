import time
from procfs import obtener_pids, leer_fds

def analizador_fds(snapshot, intervalo, evento_salida):
    """Proceso que recolecta los File Descriptors de todos los procesos."""
    while not evento_salida.is_set():
        pids = obtener_pids()
        datos = []
        
        for pid in pids:
            fd_info = leer_fds(pid)
            if fd_info:
                datos.append(fd_info)
        
        snapshot['fds'] = datos
        
        inicio = time.time()
        while time.time() - inicio < intervalo.value and not evento_salida.is_set():
            time.sleep(0.1)