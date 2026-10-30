"""Medición 2: cuánto tarda en responder GET /tareas mientras hay un hashear pesado.

Corre la misma tarea contra tres configuraciones de la app (variable HASH_MODO):

    bloqueante : el hash corre DENTRO del event loop (sin run_in_executor)
    hilo       : run_in_executor con ThreadPoolExecutor
    proceso    : run_in_executor con ProcessPoolExecutor (la configuración real)

    python -m bench.medicion_2_loop [--segundos 6]
"""

import argparse
import asyncio
import time

from app.cpu import sha256_iterado

from .comun import (
    BASE, Sonda, app_servidor, cliente, crear, ejecutar, guardar, resumen_latencias,
)


def calibrar() -> float:
    """Iteraciones de SHA-256 por segundo en esta máquina, en un solo núcleo."""
    n = 1_000_000
    t0 = time.perf_counter()
    sha256_iterado(b"calibracion", n)
    return n / (time.perf_counter() - t0)


async def una_corrida(modo: str, iteraciones: int) -> dict:
    async with app_servidor(hash_modo=modo):
        async with cliente(timeout=300) as s:
            # Línea base: servidor ocioso.
            sonda = Sonda(s, "/tareas?limite=1")
            sonda.iniciar()
            await asyncio.sleep(1.5)
            base = await sonda.detener()

            # Bajo carga: se encola el hash y se sondea hasta que termine.
            sonda = Sonda(s, "/tareas?limite=1")
            t0 = time.perf_counter()
            estado, tarea = await crear(s, "hashear", {"texto": "carga", "iteraciones": iteraciones}, timeout=300)
            t_post = time.perf_counter() - t0
            sonda.iniciar()
            while True:
                # Esta consulta también es una sonda: en modo bloqueante tarda lo
                # que le falta al hash, y recién ahí se sabe que terminó.
                async with s.get(f"{BASE}/tareas/{tarea['id']}") as r:
                    datos = await r.json()
                if datos["estado"] in ("completada", "fallida", "cancelada"):
                    break
                await asyncio.sleep(0.05)
            total = time.perf_counter() - t0
            carga = await sonda.detener()

    return {
        "modo": modo,
        "estado_final": datos["estado"],
        "post_ms": round(t_post * 1000, 2),
        "tarea_total_s": round(total, 2),
        "sin_carga": resumen_latencias(base),
        "con_carga": resumen_latencias(carga),
    }


async def principal(segundos: float) -> None:
    tasa = calibrar()
    iteraciones = int(tasa * segundos)
    print(f"SHA-256: {tasa:,.0f} iteraciones/s en un núcleo -> {iteraciones:,} iteraciones (~{segundos}s de CPU)")

    corridas = []
    for modo in ("bloqueante", "hilo", "proceso"):
        print(f"\n--- HASH_MODO={modo} ---")
        r = await una_corrida(modo, iteraciones)
        corridas.append(r)
        print(f"  POST /tareas respondió en {r['post_ms']} ms | tarea completa en {r['tarea_total_s']} s ({r['estado_final']})")
        print(f"  GET /tareas sin carga : {r['sin_carga']}")
        print(f"  GET /tareas con carga : {r['con_carga']}")

    guardar("medicion_2", {"iteraciones_por_s": round(tasa), "iteraciones": iteraciones, "corridas": corridas})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--segundos", type=float, default=6.0)
    ejecutar(principal(ap.parse_args().segundos))
