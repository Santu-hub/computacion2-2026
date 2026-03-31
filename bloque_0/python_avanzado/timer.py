import time
from typing import Optional, Type
from types import TracebackType

class Timer:
    """
    Context manager basado en clase para medir tiempo.
    Permite acceso a la propiedad 'elapsed' durante y después del bloque.
    """
    def __init__(self, nombre: Optional[str] = None):
        self.nombre = nombre
        self._inicio: float = 0.0
        self._fin: Optional[float] = None

    @property
    def elapsed(self) -> float:
        """Calcula el tiempo transcurrido desde el inicio."""
        if self._fin is not None:
            return self._fin - self._inicio
        return time.perf_counter() - self._inicio

    def __enter__(self) -> 'Timer':
        self._inicio = time.perf_counter()
        self._fin = None
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], *args) -> bool:
        self._fin = time.perf_counter()
        if self.nombre:
            print(f"[{self.nombre}] {self.elapsed:.3f}s")
        return False

if __name__ == "__main__":
    # Ejemplo de uso con acceso a elapsed
    with Timer("Proceso Largo") as t:
        time.sleep(0.2)
        print(f"Intermedio: {t.elapsed:.3f}s")
        time.sleep(0.3)
    
    print(f"Final: {t.elapsed:.3f}s")