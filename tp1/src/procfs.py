import os
import signal


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
import signal

def decodificar_mascara(mascara_hex):
    """Convierte una máscara hexadecimal en nombres de señales legibles."""
    try:
        mascara = int(mascara_hex, 16)
    except ValueError:
        return "-"
        
    if mascara == 0:
        return "-"
        
    nombres_senales = []
    # Revisamos los 64 bits posibles
    for i in range(1, 65): 
        if mascara & (1 << (i - 1)):
            try:
                # Intenta sacar el nombre oficial (ej: SIGKILL, SIGTERM)
                nombre = signal.Signals(i).name
                nombres_senales.append(nombre)
            except ValueError:
                # Si es una señal extraña o de tiempo real, ponemos su número
                nombres_senales.append(f"SIG{i}")
                
    return ",".join(nombres_senales)

def leer_senales(pid):
    """Lee /proc/<pid>/status para extraer las máscaras de señales."""
    senales_data = {"pid": pid, "pendientes": "-", "bloqueadas": "-", "ignoradas": "-", "capturadas": "-"}
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            for linea in f:
                if linea.startswith("SigPnd:"):
                    senales_data["pendientes"] = decodificar_mascara(linea.split()[1])
                elif linea.startswith("SigBlk:"):
                    senales_data["bloqueadas"] = decodificar_mascara(linea.split()[1])
                elif linea.startswith("SigIgn:"):
                    senales_data["ignoradas"] = decodificar_mascara(linea.split()[1])
                elif linea.startswith("SigCgt:"):
                    senales_data["capturadas"] = decodificar_mascara(linea.split()[1])
        return senales_data
    except FileNotFoundError:
        return None

def leer_scheduling(pid):
    """Lee estadísticas de planificación de CPU de un proceso."""
    sched_data = {"pid": pid, "prioridad": "-", "nice": "-", "politica": "-", "afinidad": "-", "ctx_switches": "0"}
    try:
        # 1. Leer stat para prioridad (campo 18), nice (campo 19) y politica (campo 41)
        with open(f"/proc/{pid}/stat", "r") as f:
            contenido = f.read().strip()
            # Cortamos a partir del nombre del proceso para no desfasar índices si el nombre tiene espacios
            parte_der = contenido.split(') ')[1]
            campos = parte_der.split()
            
            sched_data["prioridad"] = campos[15] # Índice 15 es el campo 18
            sched_data["nice"] = campos[16]      # Índice 16 es el campo 19
            
            if len(campos) > 38:
                # Decodificamos las políticas más comunes de Linux
                pol = campos[38]
                politicas = {"0": "OTHER", "1": "FIFO", "2": "RR", "3": "BATCH", "5": "IDLE"}
                sched_data["politica"] = politicas.get(pol, pol)

        # 2. Leer status para afinidad (Cpus_allowed_list) y context switches
        vol_ctx = 0
        nonvol_ctx = 0
        with open(f"/proc/{pid}/status", "r") as f:
            for linea in f:
                if linea.startswith("Cpus_allowed_list:"):
                    sched_data["afinidad"] = linea.split()[1]
                elif linea.startswith("voluntary_ctxt_switches:"):
                    vol_ctx = int(linea.split()[1])
                elif linea.startswith("nonvoluntary_ctxt_switches:"):
                    nonvol_ctx = int(linea.split()[1])
        
        # Sumamos los voluntarios y los involuntarios
        sched_data["ctx_switches"] = str(vol_ctx + nonvol_ctx)
        
        return sched_data
    except (FileNotFoundError, IndexError):
        return None

def leer_sistema():
    """Lee estadísticas globales del sistema desde múltiples archivos."""
    sys_data = {}
    
    # 1. Load Average (Carga promedio)
    try:
        with open('/proc/loadavg', 'r') as f:
            sys_data['load'] = f.read().split()[:3]
    except FileNotFoundError:
        sys_data['load'] = ['0.00', '0.00', '0.00']
        
    # 2. Memoria RAM
    try:
        with open('/proc/meminfo', 'r') as f:
            mem = {}
            for linea in f:
                partes = linea.split()
                # Limpiamos los dos puntos y guardamos el valor en KB
                mem[partes[0].replace(':', '')] = int(partes[1])
            
            total = mem.get('MemTotal', 1)
            # Usamos MemAvailable si existe (Linux moderno), sino MemFree
            libre = mem.get('MemAvailable', mem.get('MemFree', 0))
            usada = total - libre
            
            sys_data['ram_total'] = total
            sys_data['ram_usada'] = usada
            sys_data['ram_porcentaje'] = (usada / total) * 100
    except FileNotFoundError:
        sys_data['ram_total'] = sys_data['ram_usada'] = sys_data['ram_porcentaje'] = 0
        
    # 3. CPU y Procesos Totales
    try:
        with open('/proc/stat', 'r') as f:
            for linea in f:
                if linea.startswith('cpu '):
                    # Sumamos los tiempos (ticks) de CPU
                    tiempos = [float(x) for x in linea.split()[1:]]
                    idle = tiempos[3] + tiempos[4] # idle + iowait
                    total = sum(tiempos)
                    sys_data['cpu_tiempos'] = (idle, total)
                elif linea.startswith('procs_running'):
                    sys_data['procs_running'] = linea.split()[1]
                elif linea.startswith('procs_blocked'):
                    sys_data['procs_blocked'] = linea.split()[1]
    except FileNotFoundError:
        sys_data['cpu_tiempos'] = (0, 0)
        sys_data['procs_running'] = '0'
        sys_data['procs_blocked'] = '0'
        
    return sys_data