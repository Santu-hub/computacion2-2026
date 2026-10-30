"""Medición 1: 20 descargas secuenciales contra 20 concurrentes.

Usa un servidor local con latencia fija (500 ms por request), así el resultado
no depende del estado de internet. Con --internet repite la comparación a nivel
de librería contra sitios reales.

    python -m bench.medicion_1_io [--repeticiones 3] [--ms 500] [--internet]
"""

import argparse
import asyncio
import statistics
import time

import aiohttp

from .comun import (
    URL_LENTA, app_servidor, cliente, crear, ejecutar, esperar_terminadas,
    guardar, objetivo_servidor,
)

N = 20


TIMEOUT = aiohttp.ClientTimeout(total=10)


async def _bajar(s: aiohttp.ClientSession, u: str) -> bool:
    """True si la descarga anduvo. Un sitio caído no debe romper la medición."""
    try:
        async with s.get(u) as r:
            await r.read()
        return True
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return False


async def con_for_await(urls: list[str]) -> float:
    """Secuencial a nivel de código: un `for` con `await` adentro."""
    async with aiohttp.ClientSession(timeout=TIMEOUT) as s:
        t0 = time.perf_counter()
        for u in urls:
            await _bajar(s, u)
        return time.perf_counter() - t0


async def con_gather(urls: list[str]) -> float:
    async with aiohttp.ClientSession(timeout=TIMEOUT) as s:
        t0 = time.perf_counter()
        await asyncio.gather(*(_bajar(s, u) for u in urls))
        return time.perf_counter() - t0


async def via_api(workers: int, tareas: int, urls_por_tarea: int, ms: int) -> float:
    """Tiempo desde el primer POST hasta que la última tarea termina."""
    async with app_servidor(workers=workers):
        async with cliente() as s:
            t0 = time.monotonic()
            for i in range(tareas):
                urls = [f"{URL_LENTA}?ms={ms}&bytes=20000&i={i}_{j}" for j in range(urls_por_tarea)]
                estado, _ = await crear(s, "descargar", {"urls": urls})
                assert estado == 202, estado
            t1 = await esperar_terminadas(s, tareas)
            return t1 - t0


def mediana(valores: list[float]) -> float:
    return round(statistics.median(valores), 3)


async def principal(repeticiones: int, ms: int, internet: bool) -> None:
    resultado: dict = {"descargas": N, "latencia_servidor_ms": ms, "repeticiones": repeticiones}

    async with objetivo_servidor():
        urls = [f"{URL_LENTA}?ms={ms}&bytes=20000&i={i}" for i in range(N)]

        print(f"\n== A nivel de librería (aiohttp directo, {N} URLs de {ms} ms) ==")
        seq = [await con_for_await(urls) for _ in range(repeticiones)]
        con = [await con_gather(urls) for _ in range(repeticiones)]
        print(f"  for + await : {seq}  mediana {mediana(seq)} s")
        print(f"  gather      : {con}  mediana {mediana(con)} s")
        print(f"  speedup     : {mediana(seq) / mediana(con):.1f}x")
        resultado["libreria"] = {"for_await_s": seq, "gather_s": con,
                                 "speedup": round(mediana(seq) / mediana(con), 1)}

        print("\n== A través de la API (la plataforma completa) ==")
        escenarios = {
            "A) secuencial: WORKERS=1, 20 tareas de 1 URL": (1, N, 1),
            "B) concurrente en la tarea: 1 tarea con 20 URLs": (10, 1, N),
            "C) concurrente entre tareas: WORKERS=20, 20 tareas de 1 URL": (20, N, 1),
        }
        api: dict = {}
        for nombre, (workers, tareas, urls_por_tarea) in escenarios.items():
            tiempos = [await via_api(workers, tareas, urls_por_tarea, ms) for _ in range(repeticiones)]
            api[nombre] = {"tiempos_s": [round(t, 3) for t in tiempos], "mediana_s": mediana(tiempos)}
            print(f"  {nombre}\n      {api[nombre]['tiempos_s']}  mediana {api[nombre]['mediana_s']} s")
        base = api["A) secuencial: WORKERS=1, 20 tareas de 1 URL"]["mediana_s"]
        for nombre, datos in api.items():
            datos["speedup_vs_A"] = round(base / datos["mediana_s"], 1)
        print("  speedup B vs A: %.1fx   C vs A: %.1fx" % tuple(
            api[k]["speedup_vs_A"] for k in list(api)[1:]))
        resultado["api"] = api

    if internet:
        sitios = ["https://example.com", "https://www.python.org", "https://www.wikipedia.org",
                  "https://github.com", "https://www.mozilla.org", "https://www.debian.org",
                  "https://www.kernel.org", "https://www.ietf.org", "https://httpbin.org/html",
                  "https://www.rfc-editor.org"]
        urls = [f"{u}{'&' if '?' in u else '?'}n={i}" for i, u in enumerate(sitios * 2)]
        print(f"\n== Internet real ({N} URLs de {len(sitios)} sitios) ==")
        seq = [await con_for_await(urls) for _ in range(repeticiones)]
        con = [await con_gather(urls) for _ in range(repeticiones)]
        print(f"  for + await : {[round(x, 2) for x in seq]}  mediana {mediana(seq)} s")
        print(f"  gather      : {[round(x, 2) for x in con]}  mediana {mediana(con)} s")
        print(f"  speedup     : {mediana(seq) / mediana(con):.1f}x")
        resultado["internet"] = {"for_await_s": seq, "gather_s": con,
                                 "speedup": round(mediana(seq) / mediana(con), 1)}

    guardar("medicion_1", resultado)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeticiones", type=int, default=3)
    ap.add_argument("--ms", type=int, default=500)
    ap.add_argument("--internet", action="store_true")
    a = ap.parse_args()
    ejecutar(principal(a.repeticiones, a.ms, a.internet))
