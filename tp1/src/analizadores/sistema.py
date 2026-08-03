import time
from procfs import leer_sistema

def analizador_sistema(snapshot, intervalo, evento_salida):
    """Proceso que recolecta el estado global de la máquina."""
    prev_idle, prev_total = 0, 0
    
    while not evento_salida.is_set():
        datos = leer_sistema()
        
        # Calcular porcentaje de CPU comparando con el ciclo anterior
        idle, total = datos.get('cpu_tiempos', (0, 0))
        diff_idle = idle - prev_idle
        diff_total = total - prev_total
        
        cpu_porcentaje = 0.0
        if diff_total > 0:
            cpu_porcentaje = (1.0 - (diff_idle / diff_total)) * 100.0
            
        prev_idle, prev_total = idle, total
        datos['cpu_porcentaje'] = cpu_porcentaje
        
        # Mandamos a la pizarra compartida
        snapshot['sistema'] = datos
        
        inicio = time.time()
        while time.time() - inicio < intervalo.value and not evento_salida.is_set():
            time.sleep(0.1)