import curses
import time

def tui_loop(stdscr, snapshot, intervalos, evento_salida):
    try:
        curses.curs_set(0) # Ocultar cursor
    except curses.error:
        pass
        
    stdscr.nodelay(True) 
    stdscr.timeout(500) # Refrescar pantalla cada 500ms
    
    vista_activa = 'resumen'

    while not evento_salida.is_set():
        altura, anchura = stdscr.getmaxyx()
        stdscr.erase() # erase() evita parpadeos al achicar/agrandar
        
        # --- 1. PRIMERO DIBUJAR ---
        try:
            titulo = f" MINISH MONITOR | Vista: {vista_activa.upper()} | Refresco: {intervalos[vista_activa].value}s | Presione 'q' para salir "
            stdscr.addstr(0, 0, titulo[:anchura-1], curses.A_REVERSE)
            
            if vista_activa == 'resumen':
                stdscr.addstr(2, 0, f"{'PID':<8} {'ESTADO':<8} {'THREADS':<8} {'COMANDO'}")
                stdscr.addstr(3, 0, "-" * (anchura-1))
                
                datos = snapshot.get('resumen', [])
                fila = 4
                if altura > 6:
                    for proc in datos[:altura-6]: 
                        linea = f"{proc['pid']:<8} {proc['estado']:<8} {proc['threads']:<8} {proc['comando']}"
                        stdscr.addstr(fila, 0, linea[:anchura-1])
                        fila += 1
                        
            elif vista_activa == 'memoria':
                stdscr.addstr(2, 0, f"{'PID':<8} {'NOMBRE':<16} {'VIRTUAL(KB)':<15} {'RSS(KB)':<15} {'SWAP(KB)'}")
                stdscr.addstr(3, 0, "-" * (anchura-1))
                
                datos = snapshot.get('memoria', [])
                fila = 4
                if altura > 6:
                    for proc in datos[:altura-6]:
                        linea = f"{proc['pid']:<8} {proc['nombre'][:15]:<16} {proc['vmsize']:<15} {proc['vmrss']:<15} {proc['vmswap']}"
                        stdscr.addstr(fila, 0, linea[:anchura-1])
                        fila += 1
                        
            elif vista_activa == 'fds':
                stdscr.addstr(2, 0, f"{'PID':<8} {'FD':<6} {'TIPO':<15} {'DESTINO'}")
                stdscr.addstr(3, 0, "-" * (anchura-1))
                
                datos = snapshot.get('fds', [])
                fila = 4
                
                if altura > 6:
                    for proc in datos:
                        for fd_info in proc['fds']:
                            if fila >= altura - 1:
                                break # Frenamos si tocamos el borde de abajo
                                
                            linea = f"{proc['pid']:<8} {fd_info['fd']:<6} {fd_info['tipo']:<15} {fd_info['destino']}"
                            stdscr.addstr(fila, 0, linea[:anchura-1])
                            fila += 1
                        if fila >= altura - 1:
                            break
                        
            elif vista_activa == 'threads':
                stdscr.addstr(2, 0, f"{'PID':<8} {'TID(HILO)':<12} {'ESTADO':<8} {'TIEMPO CPU':<12} {'COMANDO'}")
                stdscr.addstr(3, 0, "-" * (anchura-1))
                
                datos = snapshot.get('threads', [])
                fila = 4
                
                if altura > 6:
                    for proc in datos:
                        for hilo in proc['hilos']:
                            if fila >= altura - 1:
                                break
                            
                            linea = f"{proc['pid']:<8} {hilo['tid']:<12} {hilo['estado']:<8} {hilo['tiempo_cpu']:<12} {hilo['comando']}"
                            stdscr.addstr(fila, 0, linea[:anchura-1])
                            fila += 1
                        if fila >= altura - 1:
                            break

            elif vista_activa == 'senales':
                stdscr.addstr(2, 0, f"{'PID':<8} {'PENDIENTES':<18} {'BLOQUEADAS':<18} {'IGNORADAS':<18} {'CAPTURADAS'}")
                stdscr.addstr(3, 0, "-" * (anchura-1))
                
                datos = snapshot.get('senales', [])
                fila = 4
                
                if altura > 6:
                    for proc in datos[:altura-6]:
                        # Acá está la magia del [:17] para truncar los textos largos
                        linea = f"{proc['pid']:<8} {proc['pendientes'][:17]:<18} {proc['bloqueadas'][:17]:<18} {proc['ignoradas'][:17]:<18} {proc['capturadas'][:17]}"
                        stdscr.addstr(fila, 0, linea[:anchura-1])
                        fila += 1

            elif vista_activa == 'scheduling':
                stdscr.addstr(2, 0, f"{'PID':<8} {'PRIORIDAD':<12} {'NICE':<8} {'POLITICA':<12} {'AFINIDAD':<12} {'CTX SWITCHES'}")
                stdscr.addstr(3, 0, "-" * (anchura-1))
                
                datos = snapshot.get('scheduling', [])
                fila = 4
                
                if altura > 6:
                    for proc in datos[:altura-6]:
                        linea = f"{proc['pid']:<8} {proc['prioridad']:<12} {proc['nice']:<8} {proc['politica'][:11]:<12} {proc['afinidad']:<12} {proc['ctx_switches']}"
                        stdscr.addstr(fila, 0, linea[:anchura-1])
                        fila += 1

            elif vista_activa == 'sistema':
                datos = snapshot.get('sistema', {})
                
                if datos:
                    cpu_pct = datos.get('cpu_porcentaje', 0)
                    ram_usd = datos.get('ram_usada', 0) / 1024 # Lo pasamos a MB
                    ram_tot = datos.get('ram_total', 0) / 1024
                    ram_pct = datos.get('ram_porcentaje', 0)
                    load = datos.get('load', ['0.00', '0.00', '0.00'])
                    
                    # Dibujamos barras de progreso
                    stdscr.addstr(3, 2, f"=== ESTADO GLOBAL DEL SISTEMA ===", curses.A_BOLD)
                    
                    stdscr.addstr(5, 2, f"CPU Uso:      [{'#' * int(cpu_pct/2):<50}] {cpu_pct:.1f}%")
                    
                    stdscr.addstr(7, 2, f"RAM Uso:      [{'#' * int(ram_pct/2):<50}] {ram_pct:.1f}% ({ram_usd:.0f} MB / {ram_tot:.0f} MB)")
                    
                    stdscr.addstr(9, 2, f"Load Average: {load[0]} (1m)  |  {load[1]} (5m)  |  {load[2]} (15m)")
                    stdscr.addstr(11, 2, f"Procesos:     {datos.get('procs_running', '0')} ejecutándose, {datos.get('procs_blocked', '0')} bloqueados esperando E/O")
                else:
                    stdscr.addstr(3, 2, "Recolectando datos del sistema... por favor espere.")

        except curses.error:
            pass # Ignoramos errores temporales al redimensionar muy brusco
            
        stdscr.refresh() # Mostramos la pantalla al usuario

        # --- 2. DESPUÉS ESPERAR TECLADO ---
        # Ahora el programa se pausa acá por 500ms, pero con la pantalla ya dibujada
        tecla = stdscr.getch()
        
        if tecla == ord('q'):
            evento_salida.set()
            break
        elif tecla == ord('1') or tecla == ord('r'):
            vista_activa = 'resumen'
        elif tecla == ord('2') or tecla == ord('m'):
            vista_activa = 'memoria'
        elif tecla == ord('3') or tecla == ord('f'):
            vista_activa = 'fds'
        elif tecla == ord('4') or tecla == ord('t'):
            vista_activa = 'threads'
        elif tecla == ord('5') or tecla == ord('s'):
            vista_activa = 'senales'
        elif tecla == ord('6') or tecla == ord('p'):
            vista_activa = 'scheduling'
        elif tecla == ord('7') or tecla == ord('g'):
            vista_activa = 'sistema'
        elif tecla == ord('+'):
            intervalos[vista_activa].value += 0.5
        elif tecla == ord('-'):
            intervalos[vista_activa].value = max(0.5, intervalos[vista_activa].value - 0.5)
        elif tecla == curses.KEY_RESIZE:
            # Si detecta que estiraste la terminal, fuerza un redibujado instantáneo
            pass