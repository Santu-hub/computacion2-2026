import curses
import signal as _signal
import time

import senales as mod_senales

VISTAS = ["resumen", "memoria", "fds", "threads", "senales", "scheduling", "sistema"]

TECLAS_VISTA = {
    ord("1"): "resumen", ord("r"): "resumen",
    ord("2"): "memoria", ord("m"): "memoria",
    ord("3"): "fds", ord("f"): "fds",
    ord("4"): "threads", ord("t"): "threads",
    ord("5"): "senales", ord("s"): "senales",
    ord("6"): "scheduling", ord("p"): "scheduling",
    ord("7"): "sistema", ord("g"): "sistema",
}

NOMBRES_ESTADO = {
    "R": "Running", "S": "Sleeping", "D": "Disk sleep", "Z": "Zombie",
    "T": "Stopped", "t": "Tracing stop", "X": "Dead", "I": "Idle",
}

ORDENES = ["cpu", "rss", "pid"]


def _texto(stdscr, y, x, texto, attr=0):
    """addstr que no explota si el texto no entra en la pantalla."""
    try:
        alto, ancho = stdscr.getmaxyx()
        if y < 0 or y >= alto or x >= ancho:
            return
        stdscr.addstr(y, x, str(texto)[: ancho - x - 1], attr)
    except curses.error:
        pass


def _kb(valor):
    """Formatea kilobytes de forma legible."""
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        return "-"
    if valor >= 1024 * 1024:
        return f"{valor / 1024 / 1024:.1f}G"
    if valor >= 1024:
        return f"{valor / 1024:.1f}M"
    return f"{valor:.0f}K"


def _barra(pct, ancho=40):
    pct = max(0.0, min(100.0, float(pct)))
    llenos = int(ancho * pct / 100)
    return "[" + "|" * llenos + " " * (ancho - llenos) + "]"


def _leer_texto(stdscr, prompt):
    """Pide un texto al usuario en la última línea (para / y u)."""
    alto, ancho = stdscr.getmaxyx()
    curses.echo()
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    stdscr.nodelay(False)
    _texto(stdscr, alto - 1, 0, " " * (ancho - 1))
    _texto(stdscr, alto - 1, 0, prompt, curses.A_BOLD)
    try:
        entrada = stdscr.getstr(alto - 1, len(prompt) + 1, 40).decode(errors="replace")
    except (curses.error, KeyboardInterrupt):
        entrada = ""
    curses.noecho()
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.nodelay(True)
    stdscr.timeout(250)
    return entrada.strip()


_SECUENCIAS = {
    # Modo normal (ESC [ x) y modo aplicación (ESC O x). curses pone la
    # terminal en modo aplicación al arrancar, pero tmux, screen y algunos
    # emuladores mandan igual la forma normal, así que aceptamos las dos.
    "[A": curses.KEY_UP, "OA": curses.KEY_UP,
    "[B": curses.KEY_DOWN, "OB": curses.KEY_DOWN,
    "[C": curses.KEY_RIGHT, "OC": curses.KEY_RIGHT,
    "[D": curses.KEY_LEFT, "OD": curses.KEY_LEFT,
    "[5~": curses.KEY_PPAGE, "[6~": curses.KEY_NPAGE,
}


def _leer_tecla(stdscr):
    """
    getch() con fallback: si terminfo no traduce la secuencia de la flecha,
    nos llega el ESC (27) suelto seguido de los bytes de la secuencia. En ese
    caso los leemos sin bloquear y los traducimos a mano.
    """
    tecla = stdscr.getch()
    if tecla != 27:
        return tecla

    stdscr.nodelay(True)
    seq = ""
    for _ in range(4):
        siguiente = stdscr.getch()
        if siguiente == -1:
            break
        seq += chr(siguiente)
        if seq in _SECUENCIAS:
            break
    stdscr.timeout(250)
    return _SECUENCIAS.get(seq, 27)


def _obtener(snapshot, clave):
    """Devuelve (datos, antigüedad_en_segundos) de una clave del snapshot."""
    entrada = snapshot.get(clave)
    if not entrada:
        return None, None
    return entrada.get("datos"), time.time() - entrada.get("ts", time.time())


# ---------------------------------------------------------------------------
# Paneles de detalle
# ---------------------------------------------------------------------------

def _panel_resumen(stdscr, y, alto, proc):
    if not proc:
        _texto(stdscr, y, 2, "Sin proceso seleccionado.")
        return
    estado = NOMBRES_ESTADO.get(proc["estado"], proc["estado"])
    _texto(stdscr, y, 2, f"PID {proc['pid']}   PPID {proc['ppid']}   "
                         f"Usuario {proc['usuario']} (UID {proc['uid']}/GID {proc['gid']})")
    _texto(stdscr, y + 1, 2, f"Estado: {proc['estado']} ({estado})    "
                             f"CPU: {proc['cpu_pct']}%    RSS: {_kb(proc['rss'])}    "
                             f"Threads: {proc['threads']}")
    _texto(stdscr, y + 3, 2, "Comando completo (/proc/<pid>/cmdline):", curses.A_BOLD)
    cmd = proc["comando_completo"]
    _, ancho = stdscr.getmaxyx()
    ancho_util = max(20, ancho - 6)
    for i in range(3):
        trozo = cmd[i * ancho_util:(i + 1) * ancho_util]
        if not trozo:
            break
        _texto(stdscr, y + 4 + i, 4, trozo)


def _panel_memoria(stdscr, y, alto, pid, datos):
    info = next((d for d in (datos or []) if d["pid"] == pid), None)
    if not info:
        _texto(stdscr, y, 2, "Sin datos de memoria para este proceso todavía.")
        return
    _texto(stdscr, y, 2, f"PID {info['pid']} ({info['nombre']})", curses.A_BOLD)
    _texto(stdscr, y + 1, 2,
           f"VmSize {_kb(info['vmsize']):>8}   VmRSS {_kb(info['vmrss']):>8}   "
           f"VmHWM {_kb(info['vmhwm']):>8}   VmSwap {_kb(info['vmswap']):>8}")
    _texto(stdscr, y + 2, 2,
           f"VmData {_kb(info['vmdata']):>8}   VmStk {_kb(info['vmstk']):>8}   "
           f"VmExe {_kb(info['vmexe']):>8}   VmLib  {_kb(info['vmlib']):>8}")
    _texto(stdscr, y + 3, 2,
           f"Page faults -> minor: {info['minflt']}    major: {info['majflt']}   "
           f"(major = hubo que ir al disco)")
    seg = info.get("segmentos", {})
    _texto(stdscr, y + 5, 2, "Segmentos agrupados de /proc/<pid>/maps:", curses.A_BOLD)
    _texto(stdscr, y + 6, 4,
           f"text(r-x) {_kb(seg.get('text', 0)):>8}   data(rw-) {_kb(seg.get('data', 0)):>8}   "
           f"heap {_kb(seg.get('heap', 0)):>8}   stack {_kb(seg.get('stack', 0)):>8}   "
           f"shared {_kb(seg.get('shared', 0)):>8}")


def _panel_fds(stdscr, y, alto, pid, datos, verbose):
    info = next((d for d in (datos or []) if d["pid"] == pid), None)
    if not info:
        _texto(stdscr, y, 2, "Sin FDs legibles para este proceso "
                             "(puede ser de otro usuario o ya murió).")
        return
    total = info.get("total_fds", len(info["fds"]))
    modo = "verbose" if verbose else "normal"
    _texto(stdscr, y, 2, f"PID {info['pid']} — {total} FDs abiertos "
                         f"(mostrando {len(info['fds'])}, modo {modo}: SIGUSR2 alterna)",
           curses.A_BOLD)
    _texto(stdscr, y + 1, 2, f"{'FD':<6} {'TIPO':<18} DESTINO", curses.A_UNDERLINE)
    fila = y + 2
    for fd in info["fds"]:
        if fila >= alto - 1:
            break
        _texto(stdscr, fila, 2, f"{fd['fd']:<6} {fd['tipo']:<18} {fd['destino']}")
        fila += 1


def _panel_threads(stdscr, y, alto, pid, datos):
    info = next((d for d in (datos or []) if d["pid"] == pid), None)
    if not info:
        _texto(stdscr, y, 2, "Sin datos de threads para este proceso todavía.")
        return
    _texto(stdscr, y, 2, f"PID {info['pid']} — {len(info['hilos'])} threads (LWPs) "
                         f"en /proc/{info['pid']}/task/", curses.A_BOLD)
    _texto(stdscr, y + 1, 2,
           f"{'TID':<10} {'ESTADO':<8} {'CPU%':>7}  {'CTX VOL':>10} {'CTX INVOL':>11}  NOMBRE",
           curses.A_UNDERLINE)
    fila = y + 2
    for h in sorted(info["hilos"], key=lambda x: x["cpu_pct"], reverse=True):
        if fila >= alto - 1:
            break
        marca = "  <- main" if h["tid"] == info["pid"] else ""
        _texto(stdscr, fila, 2,
               f"{h['tid']:<10} {h['estado']:<8} {h['cpu_pct']:>6.1f}%  "
               f"{h['vol_ctx']:>10} {h['nonvol_ctx']:>11}  {h['comando']}{marca}")
        fila += 1


def _panel_senales(stdscr, y, alto, pid, datos):
    info = next((d for d in (datos or []) if d["pid"] == pid), None)
    if not info:
        _texto(stdscr, y, 2, "Sin datos de señales para este proceso todavía.")
        return
    _texto(stdscr, y, 2, f"PID {info['pid']} — máscaras de /proc/<pid>/status decodificadas",
           curses.A_BOLD)
    filas = [
        ("SigPnd (pendientes al proceso)", info["pendientes"]),
        ("ShdPnd (pendientes al grupo)", info["pendientes_grupo"]),
        ("SigBlk (bloqueadas)", info["bloqueadas"]),
        ("SigIgn (ignoradas)", info["ignoradas"]),
        ("SigCgt (con handler propio)", info["capturadas"]),
    ]
    fila = y + 2
    _, ancho = stdscr.getmaxyx()
    for etiqueta, valor in filas:
        if fila >= alto - 1:
            break
        _texto(stdscr, fila, 2, f"{etiqueta:<34}", curses.A_BOLD)
        # las listas de señales son largas: las partimos en varias líneas
        disponible = max(20, ancho - 40)
        resto = valor
        primera = True
        while resto and fila < alto - 1:
            _texto(stdscr, fila, 38, resto[:disponible])
            resto = resto[disponible:]
            fila += 1
            primera = False
        if primera:
            fila += 1


def _panel_scheduling(stdscr, y, alto, pid, datos):
    info = next((d for d in (datos or []) if d["pid"] == pid), None)
    if not info:
        _texto(stdscr, y, 2, "Sin datos de scheduling para este proceso todavía.")
        return
    _texto(stdscr, y, 2, f"PID {info['pid']} — planificación", curses.A_BOLD)
    _texto(stdscr, y + 1, 2,
           f"Política: {info['politica']:<10} Nice: {info['nice']:<5} "
           f"Priority: {info['prioridad']:<5} RT prio: {info['rt_priority']}")
    _texto(stdscr, y + 2, 2,
           f"Afinidad de CPU (Cpus_allowed_list): {info['afinidad']}")
    _texto(stdscr, y + 3, 2,
           f"PGID: {info['pgid']}    SID: {info['sid']}    "
           f"utime: {info['utime']} jiffies    stime: {info['stime']} jiffies")
    _texto(stdscr, y + 5, 2, "Context switches:", curses.A_BOLD)
    _texto(stdscr, y + 6, 4,
           f"voluntarios:   {info['vol_ctx']:>10}  (el proceso cedió la CPU, "
           f"típico de I/O-bound)")
    _texto(stdscr, y + 7, 4,
           f"involuntarios: {info['nonvol_ctx']:>10}  (lo desalojó el scheduler, "
           f"típico de CPU-bound)")


def _panel_sistema(stdscr, y, alto, datos, tops):
    if not datos:
        _texto(stdscr, y, 2, "Recolectando datos del sistema...")
        return
    cpu = datos.get("cpu", {})
    _texto(stdscr, y, 2, "ESTADO GLOBAL DEL SISTEMA", curses.A_BOLD)
    _texto(stdscr, y + 1, 2,
           f"CPU  {_barra(cpu.get('uso', 0))} {cpu.get('uso', 0):5.1f}%   "
           f"user {cpu.get('user', 0):4.1f}%  sys {cpu.get('system', 0):4.1f}%  "
           f"idle {cpu.get('idle', 0):4.1f}%  iowait {cpu.get('iowait', 0):4.1f}%")

    ram_pct = datos.get("ram_porcentaje", 0)
    _texto(stdscr, y + 2, 2,
           f"RAM  {_barra(ram_pct)} {ram_pct:5.1f}%   "
           f"{_kb(datos.get('ram_usada', 0))} / {_kb(datos.get('ram_total', 0))}   "
           f"buffers {_kb(datos.get('ram_buffers', 0))}  cached {_kb(datos.get('ram_cached', 0))}")

    swap_total = datos.get("swap_total", 0)
    swap_usado = swap_total - datos.get("swap_libre", 0)
    swap_pct = (swap_usado / swap_total * 100) if swap_total else 0
    _texto(stdscr, y + 3, 2,
           f"SWAP {_barra(swap_pct)} {swap_pct:5.1f}%   "
           f"{_kb(swap_usado)} / {_kb(swap_total)}")

    load = datos.get("load", ["-", "-", "-"])
    uptime = datos.get("uptime", 0)
    horas = int(uptime // 3600)
    minutos = int((uptime % 3600) // 60)
    boot = datos.get("boot_time", 0)
    boot_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot)) if boot else "-"
    _texto(stdscr, y + 5, 2,
           f"Load average: {load[0]} (1m)  {load[1]} (5m)  {load[2]} (15m)     "
           f"Uptime: {horas}h {minutos}m     Boot: {boot_str}")

    estados = datos.get("procesos_por_estado", {})
    detalle = "  ".join(f"{NOMBRES_ESTADO.get(e, e)}({e}): {c}"
                        for e, c in sorted(estados.items()))
    _texto(stdscr, y + 6, 2,
           f"Procesos: {datos.get('procesos_total', 0)} total   "
           f"Threads: {datos.get('threads_total', 0)}   "
           f"Zombies: {datos.get('zombies', 0)}")
    _texto(stdscr, y + 7, 2, f"Por estado: {detalle}")
    _texto(stdscr, y + 8, 2,
           f"procs_running: {datos.get('procs_running', '0')}   "
           f"procs_blocked: {datos.get('procs_blocked', '0')}")

    if tops:
        _texto(stdscr, y + 10, 2, "TOP 3 por CPU:", curses.A_BOLD)
        for i, (pid, cmd, val) in enumerate(tops.get("cpu", [])):
            _texto(stdscr, y + 11 + i, 4, f"{pid:>8}  {cmd:<20} {val:>6.1f}%")
        _texto(stdscr, y + 10, 50, "TOP 3 por memoria (RSS):", curses.A_BOLD)
        for i, (pid, cmd, val) in enumerate(tops.get("mem", [])):
            _texto(stdscr, y + 11 + i, 52, f"{pid:>8}  {cmd:<20} {_kb(val):>8}")


def _panel_ayuda(stdscr, y, alto):
    lineas = [
        ("VISTAS", ""),
        ("1 / r", "Resumen — estado, CPU%, RSS, threads, comando"),
        ("2 / m", "Memoria — Vm*, page faults, segmentos de maps"),
        ("3 / f", "File descriptors — lista de FDs y su destino"),
        ("4 / t", "Threads — LWPs con CPU% y context switches"),
        ("5 / s", "Señales — máscaras decodificadas"),
        ("6 / p", "Scheduling — nice, priority, policy, afinidad"),
        ("7 / g", "Sistema — CPU, RAM, load, totales globales"),
        ("", ""),
        ("NAVEGACIÓN", ""),
        ("Flechas", "Mover la selección por la lista"),
        ("Enter", "Fijar (pin) el proceso seleccionado"),
        ("/", "Filtrar por nombre de comando"),
        ("u", "Filtrar por usuario"),
        ("c", "Cambiar orden (CPU% / RSS / PID)"),
        ("+ / -", "Ajustar el intervalo de la vista activa"),
        ("h / ?", "Esta ayuda"),
        ("q", "Salir"),
        ("", ""),
        ("SEÑALES", "kill -HUP <pid> recarga config.json | "
                    "-USR1 dump JSON | -USR2 verbose"),
    ]
    fila = y
    for izq, der in lineas:
        if fila >= alto - 1:
            break
        if izq and not der:
            _texto(stdscr, fila, 2, izq, curses.A_BOLD | curses.A_UNDERLINE)
        else:
            _texto(stdscr, fila, 4, f"{izq:<12} {der}")
        fila += 1


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

def tui_loop(stdscr, contexto):
    snapshot = contexto["snapshot"]
    intervalos = contexto["intervalos"]
    minimos = contexto["minimos"]
    verbose = contexto["verbose"]
    evento_salida = contexto["evento_salida"]
    fd_senales = contexto["fd_senales"]
    config = contexto["config"]

    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.nodelay(True)
    stdscr.timeout(250)
    # keypad(True) hace que curses traduzca las secuencias de escape de las
    # flechas a constantes (KEY_UP, KEY_DOWN...) en vez de darnos los bytes
    # crudos. curses.wrapper ya lo activa, pero lo dejamos explícito.
    stdscr.keypad(True)
    try:
        curses.use_default_colors()
    except curses.error:
        pass

    vista_activa = "resumen"
    seleccion = 0
    pin_pid = None
    filtro_cmd = config.get("filtro_comando", "")
    filtro_usr = config.get("filtro_usuario", "")
    orden = config.get("orden", "cpu")
    mostrar_ayuda = False
    mensaje = ""
    mensaje_hasta = 0.0
    supervisar = contexto.get("supervisar")
    proximo_chequeo = 0.0

    def avisar(texto, segundos=4):
        nonlocal mensaje, mensaje_hasta
        mensaje = texto
        mensaje_hasta = time.time() + segundos

    while not evento_salida.is_set():
        # ---------------- watchdog de procesos hijos ----------------
        if supervisar and time.time() > proximo_chequeo:
            proximo_chequeo = time.time() + 2.0
            revividos = supervisar()
            if revividos:
                mensaje = f"Watchdog: se relanzó {', '.join(revividos)}"
                mensaje_hasta = time.time() + 5

        # ---------------- señales pendientes (self-pipe) ----------------
        for signum in mod_senales.leer_senales_pendientes(fd_senales):
            if signum in (_signal.SIGINT, _signal.SIGTERM):
                evento_salida.set()
                break
            elif signum == _signal.SIGHUP:
                nueva, ok = mod_senales.cargar_config(contexto["ruta_config"])
                if ok:
                    mod_senales.aplicar_config(nueva, intervalos, verbose)
                    filtro_cmd = nueva.get("filtro_comando", filtro_cmd)
                    filtro_usr = nueva.get("filtro_usuario", filtro_usr)
                    orden = nueva.get("orden", orden)
                    avisar("SIGHUP: config.json recargada")
                else:
                    avisar("SIGHUP: no se pudo leer config.json")
            elif signum == _signal.SIGUSR1:
                try:
                    ruta = mod_senales.volcar_snapshot(snapshot, contexto["dir_dumps"])
                    avisar(f"SIGUSR1: snapshot volcado a {ruta}")
                except OSError as e:
                    avisar(f"SIGUSR1: error al volcar ({e})")
            elif signum == _signal.SIGUSR2:
                verbose.value = 0 if verbose.value else 1
                avisar(f"SIGUSR2: modo verbose {'ON' if verbose.value else 'OFF'}")
            elif signum == _signal.SIGWINCH:
                # La terminal cambió de tamaño: curses necesita releer
                # las dimensiones antes de redibujar.
                try:
                    curses.endwin()
                    stdscr.refresh()
                except curses.error:
                    pass

        if evento_salida.is_set():
            break

        alto, ancho = stdscr.getmaxyx()
        stdscr.erase()

        # ---------------- datos ----------------
        procesos, edad = _obtener(snapshot, "resumen")
        procesos = procesos or []

        if filtro_cmd:
            procesos = [p for p in procesos
                        if filtro_cmd.lower() in p["comando"].lower()
                        or filtro_cmd.lower() in p["comando_completo"].lower()]
        if filtro_usr:
            procesos = [p for p in procesos
                        if filtro_usr.lower() in p["usuario"].lower()]

        if orden == "cpu":
            procesos.sort(key=lambda p: p.get("cpu_pct", 0), reverse=True)
        elif orden == "rss":
            procesos.sort(key=lambda p: p.get("rss", 0), reverse=True)
        else:
            procesos.sort(key=lambda p: int(p["pid"]))

        # Si hay un proceso pineado, la selección lo sigue aunque cambie de
        # posición en el orden (ese es todo el punto del pin).
        if pin_pid is not None:
            idx = next((i for i, p in enumerate(procesos) if p["pid"] == pin_pid), None)
            if idx is not None:
                seleccion = idx
        if procesos:
            seleccion = max(0, min(seleccion, len(procesos) - 1))
        else:
            seleccion = 0
        proc_sel = procesos[seleccion] if procesos else None
        pid_sel = proc_sel["pid"] if proc_sel else None

        # ---------------- barra de título ----------------
        intervalo_actual = intervalos[vista_activa].value
        edad_txt = f"hace {edad:.1f}s" if edad is not None else "sin datos"
        titulo = (f" MONITOR /proc | vista: {vista_activa.upper():<11}"
                  f"| refresco: {intervalo_actual:.1f}s (min {minimos.get(vista_activa, 0.5):.1f}s) "
                  f"| datos: {edad_txt} | q=salir  h=ayuda ")
        _texto(stdscr, 0, 0, titulo.ljust(ancho - 1), curses.A_REVERSE)

        # ---------------- barra de estado ----------------
        estado_txt = (f" orden: {orden.upper():<4} "
                      f"| filtro cmd: {filtro_cmd or '-':<12} "
                      f"| usuario: {filtro_usr or '-':<10} "
                      f"| verbose: {'ON' if verbose.value else 'OFF':<3} "
                      f"| pin: {pin_pid or '-':<8} "
                      f"| procesos: {len(procesos)}")
        _texto(stdscr, 1, 0, estado_txt, curses.A_DIM)

        # ---------------- lista de procesos ----------------
        # La lista ocupa el tercio superior; el detalle, el resto.
        alto_lista = max(4, (alto - 6) // 3)
        fila_sep = 3 + alto_lista

        _texto(stdscr, 2, 0,
               f"{'PID':>8} {'USUARIO':<10} {'S':<2} {'CPU%':>6} {'RSS':>8} "
               f"{'THR':>4}  COMANDO",
               curses.A_BOLD | curses.A_UNDERLINE)

        # Scroll: mantenemos la selección siempre visible dentro de la ventana.
        primero = max(0, seleccion - alto_lista + 1) if seleccion >= alto_lista else 0
        for i, p in enumerate(procesos[primero:primero + alto_lista]):
            fila = 3 + i
            idx_real = primero + i
            attr = 0
            if idx_real == seleccion:
                attr = curses.A_REVERSE
            elif p["pid"] == pin_pid:
                attr = curses.A_BOLD
            if p["estado"] == "Z":
                attr |= curses.A_BLINK
            marca = "*" if p["pid"] == pin_pid else " "
            linea = (f"{p['pid']:>8} {p['usuario'][:10]:<10} {p['estado']:<2} "
                     f"{p['cpu_pct']:>6.1f} {_kb(p['rss']):>8} {p['threads']:>4} "
                     f"{marca}{p['comando_completo']}")
            _texto(stdscr, fila, 0, linea.ljust(ancho - 1), attr)

        if not procesos:
            _texto(stdscr, 3, 2, "Sin procesos que coincidan con el filtro "
                                 "(o los analizadores todavía no publicaron).")

        # ---------------- separador ----------------
        etiqueta = f" DETALLE: {vista_activa.upper()} "
        if pid_sel and vista_activa != "sistema":
            etiqueta += f"— PID {pid_sel} "
        _texto(stdscr, fila_sep, 0,
               ("─" * 2 + etiqueta).ljust(ancho - 1, "─"), curses.A_BOLD)

        # ---------------- panel de detalle ----------------
        y = fila_sep + 1
        if mostrar_ayuda:
            _panel_ayuda(stdscr, y, alto)
        elif vista_activa == "resumen":
            _panel_resumen(stdscr, y, alto, proc_sel)
        elif vista_activa == "memoria":
            datos, _ = _obtener(snapshot, "memoria")
            _panel_memoria(stdscr, y, alto, pid_sel, datos)
        elif vista_activa == "fds":
            datos, _ = _obtener(snapshot, "fds")
            _panel_fds(stdscr, y, alto, pid_sel, datos, bool(verbose.value))
        elif vista_activa == "threads":
            datos, _ = _obtener(snapshot, "threads")
            _panel_threads(stdscr, y, alto, pid_sel, datos)
        elif vista_activa == "senales":
            datos, _ = _obtener(snapshot, "senales")
            _panel_senales(stdscr, y, alto, pid_sel, datos)
        elif vista_activa == "scheduling":
            datos, _ = _obtener(snapshot, "scheduling")
            _panel_scheduling(stdscr, y, alto, pid_sel, datos)
        elif vista_activa == "sistema":
            datos, _ = _obtener(snapshot, "sistema")
            tops, _ = _obtener(snapshot, "tops")
            tops = snapshot.get("tops")
            _panel_sistema(stdscr, y, alto, datos, tops)

        # ---------------- barra inferior ----------------
        if mensaje and time.time() < mensaje_hasta:
            _texto(stdscr, alto - 1, 0, f" {mensaje} ".ljust(ancho - 1), curses.A_REVERSE)
        else:
            ayuda = (" 1-7/rmftspg vistas | ↑↓ navegar | Enter pin | / cmd | u usuario "
                     "| c orden | +/- intervalo | h ayuda | q salir")
            _texto(stdscr, alto - 1, 0, ayuda.ljust(ancho - 1), curses.A_DIM)

        stdscr.refresh()

        # ---------------- teclado ----------------
        try:
            tecla = _leer_tecla(stdscr)
        except KeyboardInterrupt:
            evento_salida.set()
            break

        if tecla == -1:
            continue

        # OJO: no usar 27 (ESC) para salir. Las flechas mandan secuencias
        # que EMPIEZAN con ESC (\x1b[B), y si curses llega a entregar ese
        # byte suelto, el monitor se cerraría al apretar una flecha.
        if tecla == ord("q"):
            evento_salida.set()
            break
        elif tecla in TECLAS_VISTA:
            vista_activa = TECLAS_VISTA[tecla]
            mostrar_ayuda = False
        elif tecla == curses.KEY_UP:
            seleccion = max(0, seleccion - 1)
            pin_pid = None
        elif tecla == curses.KEY_DOWN:
            seleccion = min(len(procesos) - 1, seleccion + 1) if procesos else 0
            pin_pid = None
        elif tecla == curses.KEY_PPAGE:
            seleccion = max(0, seleccion - alto_lista)
            pin_pid = None
        elif tecla == curses.KEY_NPAGE:
            seleccion = min(len(procesos) - 1, seleccion + alto_lista) if procesos else 0
            pin_pid = None
        elif tecla in (curses.KEY_ENTER, 10, 13):
            if pin_pid == pid_sel:
                pin_pid = None
                avisar("Pin liberado")
            elif pid_sel:
                pin_pid = pid_sel
                avisar(f"PID {pid_sel} fijado: la selección lo sigue aunque cambie el orden")
        elif tecla == ord("/"):
            filtro_cmd = _leer_texto(stdscr, "Filtrar por comando (vacío = limpiar):")
            seleccion = 0
        elif tecla == ord("u"):
            filtro_usr = _leer_texto(stdscr, "Filtrar por usuario (vacío = limpiar):")
            seleccion = 0
        elif tecla == ord("c"):
            orden = ORDENES[(ORDENES.index(orden) + 1) % len(ORDENES)]
            avisar(f"Orden: {orden.upper()}")
        elif tecla in (ord("+"), ord("=")):
            intervalos[vista_activa].value += 0.5
            avisar(f"Intervalo de {vista_activa}: {intervalos[vista_activa].value:.1f}s")
        elif tecla == ord("-"):
            minimo = minimos.get(vista_activa, 0.5)
            nuevo = max(minimo, intervalos[vista_activa].value - 0.5)
            intervalos[vista_activa].value = nuevo
            if nuevo == minimo:
                avisar(f"Intervalo de {vista_activa}: {nuevo:.1f}s (mínimo permitido)")
            else:
                avisar(f"Intervalo de {vista_activa}: {nuevo:.1f}s")
        elif tecla in (ord("h"), ord("?")):
            mostrar_ayuda = not mostrar_ayuda
        elif tecla == curses.KEY_RESIZE:
            stdscr.erase()
