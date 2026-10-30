"""Las tres tareas obligatorias.

Cada una es una corrutina ``(parametros, contexto) -> resultado``. Ninguna
se ocupa de timeouts ni de cancelación: eso lo resuelve el gestor desde
afuera. Lo único que se les pide es **no bloquear el event loop** y
**limpiar lo que abren** (con ``async with``) para que ser canceladas en
cualquier ``await`` no deje recursos colgados.
"""

import asyncio
import hashlib
import logging
import time
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import aiohttp

from .config import Config
from .cpu import sha256_iterado

log = logging.getLogger(__name__)


@dataclass
class Contexto:
    """Recursos compartidos que el gestor le presta a las tareas."""

    config: Config
    sesion: aiohttp.ClientSession
    ejecutor_cpu: Executor | None  # None cuando hash_modo == "bloqueante"


# --------------------------------------------------------------------------
# descargar  (I/O-bound)
# --------------------------------------------------------------------------

class _DemasiadoGrande(Exception):
    pass


async def _bajar(url: str, ctx: Contexto, timeout_url: float) -> dict[str, Any]:
    """Descarga una URL contando bytes sin guardarlos en memoria.

    Los errores de red se devuelven como datos (una URL caída no debe tirar
    abajo las otras 19). ``CancelledError`` no se captura: tiene que
    propagarse para que la cancelación llegue hasta el socket.
    """
    inicio = time.perf_counter()
    try:
        async with ctx.sesion.get(url, timeout=aiohttp.ClientTimeout(total=timeout_url)) as resp:
            total = 0
            async for trozo in resp.content.iter_chunked(64 * 1024):
                total += len(trozo)
                if total > ctx.config.max_bytes_descarga:
                    raise _DemasiadoGrande(f"supera {ctx.config.max_bytes_descarga} bytes")
            codigo = resp.status
        error = None
    except (aiohttp.ClientError, asyncio.TimeoutError, _DemasiadoGrande) as exc:
        codigo, total, error = None, 0, f"{type(exc).__name__}: {exc}"
    resultado = {
        "url": url,
        "codigo": codigo,
        "bytes": total,
        "segundos": round(time.perf_counter() - inicio, 4),
    }
    if error:
        resultado["error"] = error
    return resultado


async def descargar(parametros: dict[str, Any], ctx: Contexto) -> dict[str, Any]:
    # TaskGroup (3.11+): si esta tarea se cancela, las descargas hijas se
    # cancelan y se esperan antes de salir. No quedan corrutinas huérfanas.
    inicio = time.perf_counter()
    async with asyncio.TaskGroup() as grupo:
        hijas = [
            grupo.create_task(_bajar(url, ctx, parametros["timeout_url"]))
            for url in parametros["urls"]
        ]
    resultados = [h.result() for h in hijas]
    return {
        "descargas": resultados,
        "exitosas": sum(1 for r in resultados if r["codigo"] is not None),
        "segundos_totales": round(time.perf_counter() - inicio, 4),
    }


# --------------------------------------------------------------------------
# hashear  (CPU-bound)
# --------------------------------------------------------------------------

async def hashear(parametros: dict[str, Any], ctx: Contexto) -> dict[str, Any]:
    """SHA-256 iterado, fuera del event loop.

    El trabajo se parte en trozos de ``hash_chunk`` iteraciones y cada trozo
    viaja al executor por separado. Entre trozo y trozo hay un ``await``, o
    sea un punto donde la tarea puede cancelarse o vencer su timeout. Un
    solo ``run_in_executor`` de 2.000 millones de iteraciones no tendría
    ninguno: el pool seguiría quemando CPU hasta terminar aunque el cliente
    ya se hubiera ido.
    """
    inicio = time.perf_counter()
    digest = hashlib.sha256(parametros["texto"].encode()).digest()
    restantes = parametros["iteraciones"] - 1  # el sha256 inicial ya cuenta como una

    if ctx.config.hash_modo == "bloqueante":
        # Mal a propósito: bloquea el event loop. Existe solo para medirlo.
        digest = sha256_iterado(digest, restantes)
    else:
        loop = asyncio.get_running_loop()
        while restantes > 0:
            trozo = min(restantes, ctx.config.hash_chunk)
            digest = await loop.run_in_executor(ctx.ejecutor_cpu, sha256_iterado, digest, trozo)
            restantes -= trozo

    return {
        "hash": digest.hex(),
        "iteraciones": parametros["iteraciones"],
        "segundos": round(time.perf_counter() - inicio, 4),
    }


# --------------------------------------------------------------------------
# esperar  (I/O simulado)
# --------------------------------------------------------------------------

async def esperar(parametros: dict[str, Any], ctx: Contexto) -> dict[str, Any]:
    # asyncio.sleep cede el control al loop; time.sleep lo congelaría entero.
    await asyncio.sleep(parametros["segundos"])
    return {"dormido_segundos": parametros["segundos"]}


Handler = Callable[[dict[str, Any], Contexto], Awaitable[dict[str, Any]]]

HANDLERS: dict[str, Handler] = {
    "descargar": descargar,
    "hashear": hashear,
    "esperar": esperar,
}
