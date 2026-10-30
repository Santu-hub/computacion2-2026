"""Punto de entrada: ``python -m app.main``."""

import uvicorn

from .api import crear_app
from .config import Config

app = crear_app()

# Esta guarda NO es opcional. Con ``spawn`` (macOS, Windows, y el pool de
# procesos de este proyecto en todos los sistemas) cada proceso hijo vuelve
# a importar este módulo; sin la guarda, cada hijo intentaría levantar su
# propio servidor y crear su propio pool.
if __name__ == "__main__":
    config = Config.desde_entorno()
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=config.log_level,
        timeout_graceful_shutdown=5,
    )
