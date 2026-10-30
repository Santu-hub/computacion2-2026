"""Configuración de la plataforma, leída de variables de entorno."""

import os
from dataclasses import dataclass

MODOS_HASH = ("proceso", "hilo", "bloqueante")


def _entero(nombre: str, defecto: int) -> int:
    return int(os.environ.get(nombre, defecto))


@dataclass(frozen=True)
class Config:
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # Pool de workers (corrutinas) y tamaño máximo de la cola.
    workers: int = 10
    max_cola: int = 5000

    # Timeout por tarea (segundos) cuando el cliente no manda uno propio.
    timeout_defecto: float = 60.0

    # Tareas CPU-bound: dónde se ejecutan y en cuántas iteraciones por viaje.
    #   proceso    -> ProcessPoolExecutor (default, paralelismo real)
    #   hilo       -> ThreadPoolExecutor (para comparar: el GIL molesta)
    #   bloqueante -> directo en el event loop. SOLO para la medición 2.
    hash_modo: str = "proceso"
    cpu_workers: int = os.cpu_count() or 1
    hash_chunk: int = 200_000

    # Descargas.
    max_conexiones: int = 100
    max_bytes_descarga: int = 50 * 1024 * 1024

    # Apagado ordenado: segundos de gracia para las tareas en curso y carpeta
    # donde se vuelca estado_<timestamp>.json.
    tiempo_apagado: float = 20.0
    dir_estado: str = "."

    @classmethod
    def desde_entorno(cls) -> "Config":
        modo = os.environ.get("HASH_MODO", cls.hash_modo)
        if modo not in MODOS_HASH:
            raise ValueError(f"HASH_MODO debe ser uno de {MODOS_HASH}, no {modo!r}")
        return cls(
            host=os.environ.get("HOST", cls.host),
            port=_entero("PORT", cls.port),
            log_level=os.environ.get("LOG_LEVEL", cls.log_level),
            workers=_entero("WORKERS", cls.workers),
            max_cola=_entero("MAX_COLA", cls.max_cola),
            timeout_defecto=float(os.environ.get("TIMEOUT_DEFECTO", cls.timeout_defecto)),
            hash_modo=modo,
            cpu_workers=_entero("CPU_WORKERS", cls.cpu_workers),
            hash_chunk=_entero("HASH_CHUNK", cls.hash_chunk),
            max_conexiones=_entero("MAX_CONEXIONES", cls.max_conexiones),
            max_bytes_descarga=_entero("MAX_BYTES_DESCARGA", cls.max_bytes_descarga),
            tiempo_apagado=float(os.environ.get("TIEMPO_APAGADO", cls.tiempo_apagado)),
            dir_estado=os.environ.get("DIR_ESTADO", cls.dir_estado),
        )
