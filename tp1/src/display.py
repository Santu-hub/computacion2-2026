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
        elif tecla == ord('+'):
            intervalos[vista_activa].value += 0.5
        elif tecla == ord('-'):
            intervalos[vista_activa].value = max(0.5, intervalos[vista_activa].value - 0.5)
        elif tecla == curses.KEY_RESIZE:
            # Si detecta que estiraste la terminal, fuerza un redibujado instantáneo
            pass