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

def leer_memoria(pid):
    """Lee /proc/<pid>/status para extraer estadísticas de memoria."""
    # Valores por defecto por si el proceso no tiene memoria asignada (ej. procesos del kernel)
    mem_data = {"pid": pid, "nombre": "", "vmsize": "0", "vmrss": "0", "vmswap": "0"}
    
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            for linea in f:
                if linea.startswith("Name:"):
                    mem_data["nombre"] = linea.split()[1]
                elif linea.startswith("VmSize:"):
                    mem_data["vmsize"] = linea.split()[1] # Está en kB
                elif linea.startswith("VmRSS:"):
                    mem_data["vmrss"] = linea.split()[1]
                elif linea.startswith("VmSwap:"):
                    mem_data["vmswap"] = linea.split()[1]
        return mem_data
    except FileNotFoundError:
        return None

def leer_fds(pid):
    """Lee /proc/<pid>/fd/ para listar los archivos abiertos y clasificar su tipo."""
    fds_data = []
    fd_dir = f"/proc/{pid}/fd/"
    
    try:
        # Primero chequeamos si tenemos permisos (procesos de root nos van a rebotar)
        if not os.access(fd_dir, os.R_OK):
            return None 
            
        for fd_num in os.listdir(fd_dir):
            ruta_absoluta = os.path.join(fd_dir, fd_num)
            try:
                # os.readlink nos dice a dónde apunta realmente el acceso directo
                destino = os.readlink(ruta_absoluta)
                
                # Inferimos el tipo según el texto
                tipo = "Desconocido"
                if destino.startswith("/dev/pts/") or destino.startswith("/dev/tty"):
                    tipo = "Terminal"
                elif destino.startswith("pipe:"):
                    tipo = "Pipe"
                elif destino.startswith("socket:"):
                    tipo = "Socket"
                elif destino.startswith("/"):
                    tipo = "Archivo"
                    
                fds_data.append({"fd": fd_num, "destino": destino, "tipo": tipo})
                
            except OSError:
                # Si el proceso cerró el archivo justo mientras lo leíamos, lo ignoramos
                pass
                
        return {"pid": pid, "fds": fds_data}
        
    except (FileNotFoundError, PermissionError):
        return None

def leer_threads(pid):
    """Lee /proc/<pid>/task/ para extraer los hilos de un proceso."""
    hilos_data = []
    task_dir = f"/proc/{pid}/task/"
    
    try:
        # Chequeamos si la carpeta existe y tenemos permisos
        if not os.path.isdir(task_dir) or not os.access(task_dir, os.R_OK):
            return None
            
        for tid in os.listdir(task_dir):
            if not tid.isdigit():
                continue
                
            # Leemos el archivo stat específico de este hilo
            try:
                with open(f"{task_dir}{tid}/stat", "r") as f:
                    contenido = f.read().strip()
                    parte_izq, parte_der = contenido.split(') ', 1)
                    comando = parte_izq.split(' (')[1]
                    campos = parte_der.split()
                    
                    estado = campos[0]
                    # Tiempo de CPU (sumando tiempo de usuario y sistema en 'ticks')
                    tiempo_cpu = int(campos[11]) + int(campos[12])
                    
                    hilos_data.append({
                        "tid": tid,
                        "comando": comando,
                        "estado": estado,
                        "tiempo_cpu": tiempo_cpu
                    })
            except (FileNotFoundError, IndexError):
                pass
                
        if hilos_data:
            return {"pid": pid, "hilos": hilos_data}
        return None
        
    except (FileNotFoundError, PermissionError):
        return None