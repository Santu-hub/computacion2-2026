"""Gestor de tareas: cola, pool de workers, cancelación, timeouts y apagado.

Todo el estado mutable (``tareas``, contadores, la cola) lo toca únicamente
código que corre en el hilo del event loop, y los cambios de estado no
tienen ``await`` en el medio. Como el loop solo cambia de corrutina en un
``await``, cada transición es atómica sin necesidad de un ``Lock``.
"""

import asyncio
import logging
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from functools import partial
from itertools import islice
from multiprocessing import get_context
from typing import Any

import aiohttp

from . import persistencia
from .config import Config
from .cpu import sha256_iterado
from .modelos import TERMINALES, Estado, Tarea, ahora
from .tareas import HANDLERS, Contexto

log = logging.getLogger(__name__)


class NoAcepta(Exception):
    """El servidor se está apagando."""


class ColaLlena(Exception):
    """Se alcanzó ``max_cola`` tareas pendientes."""


class YaFinalizada(Exception):
    """Se intentó cancelar una tarea que ya terminó."""


class TiempoAgotado(Exception):
    pass


class Gestor:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.tareas: dict[str, Tarea] = {}
        self.cola: asyncio.Queue[str] = asyncio.Queue(maxsize=config.max_cola)
        self.aceptando = False
        self.inicio = time.monotonic()

        self._workers: set[asyncio.Task] = set()
        self._sesion: aiohttp.ClientSession | None = None
        self._ejecutor: Executor | None = None
        self._ctx: Contexto | None = None

        # Contadores incrementales: /estadisticas no recorre todas las tareas.
        self._por_estado: Counter[Estado] = Counter()
        self._suma_espera = 0.0
        self._n_iniciadas = 0
        self._ejecucion: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])  # tipo -> [n, suma]

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def iniciar(self) -> None:
        cfg = self.config
        conector = aiohttp.TCPConnector(limit=cfg.max_conexiones)
        self._sesion = aiohttp.ClientSession(connector=conector)

        if cfg.hash_modo == "proceso":
            # spawn en todos los sistemas: mismo comportamiento en Linux y
            # macOS, y evita hacer fork de un proceso que ya tiene un loop.
            self._ejecutor = ProcessPoolExecutor(
                max_workers=cfg.cpu_workers, mp_context=get_context("spawn")
            )
            await self._precalentar()
        elif cfg.hash_modo == "hilo":
            self._ejecutor = ThreadPoolExecutor(max_workers=cfg.cpu_workers)
        self._ctx = Contexto(config=cfg, sesion=self._sesion, ejecutor_cpu=self._ejecutor)

        self.aceptando = True
        for n in range(cfg.workers):
            self._lanzar_worker(n)
        log.info(
            "Gestor listo: %d workers, cola máx %d, hash_modo=%s (%d en el pool CPU)",
            cfg.workers, cfg.max_cola, cfg.hash_modo, cfg.cpu_workers,
        )

    async def _precalentar(self) -> None:
        """Arranca los procesos del pool ya, para que la primera tarea no pague el spawn."""
        loop = asyncio.get_running_loop()
        await asyncio.gather(*(
            loop.run_in_executor(self._ejecutor, sha256_iterado, b"", 1)
            for _ in range(self.config.cpu_workers)
        ))

    def dejar_de_aceptar(self) -> None:
        if self.aceptando:
            self.aceptando = False
            log.info("Se dejan de aceptar tareas nuevas")

    async def apagar(self) -> None:
        """Apagado ordenado: gracia a las tareas en curso, cancelar el resto, volcar el estado."""
        self.dejar_de_aceptar()
        en_curso = {t.asyncio_task for t in self.tareas.values() if t.estado == Estado.EJECUTANDO}
        en_curso.discard(None)
        if en_curso:
            log.info("Esperando hasta %.0fs a %d tareas en curso", self.config.tiempo_apagado, len(en_curso))
            _, sobran = await asyncio.wait(en_curso, timeout=self.config.tiempo_apagado)
            for t in self.tareas.values():
                if t.asyncio_task in sobran:
                    t.motivo_cancelacion = "interrumpida por apagado del servidor"
            for tarea in sobran:
                tarea.cancel()
            if sobran:
                log.warning("%d tareas no terminaron a tiempo y se cancelaron", len(sobran))
                await asyncio.wait(sobran)

        # Los workers ociosos están bloqueados en cola.get(): se cancelan.
        workers = list(self._workers)
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

        if self._ejecutor is not None:
            await asyncio.to_thread(self._ejecutor.shutdown, wait=True, cancel_futures=True)
        if self._sesion is not None:
            await self._sesion.close()

        destino = await asyncio.to_thread(
            persistencia.escribir_volcado, self.config.dir_estado, self.volcado()
        )
        log.info("Estado volcado en %s", destino)

    # ------------------------------------------------------------------
    # API pública (la usan los endpoints)
    # ------------------------------------------------------------------

    def encolar(self, tipo: str, parametros: dict[str, Any], timeout: float | None) -> Tarea:
        if not self.aceptando:
            raise NoAcepta("el servidor se está apagando")
        tarea = Tarea(
            id=self._nuevo_id(),
            tipo=tipo,
            parametros=parametros,
            timeout=timeout if timeout is not None else self.config.timeout_defecto,
        )
        try:
            self.cola.put_nowait(tarea.id)
        except asyncio.QueueFull:
            raise ColaLlena(f"hay {self.config.max_cola} tareas en cola") from None
        self.tareas[tarea.id] = tarea
        self._por_estado[Estado.PENDIENTE] += 1
        return tarea

    def obtener(self, id_: str) -> Tarea:
        return self.tareas[id_]  # KeyError -> 404 en la API

    def listar(self, estado: Estado | None, limite: int, offset: int) -> list[Tarea]:
        todas = (t for t in self.tareas.values() if estado is None or t.estado == estado)
        return list(islice(todas, offset, offset + limite))

    async def cancelar(self, id_: str) -> Tarea:
        tarea = self.tareas[id_]
        if tarea.estado in TERMINALES:
            raise YaFinalizada(f"la tarea ya está {tarea.estado.value}")

        if tarea.estado == Estado.PENDIENTE:
            # Sigue en la cola: no se la saca (asyncio.Queue no lo permite),
            # se la marca, y el worker que la desencole la va a descartar.
            self._cambiar(tarea, Estado.CANCELADA)
            tarea.error = "cancelada por el cliente antes de ejecutarse"
            self._cerrar(tarea)
            return tarea

        # EJECUTANDO: se cancela la tarea de asyncio y se espera a que el
        # callback _finalizar registre el resultado, así la respuesta ya
        # refleja el estado definitivo.
        tarea.motivo_cancelacion = "cancelada por el cliente durante la ejecución"
        interna = tarea.asyncio_task
        interna.cancel()
        await asyncio.wait({interna}, timeout=10)
        if tarea.estado != Estado.CANCELADA:
            raise YaFinalizada(f"la tarea terminó antes de poder cancelarse ({tarea.estado.value})")
        return tarea

    def estadisticas(self) -> dict[str, Any]:
        ocupados = self._por_estado[Estado.EJECUTANDO]
        return {
            "total_tareas": len(self.tareas),
            "por_estado": {e.value: self._por_estado[e] for e in Estado},
            "workers": {
                "total": self.config.workers,
                "ocupados": ocupados,
                "libres": self.config.workers - ocupados,
            },
            "cola": {"pendientes": self._por_estado[Estado.PENDIENTE], "capacidad": self.config.max_cola},
            "tiempos_promedio_segundos": {
                "espera_en_cola": self._promedio(self._suma_espera, self._n_iniciadas),
                "ejecucion_completadas": {
                    tipo: self._promedio(suma, n) for tipo, (n, suma) in self._ejecucion.items()
                },
            },
            "aceptando_tareas": self.aceptando,
            "uptime_segundos": round(time.monotonic() - self.inicio, 1),
        }

    def volcado(self) -> dict[str, Any]:
        return {
            "generado": ahora().isoformat(),
            "estadisticas": self.estadisticas(),
            "tareas": [t.a_dict() for t in self.tareas.values()],
        }

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------

    def _lanzar_worker(self, n: int) -> None:
        w = asyncio.create_task(self._worker(), name=f"worker-{n}")
        self._workers.add(w)
        w.add_done_callback(partial(self._worker_termino, n))

    def _worker_termino(self, n: int, w: asyncio.Task) -> None:
        """Supervisor: si un worker muere por una excepción inesperada, se lo reemplaza."""
        self._workers.discard(w)
        if w.cancelled():
            return
        exc = w.exception()
        if exc is not None and self.aceptando:
            log.error("worker-%d murió por una excepción no capturada; se lo reemplaza", n, exc_info=exc)
            self._lanzar_worker(n)

    async def _worker(self) -> None:
        while True:
            id_ = await self.cola.get()
            tarea = None
            try:
                if not self.aceptando:
                    return  # apagado: lo que quede en la cola queda 'pendiente'
                tarea = self.tareas[id_]
                if tarea.estado != Estado.PENDIENTE:
                    continue  # cancelada mientras esperaba en la cola
                await self._correr(tarea)
            except Exception:
                # Red de seguridad: un bug en el bookkeeping no puede matar al worker.
                log.exception("Error inesperado en el worker al procesar %s", id_)
                self._abortar(tarea)
            except BaseException:
                self._abortar(tarea)  # el worker muere; el supervisor lo reemplaza
                raise
            finally:
                self.cola.task_done()

    def _abortar(self, tarea: Tarea | None) -> None:
        """Evita que una tarea ya desencolada quede 'pendiente' para siempre.

        Si ya estaba ejecutando no hace falta nada: su estado final lo fija
        el done-callback, que no depende de que el worker siga vivo.
        """
        if tarea is not None and tarea.estado == Estado.PENDIENTE:
            self._cambiar(tarea, Estado.FALLIDA)
            tarea.error = "el worker falló antes de ejecutarla"
            self._cerrar(tarea)

    async def _correr(self, tarea: Tarea) -> None:
        interna = asyncio.create_task(self._ejecutar(tarea), name=f"tarea-{tarea.id}")
        tarea.asyncio_task = interna
        self._cambiar(tarea, Estado.EJECUTANDO)
        tarea.iniciada = ahora()
        tarea.m_inicio = time.monotonic()
        self._suma_espera += tarea.m_inicio - tarea.m_creada
        self._n_iniciadas += 1
        # Se registra ANTES de esperar: corre apenas termina `interna`, aunque
        # termine cancelada sin haber arrancado, y antes de que despierte
        # cualquiera que la esté esperando (incluido el DELETE).
        interna.add_done_callback(partial(self._finalizar, tarea))
        try:
            # wait() no relanza la excepción de `interna`: _finalizar se ocupa.
            await asyncio.wait({interna})
        except asyncio.CancelledError:
            # Cancelaron al worker (apagado): no se deja la tarea huérfana.
            if tarea.motivo_cancelacion is None:
                tarea.motivo_cancelacion = "interrumpida por apagado del servidor"
            interna.cancel()
            await asyncio.wait({interna})
            raise

    async def _ejecutar(self, tarea: Tarea) -> Any:
        try:
            async with asyncio.timeout(tarea.timeout) as limite:
                return await HANDLERS[tarea.tipo](tarea.parametros, self._ctx)
        except TimeoutError:
            # Un TimeoutError de adentro (p. ej. de aiohttp) no es nuestro timeout.
            if limite.expired():
                raise TiempoAgotado(f"timeout: superó los {tarea.timeout:g}s") from None
            raise

    def _finalizar(self, tarea: Tarea, interna: asyncio.Task) -> None:
        """Done-callback de la tarea interna: fija el estado final. Es síncrono."""
        tarea.asyncio_task = None
        if interna.cancelled():
            self._cambiar(tarea, Estado.CANCELADA)
            tarea.error = tarea.motivo_cancelacion or "cancelada"
        elif (exc := interna.exception()) is not None:
            self._cambiar(tarea, Estado.FALLIDA)
            if isinstance(exc, TiempoAgotado):
                tarea.error = str(exc)
            else:
                tarea.error = f"{type(exc).__name__}: {exc}"
                log.error("La tarea %s (%s) falló", tarea.id, tarea.tipo, exc_info=exc)
        else:
            self._cambiar(tarea, Estado.COMPLETADA)
            tarea.resultado = interna.result()
        self._cerrar(tarea)
        if tarea.estado == Estado.COMPLETADA and tarea.duracion_segundos is not None:
            acumulado = self._ejecucion[tarea.tipo]
            acumulado[0] += 1
            acumulado[1] += tarea.duracion_segundos

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def _cambiar(self, tarea: Tarea, nuevo: Estado) -> None:
        self._por_estado[tarea.estado] -= 1
        tarea.estado = nuevo
        self._por_estado[nuevo] += 1

    @staticmethod
    def _cerrar(tarea: Tarea) -> None:
        tarea.terminada = ahora()
        tarea.m_fin = time.monotonic()

    def _nuevo_id(self) -> str:
        while True:
            id_ = uuid.uuid4().hex[:12]
            if id_ not in self.tareas:
                return id_

    @staticmethod
    def _promedio(suma: float, n: int) -> float | None:
        return round(suma / n, 4) if n else None
