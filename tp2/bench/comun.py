"""Utilidades compartidas por los scripts de medición."""

import asyncio
import json
import os
import platform
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp

RAIZ = Path(__file__).resolve().parent.parent
RESULTADOS = RAIZ / "resultados"
PUERTO_APP = 8765
PUERTO_OBJETIVO = 9100


def entorno_maquina() -> dict:
    return {
        "python": platform.python_version(),
        "plataforma": platform.platform(),
        "nucleos": os.cpu_count(),
    }


def guardar(nombre: str, datos: dict) -> None:
    RESULTADOS.mkdir(exist_ok=True)
    datos = {"maquina": entorno_maquina(), **datos}
    (RESULTADOS / f"{nombre}.json").write_text(json.dumps(datos, indent=2, ensure_ascii=False))


def percentil(valores: list[float], p: float) -> float:
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(len(ordenados) * p / 100))]


def resumen_latencias(lat: list[float]) -> dict:
    """Latencias en milisegundos."""
    ms = [x * 1000 for x in lat]
    return {
        "n": len(ms),
        "p50_ms": round(statistics.median(ms), 2),
        "p95_ms": round(percentil(ms, 95), 2),
        "p99_ms": round(percentil(ms, 99), 2),
        "max_ms": round(max(ms), 2),
    }


async def _esperar_http(url: str, timeout: float = 30.0) -> None:
    limite = time.monotonic() + timeout
    async with aiohttp.ClientSession() as s:
        while time.monotonic() < limite:
            try:
                async with s.get(url) as r:
                    if r.status < 500:
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.2)
    raise TimeoutError(f"{url} no respondió en {timeout}s")


class ProcesoServidor:
    """Un subproceso (la app o el servidor de prueba) con ciclo de vida controlado."""

    def __init__(self, comando: list[str], entorno: dict[str, str], url_listo: str) -> None:
        self.comando, self.entorno, self.url_listo = comando, entorno, url_listo
        self.proc: subprocess.Popen | None = None

    async def __aenter__(self) -> "ProcesoServidor":
        self.proc = subprocess.Popen(
            self.comando,
            cwd=RAIZ,
            env={**os.environ, **self.entorno},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await _esperar_http(self.url_listo)
        return self

    async def __aexit__(self, *exc) -> None:
        self.proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.to_thread(self.proc.wait, 40)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def rss_mb(self) -> float | None:
        """Memoria residente del proceso (solo Linux)."""
        try:
            for linea in Path(f"/proc/{self.proc.pid}/status").read_text().splitlines():
                if linea.startswith("VmRSS"):
                    return round(int(linea.split()[1]) / 1024, 1)
        except OSError:
            return None
        return None


def app_servidor(**env) -> ProcesoServidor:
    """Levanta la app real con la configuración indicada (variables de entorno)."""
    carpeta = tempfile.mkdtemp(prefix="tp2_estado_")
    entorno = {"PORT": str(PUERTO_APP), "DIR_ESTADO": carpeta, "LOG_LEVEL": "warning"}
    entorno.update({k.upper(): str(v) for k, v in env.items()})
    return ProcesoServidor(
        [sys.executable, "-m", "app.main"], entorno, f"http://127.0.0.1:{PUERTO_APP}/estadisticas"
    )


def objetivo_servidor() -> ProcesoServidor:
    return ProcesoServidor(
        [sys.executable, "-m", "bench.servidor_prueba", "--port", str(PUERTO_OBJETIVO)],
        {},
        f"http://127.0.0.1:{PUERTO_OBJETIVO}/lento?ms=0",
    )


BASE = f"http://127.0.0.1:{PUERTO_APP}"
URL_LENTA = f"http://127.0.0.1:{PUERTO_OBJETIVO}/lento"


@asynccontextmanager
async def cliente(limite: int = 200, timeout: float = 120.0):
    conector = aiohttp.TCPConnector(limit=limite)
    async with aiohttp.ClientSession(
        connector=conector, timeout=aiohttp.ClientTimeout(total=timeout)
    ) as s:
        yield s


async def crear(s: aiohttp.ClientSession, tipo: str, parametros: dict, timeout: float | None = None):
    cuerpo = {"tipo": tipo, "parametros": parametros}
    if timeout is not None:
        cuerpo["timeout"] = timeout
    async with s.post(f"{BASE}/tareas", json=cuerpo) as r:
        return r.status, await r.json()


async def estadisticas(s: aiohttp.ClientSession) -> dict:
    async with s.get(f"{BASE}/estadisticas") as r:
        return await r.json()


async def esperar_terminadas(s: aiohttp.ClientSession, total: int, timeout: float = 600.0) -> float:
    """Espera hasta que haya `total` tareas en estado terminal. Devuelve el instante (monotonic)."""
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        est = (await estadisticas(s))["por_estado"]
        if est["completada"] + est["fallida"] + est["cancelada"] >= total:
            return time.monotonic()
        await asyncio.sleep(0.03)
    raise TimeoutError("las tareas no terminaron")


class Sonda:
    """Mide la latencia de un GET repetido mientras el sistema está bajo carga."""

    def __init__(self, s: aiohttp.ClientSession, ruta: str, intervalo: float = 0.02) -> None:
        self.s, self.url, self.intervalo = s, f"{BASE}{ruta}", intervalo
        self.latencias: list[float] = []
        self._activa = False
        self._tarea: asyncio.Task | None = None

    async def _bucle(self) -> None:
        while self._activa:
            t0 = time.perf_counter()
            async with self.s.get(self.url) as r:
                await r.read()
            self.latencias.append(time.perf_counter() - t0)
            await asyncio.sleep(self.intervalo)

    def iniciar(self) -> None:
        self._activa = True
        self._tarea = asyncio.create_task(self._bucle())

    async def detener(self) -> list[float]:
        self._activa = False
        await self._tarea
        return self.latencias


def ejecutar(coro) -> None:
    asyncio.run(coro)
