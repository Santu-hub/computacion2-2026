"""API HTTP (FastAPI) y ciclo de vida de la aplicación."""

import asyncio
import logging
import signal
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from . import persistencia
from .config import Config
from .gestor import ColaLlena, Gestor, NoAcepta, YaFinalizada
from .modelos import Estado, Solicitud, TareaOut

log = logging.getLogger(__name__)


def _encadenar_senales(gestor: Gestor) -> None:
    """Corta la admisión de tareas en el instante en que llega SIGTERM/SIGINT.

    uvicorn ya captura estas señales para apagarse; acá se envuelve su
    handler. El de uvicorn se llama igual, pero antes se baja el flag
    ``aceptando``. Sin esto habría una ventana entre la señal y el inicio
    del shutdown del lifespan en la que un POST por una conexión keep-alive
    todavía sería aceptado.

    Se usa ``signal.signal`` y no ``loop.add_signal_handler`` porque este
    último pisaría el handler de uvicorn y porque ``signal.signal`` también
    anda en Windows.
    """
    for sig in (signal.SIGINT, signal.SIGTERM):
        previo = signal.getsignal(sig)

        def handler(signum, frame, previo=previo):
            log.info("Señal %s recibida: apagado ordenado", signal.Signals(signum).name)
            gestor.dejar_de_aceptar()
            if callable(previo):
                previo(signum, frame)

        try:
            signal.signal(sig, handler)
        except ValueError:  # fuera del hilo principal (p. ej. en tests)
            log.debug("No se pudo instalar el handler de %s", sig)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = Config.desde_entorno()
    logging.basicConfig(
        level=config.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    await asyncio.to_thread(persistencia.reportar_volcado_previo, config.dir_estado)

    gestor = Gestor(config)
    app.state.gestor = gestor
    await gestor.iniciar()
    _encadenar_senales(gestor)
    try:
        yield
    finally:
        await gestor.apagar()


def obtener_gestor(request: Request) -> Gestor:
    return request.app.state.gestor


GestorDep = Annotated[Gestor, Depends(obtener_gestor)]


def crear_app() -> FastAPI:
    app = FastAPI(
        title="Plataforma de Tareas Distribuidas",
        description="TP2 — Computación II. Cola de tareas asíncrona con pool de workers.",
        lifespan=lifespan,
    )

    @app.post("/tareas", response_model=TareaOut, status_code=202)
    async def crear_tarea(solicitud: Solicitud, gestor: GestorDep):
        """Encola una tarea y responde de inmediato (202); no espera a que termine."""
        try:
            tarea = gestor.encolar(
                solicitud.tipo,
                solicitud.parametros.model_dump(mode="json"),
                solicitud.timeout,
            )
        except (NoAcepta, ColaLlena) as exc:
            raise HTTPException(503, str(exc), headers={"Retry-After": "5"})
        return tarea

    @app.get("/tareas", response_model=list[TareaOut])
    async def listar_tareas(
        gestor: GestorDep,
        estado: Estado | None = None,
        limite: Annotated[int, Query(ge=1, le=1000)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        """Lista tareas en orden de creación, con filtro opcional por estado."""
        return gestor.listar(estado, limite, offset)

    @app.get("/tareas/{id}", response_model=TareaOut)
    async def ver_tarea(id: str, gestor: GestorDep):
        try:
            return gestor.obtener(id)
        except KeyError:
            raise HTTPException(404, f"no existe la tarea {id}") from None

    @app.delete("/tareas/{id}", response_model=TareaOut)
    async def cancelar_tarea(id: str, gestor: GestorDep):
        """Cancela una tarea pendiente o en ejecución."""
        try:
            return await gestor.cancelar(id)
        except KeyError:
            raise HTTPException(404, f"no existe la tarea {id}") from None
        except YaFinalizada as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/estadisticas")
    async def estadisticas(gestor: GestorDep):
        return gestor.estadisticas()

    return app
