import os

def obtener_pids():
    """Devuelve una lista con los PIDs numéricos de /proc."""
    try:
        return [pid for pid in os.listdir('/proc') if pid.isdigit()]
    except FileNotFoundError:
        return []

def leer_stat(pid):
    """Lee /proc/<pid>/stat y devuelve un diccionario con los datos."""
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            contenido = f.read().strip()
            # Aislar el nombre del comando (que puede tener espacios)
            parte_izq, parte_der = contenido.split(') ', 1)
            comando = parte_izq.split(' (')[1]
            campos = parte_der.split()
            
            return {
                "pid": pid,
                "comando": comando,
                "estado": campos[0], # R, S, Z...
                "ppid": campos[1],
                "threads": campos[17], # Número de threads
                "utime": int(campos[11]),
                "stime": int(campos[12])
            }
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return None