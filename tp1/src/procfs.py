import os
import pwd
import signal
import time

# Jiffies por segundo del kernel. Los campos utime/stime de /proc/<pid>/stat
# están en "ticks" de este reloj, no en segundos reales -> hace falta esta
# constante para poder convertir deltas de jiffies en % de CPU.
CLK_TCK = os.sysconf("SC_CLK_TCK")


# ---------------------------------------------------------------------------
# Listado de procesos
# ---------------------------------------------------------------------------

def obtener_pids():
    """Devuelve una lista con los PIDs numéricos de /proc."""
    try:
        return [pid for pid in os.listdir("/proc") if pid.isdigit()]
    except FileNotFoundError:
        return []


# ---------------------------------------------------------------------------
# /proc/<pid>/stat — el archivo con más campos posicionales
# ---------------------------------------------------------------------------

def leer_campos_stat(pid):
    """
    Parsea /proc/<pid>/stat y devuelve la lista de campos ALINEADA como en
    proc(5): campos[0] es el campo 3 (state), porque el pid (campo 1) y el
    comm (campo 2) los sacamos aparte (el comm puede tener espacios y
    paréntesis, por eso se corta por ') ').

    O sea: para el campo N de la man page, el índice es campos[N - 3].
    """
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            contenido = f.read().strip()
        return _separar_comm(contenido)
    except (FileNotFoundError, ProcessLookupError, IndexError, ValueError):
        return None, None


def _separar_comm(contenido):
    """
    Separa el comm del resto de los campos de una línea de stat.

    El comm va entre paréntesis y PUEDE contener espacios, paréntesis y
    hasta la secuencia ") " (el kernel no escapa nada, sólo trunca a 15
    caracteres). Por eso NO alcanza con split(') ') ni con split().

    La forma correcta es buscar el ÚLTIMO ')' de la línea: después del comm
    ya no hay ningún paréntesis en el formato de stat, así que ese cierre
    es sin ambigüedad el del comm.
    """
    apertura = contenido.index("(")
    cierre = contenido.rindex(")")
    comando = contenido[apertura + 1:cierre]
    campos = contenido[cierre + 1:].split()
    return comando, campos


def leer_status_dict(pid):
    """Lee /proc/<pid>/status entero como un dict {clave: valor_crudo}."""
    datos = {}
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            for linea in f:
                if ":" not in linea:
                    continue
                clave, valor = linea.split(":", 1)
                datos[clave.strip()] = valor.strip()
    except FileNotFoundError:
        return None
    return datos


def leer_cmdline(pid):
    """
    Lee /proc/<pid>/cmdline (argumentos separados por \\0) y arma la línea
    de comando completa. Si está vacío (procesos kernel / zombies) devuelve
    cadena vacía para que el caller haga fallback al 'comm' de stat.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            crudo = f.read()
        if not crudo:
            return ""
        partes = crudo.split(b"\x00")
        return " ".join(p.decode(errors="replace") for p in partes if p)
    except FileNotFoundError:
        return ""


_cache_usuarios = {}


def resolver_usuario(uid):
    """Traduce UID a nombre de usuario, cacheando (getpwuid es I/O)."""
    if uid in _cache_usuarios:
        return _cache_usuarios[uid]
    try:
        nombre = pwd.getpwuid(int(uid)).pw_name
    except (KeyError, ValueError):
        nombre = uid
    _cache_usuarios[uid] = nombre
    return nombre


# ---------------------------------------------------------------------------
# Vista Resumen
# ---------------------------------------------------------------------------

def leer_resumen(pid, cache_cpu):
    """
    Datos básicos de un proceso para la vista Resumen, incluyendo CPU%.

    cache_cpu: dict compartido ENTRE LLAMADAS (vive en el proceso analizador,
    no en /proc) que guarda {pid: (utime+stime, timestamp)} de la lectura
    anterior. El CPU% de un proceso no es un dato instantáneo: hay que medir
    cuántos jiffies de CPU consumió ENTRE DOS LECTURAS y dividir por el
    tiempo real que pasó. Por eso el propio analizador tiene que sostener
    este estado mientras vive.
    """
    comando, campos = leer_campos_stat(pid)
    if campos is None or len(campos) < 15:
        return None

    estado = campos[0]
    ppid = campos[1]
    utime = int(campos[11])
    stime = int(campos[12])
    threads = campos[17] if len(campos) > 17 else "1"

    status = leer_status_dict(pid)
    uid = gid = "-"
    usuario = "-"
    rss = 0
    if status:
        if "Uid" in status:
            uid = status["Uid"].split()[0]
            usuario = resolver_usuario(uid)
        if "Gid" in status:
            gid = status["Gid"].split()[0]
        v = status.get("VmRSS", "0 kB").split()
        rss = int(v[0]) if v and v[0].isdigit() else 0

    cmdline = leer_cmdline(pid)
    comando_completo = cmdline if cmdline else f"[{comando}]"

    # --- cálculo de CPU% por delta de jiffies ---
    jiffies_actual = utime + stime
    ahora = time.time()
    cpu_pct = 0.0
    anterior = cache_cpu.get(pid)
    if anterior is not None:
        jiffies_prev, ts_prev = anterior
        delta_jiffies = jiffies_actual - jiffies_prev
        delta_tiempo = ahora - ts_prev
        if delta_tiempo > 0 and delta_jiffies >= 0:
            cpu_pct = 100.0 * (delta_jiffies / CLK_TCK) / delta_tiempo
    cache_cpu[pid] = (jiffies_actual, ahora)

    return {
        "pid": pid,
        "ppid": ppid,
        "uid": uid,
        "gid": gid,
        "usuario": usuario,
        "estado": estado,
        "comando": comando,
        "comando_completo": comando_completo,
        "threads": threads,
        "rss": rss,
        "cpu_pct": round(cpu_pct, 1),
    }


# ---------------------------------------------------------------------------
# Vista Memoria
# ---------------------------------------------------------------------------

def leer_memoria(pid):
    """VmSize/VmRSS/.../VmSwap + page faults + segmentos de /proc/<pid>/maps."""
    status = leer_status_dict(pid)
    if status is None:
        return None

    def kb(clave):
        v = status.get(clave, "0 kB").split()
        return int(v[0]) if v and v[0].isdigit() else 0

    _, campos = leer_campos_stat(pid)
    minflt = majflt = 0
    if campos and len(campos) > 10:
        # field10=minflt -> campos[7], field12=majflt -> campos[9]
        minflt = int(campos[7])
        majflt = int(campos[9])

    return {
        "pid": pid,
        "nombre": status.get("Name", "?"),
        "vmsize": kb("VmSize"),
        "vmrss": kb("VmRSS"),
        "vmhwm": kb("VmHWM"),
        "vmdata": kb("VmData"),
        "vmstk": kb("VmStk"),
        "vmexe": kb("VmExe"),
        "vmlib": kb("VmLib"),
        "vmswap": kb("VmSwap"),
        "minflt": minflt,
        "majflt": majflt,
        "segmentos": leer_segmentos_maps(pid),
    }


def leer_segmentos_maps(pid):
    """
    Agrupa /proc/<pid>/maps por tipo de segmento (heap/stack/text/data/shared)
    sumando el tamaño (fin - inicio) de cada línea, en KB.
    """
    segmentos = {"text": 0, "data": 0, "heap": 0, "stack": 0, "shared": 0, "otros": 0}
    try:
        with open(f"/proc/{pid}/maps", "r") as f:
            for linea in f:
                partes = linea.split()
                if len(partes) < 5:
                    continue
                rango, perms = partes[0], partes[1]
                inicio_s, fin_s = rango.split("-")
                tam_kb = (int(fin_s, 16) - int(inicio_s, 16)) // 1024
                ruta = partes[5] if len(partes) > 5 else ""

                if "[heap]" in ruta:
                    segmentos["heap"] += tam_kb
                elif "[stack]" in ruta:
                    segmentos["stack"] += tam_kb
                elif "s" in perms:  # mapeo compartido
                    segmentos["shared"] += tam_kb
                elif "x" in perms:  # con permiso de ejecución -> código
                    segmentos["text"] += tam_kb
                elif "w" in perms:  # escribible, sin ejecución -> datos
                    segmentos["data"] += tam_kb
                else:
                    segmentos["otros"] += tam_kb
    except FileNotFoundError:
        pass
    return segmentos


# ---------------------------------------------------------------------------
# Vista File Descriptors
# ---------------------------------------------------------------------------

def leer_fds(pid):
    """Lista los FDs abiertos de un proceso y clasifica su tipo."""
    fd_dir = f"/proc/{pid}/fd/"
    if not os.path.isdir(fd_dir) or not os.access(fd_dir, os.R_OK):
        return None

    fds_data = []
    try:
        for fd_num in os.listdir(fd_dir):
            ruta_absoluta = os.path.join(fd_dir, fd_num)
            try:
                destino = os.readlink(ruta_absoluta)
            except OSError:
                continue  # el fd se cerró justo mientras lo leíamos

            if destino.startswith("/dev/pts/") or destino.startswith("/dev/tty"):
                tipo = "Terminal"
            elif destino.startswith("pipe:"):
                tipo = "Pipe"
            elif destino.startswith("socket:"):
                tipo = "Socket"
            elif destino.startswith("anon_inode:"):
                tipo = "Anon_inode"
            elif destino.startswith("/memfd:"):
                tipo = "Memfd"
            elif destino.startswith("/"):
                tipo = "Archivo (borrado)" if "(deleted)" in destino else "Archivo"
            else:
                tipo = "Desconocido"

            fds_data.append({"fd": fd_num, "destino": destino, "tipo": tipo})
    except (FileNotFoundError, PermissionError):
        return None

    return {"pid": pid, "fds": fds_data}


# ---------------------------------------------------------------------------
# Vista Threads
# ---------------------------------------------------------------------------

def leer_threads(pid, cache_cpu):
    """
    Threads (LWPs) de un proceso: estado, CPU% (delta de jiffies, igual
    idea que en leer_resumen pero por thread) y context switches.
    """
    task_dir = f"/proc/{pid}/task/"
    if not os.path.isdir(task_dir) or not os.access(task_dir, os.R_OK):
        return None

    hilos_data = []
    try:
        for tid in os.listdir(task_dir):
            if not tid.isdigit():
                continue

            comando, campos = leer_campos_stat_task(pid, tid)
            if campos is None or len(campos) < 15:
                continue

            estado = campos[0]
            utime = int(campos[11])
            stime = int(campos[12])

            # nombre "oficial" del thread, no truncado como en /stat
            try:
                with open(f"{task_dir}{tid}/comm", "r") as f:
                    nombre = f.read().strip()
            except FileNotFoundError:
                nombre = comando

            vol_ctx = nonvol_ctx = 0
            status = {}
            try:
                with open(f"{task_dir}{tid}/status", "r") as f:
                    for linea in f:
                        if linea.startswith("voluntary_ctxt_switches:"):
                            vol_ctx = int(linea.split()[1])
                        elif linea.startswith("nonvoluntary_ctxt_switches:"):
                            nonvol_ctx = int(linea.split()[1])
            except FileNotFoundError:
                pass

            clave = f"{pid}:{tid}"
            jiffies_actual = utime + stime
            ahora = time.time()
            cpu_pct = 0.0
            anterior = cache_cpu.get(clave)
            if anterior is not None:
                jiffies_prev, ts_prev = anterior
                delta_j = jiffies_actual - jiffies_prev
                delta_t = ahora - ts_prev
                if delta_t > 0 and delta_j >= 0:
                    cpu_pct = 100.0 * (delta_j / CLK_TCK) / delta_t
            cache_cpu[clave] = (jiffies_actual, ahora)

            hilos_data.append({
                "tid": tid,
                "comando": nombre,
                "estado": estado,
                "cpu_pct": round(cpu_pct, 1),
                "vol_ctx": vol_ctx,
                "nonvol_ctx": nonvol_ctx,
            })
    except (FileNotFoundError, PermissionError):
        return None

    if not hilos_data:
        return None
    return {"pid": pid, "hilos": hilos_data}


def leer_campos_stat_task(pid, tid):
    try:
        with open(f"/proc/{pid}/task/{tid}/stat", "r") as f:
            contenido = f.read().strip()
        return _separar_comm(contenido)
    except (FileNotFoundError, IndexError, ValueError):
        return None, None


# ---------------------------------------------------------------------------
# Vista Señales
# ---------------------------------------------------------------------------

def decodificar_mascara(mascara_hex):
    """Convierte una máscara hexadecimal de 64 bits en nombres de señales."""
    try:
        mascara = int(mascara_hex, 16)
    except ValueError:
        return "-"
    if mascara == 0:
        return "-"

    nombres = []
    for i in range(1, 65):
        if mascara & (1 << (i - 1)):
            try:
                nombres.append(signal.Signals(i).name)
            except ValueError:
                nombres.append(f"SIG{i}")  # señales de tiempo real (RT), sin nombre fijo
    return ",".join(nombres)


def leer_senales(pid):
    status = leer_status_dict(pid)
    if status is None:
        return None
    return {
        "pid": pid,
        "pendientes": decodificar_mascara(status.get("SigPnd", "0")),
        "pendientes_grupo": decodificar_mascara(status.get("ShdPnd", "0")),
        "bloqueadas": decodificar_mascara(status.get("SigBlk", "0")),
        "ignoradas": decodificar_mascara(status.get("SigIgn", "0")),
        "capturadas": decodificar_mascara(status.get("SigCgt", "0")),
    }


# ---------------------------------------------------------------------------
# Vista Scheduling
# ---------------------------------------------------------------------------

_POLITICAS = {"0": "OTHER", "1": "FIFO", "2": "RR", "3": "BATCH", "5": "IDLE", "6": "DEADLINE"}


def leer_scheduling(pid):
    _, campos = leer_campos_stat(pid)
    if campos is None or len(campos) < 20:
        return None

    # field N de proc(5) -> campos[N - 3]
    pgid = campos[2]                                        # field 5  (pgrp)
    sid = campos[3]                                         # field 6  (session)
    prioridad = campos[15]                                  # field 18 (priority)
    nice = campos[16]                                       # field 19 (nice)
    rt_priority = campos[37] if len(campos) > 37 else "0"   # field 40
    politica_raw = campos[38] if len(campos) > 38 else "-"  # field 41
    utime = campos[11]
    stime = campos[12]

    politica = _POLITICAS.get(politica_raw, politica_raw)

    status = leer_status_dict(pid) or {}
    afinidad = status.get("Cpus_allowed_list", "-")
    vol_ctx = int(status.get("voluntary_ctxt_switches", "0"))
    nonvol_ctx = int(status.get("nonvoluntary_ctxt_switches", "0"))

    return {
        "pid": pid,
        "sid": sid,
        "pgid": pgid,
        "prioridad": prioridad,
        "nice": nice,
        "politica": politica,
        "rt_priority": rt_priority,
        "afinidad": afinidad,
        "vol_ctx": vol_ctx,
        "nonvol_ctx": nonvol_ctx,
        "utime": utime,
        "stime": stime,
    }


# ---------------------------------------------------------------------------
# Vista Sistema (stats globales)
# ---------------------------------------------------------------------------

def leer_boot_time():
    try:
        with open("/proc/stat") as f:
            for linea in f:
                if linea.startswith("btime"):
                    return int(linea.split()[1])
    except FileNotFoundError:
        pass
    return 0


def leer_uptime():
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except (FileNotFoundError, ValueError):
        return 0.0


def leer_sistema(pids=None):
    """
    Stats globales: CPU, memoria, load, y recorrido de todos los procesos
    para totales por estado / threads totales / zombies.

    Si el recolector ya listó los PIDs, se los pasamos para no volver a
    hacer listdir('/proc') al pedo.
    """
    sys_data = {}

    try:
        with open("/proc/loadavg", "r") as f:
            sys_data["load"] = f.read().split()[:3]
    except FileNotFoundError:
        sys_data["load"] = ["0.00", "0.00", "0.00"]

    try:
        with open("/proc/meminfo", "r") as f:
            mem = {}
            for linea in f:
                partes = linea.split()
                mem[partes[0].replace(":", "")] = int(partes[1])
        total = mem.get("MemTotal", 1)
        libre = mem.get("MemAvailable", mem.get("MemFree", 0))
        sys_data["ram_total"] = total
        sys_data["ram_usada"] = total - libre
        sys_data["ram_libre"] = libre
        sys_data["ram_buffers"] = mem.get("Buffers", 0)
        sys_data["ram_cached"] = mem.get("Cached", 0)
        sys_data["swap_total"] = mem.get("SwapTotal", 0)
        sys_data["swap_libre"] = mem.get("SwapFree", 0)
        sys_data["ram_porcentaje"] = (sys_data["ram_usada"] / total) * 100 if total else 0
    except FileNotFoundError:
        sys_data.update({"ram_total": 0, "ram_usada": 0, "ram_porcentaje": 0})

    try:
        with open("/proc/stat", "r") as f:
            for linea in f:
                if linea.startswith("cpu "):
                    # user nice system idle iowait irq softirq ...
                    tiempos = [float(x) for x in linea.split()[1:]]
                    sys_data["cpu_tiempos"] = tiempos
                elif linea.startswith("procs_running"):
                    sys_data["procs_running"] = linea.split()[1]
                elif linea.startswith("procs_blocked"):
                    sys_data["procs_blocked"] = linea.split()[1]
    except FileNotFoundError:
        sys_data["cpu_tiempos"] = [0.0] * 8
        sys_data["procs_running"] = "0"
        sys_data["procs_blocked"] = "0"

    # Recorrido de /proc para totales: procesos por estado, threads y zombies
    conteo_estados = {}
    threads_total = 0
    if pids is None:
        pids = obtener_pids()
    for pid in pids:
        _, campos = leer_campos_stat(pid)
        if campos is None:
            continue
        estado = campos[0]
        conteo_estados[estado] = conteo_estados.get(estado, 0) + 1
        if len(campos) > 17:
            try:
                threads_total += int(campos[17])
            except ValueError:
                pass

    sys_data["procesos_por_estado"] = conteo_estados
    sys_data["procesos_total"] = sum(conteo_estados.values())
    sys_data["threads_total"] = threads_total
    sys_data["zombies"] = conteo_estados.get("Z", 0)
    sys_data["boot_time"] = leer_boot_time()
    sys_data["uptime"] = leer_uptime()

    return sys_data
