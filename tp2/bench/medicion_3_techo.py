"""Medición 3: ¿dónde está el techo del pool y por qué?

Cinco experimentos, cada uno ataca un límite distinto:

  a) workers   : tareas `esperar` de 1 s con WORKERS=50 y K creciente.
  b) event loop: WORKERS=K (sin límite de pool): hasta 20.000 corrutinas vivas a la vez.
  c) CPU       : tareas `hashear` de ~1 s de CPU; el límite son los núcleos.
  d) conexiones: `descargar` con K URLs lentas; el límite es el connector (100).
  e) 10.000 tareas de golpe contra la configuración por defecto.

    python -m bench.medicion_3_techo [--rapido] [--solo a b c d e]
"""

import argparse
import asyncio
import math
import os
import time

from app.cpu import sha256_iterado

from .comun import (
    URL_LENTA, Sonda, app_servidor, cliente, crear, ejecutar, esperar_terminadas,
    estadisticas, guardar, objetivo_servidor, resumen_latencias,
)


async def enviar(s, cantidad: int, tipo: str, parametros_de, concurrencia: int = 200) -> dict:
    """Manda `cantidad` POST con un tope de requests en vuelo. Devuelve conteo por status y duración."""
    sem = asyncio.Semaphore(concurrencia)
    codigos: dict[int, int] = {}

    async def uno(i: int) -> None:
        async with sem:
            estado, _ = await crear(s, tipo, parametros_de(i))
            codigos[estado] = codigos.get(estado, 0) + 1

    t0 = time.perf_counter()
    await asyncio.gather(*(uno(i) for i in range(cantidad)))
    return {"codigos": codigos, "segundos": time.perf_counter() - t0}


# -- a) y b) -----------------------------------------------------------------

async def exp_esperar(workers: int, k: int, max_cola: int, segundos: float = 1.0) -> dict:
    async with app_servidor(workers=workers, max_cola=max_cola) as srv:
        async with cliente() as s:
            sonda = Sonda(s, "/estadisticas")
            sonda.iniciar()
            t0 = time.monotonic()
            envio = await enviar(s, k, "esperar", lambda i: {"segundos": segundos})
            pico = (await estadisticas(s))["workers"]["ocupados"]
            t1 = await esperar_terminadas(s, k)
            lat = await sonda.detener()
            rss = srv.rss_mb()
    total = t1 - t0
    ideal = math.ceil(k / workers) * segundos
    return {
        "workers": workers, "tareas": k, "total_s": round(total, 2), "ideal_s": ideal,
        "sobrecosto_s": round(total - ideal, 2), "post_s": round(envio["segundos"], 2),
        "post_por_s": round(k / envio["segundos"]), "workers_ocupados_pico": pico,
        "rss_mb": rss, "get_estadisticas": resumen_latencias(lat),
    }


def imprimir_esperar(r: dict) -> None:
    g = r["get_estadisticas"]
    print(f"  K={r['tareas']:>6} W={r['workers']:>6} | total {r['total_s']:>6}s (ideal {r['ideal_s']:>5}s, "
          f"+{r['sobrecosto_s']}s) | POST {r['post_s']}s ({r['post_por_s']}/s) | "
          f"RSS {r['rss_mb']} MB | GET p99 {g['p99_ms']} ms, max {g['max_ms']} ms")


async def exp_loop(k: int) -> dict:
    """K tareas `esperar` de 60 s con WORKERS=K: las K están vivas a la vez.

    Se mide la latencia de GET en ese estado estable (todas durmiendo), que es
    lo que cuesta mantener K corrutinas, separada de la que se ve mientras se
    admiten los POST.
    """
    async with app_servidor(workers=k, max_cola=k + 1000, tiempo_apagado=2) as srv:
        async with cliente() as s:
            envio = await enviar(s, k, "esperar", lambda i: {"segundos": 60})
            limite = time.monotonic() + 60
            while (await estadisticas(s))["workers"]["ocupados"] < k and time.monotonic() < limite:
                await asyncio.sleep(0.1)
            ocupados = (await estadisticas(s))["workers"]["ocupados"]
            sonda = Sonda(s, "/estadisticas")
            sonda.iniciar()
            await asyncio.sleep(4)
            lat = await sonda.detener()
            rss = srv.rss_mb()
    return {
        "tareas": k, "workers": k, "ocupados_simultaneos": ocupados,
        "post_s": round(envio["segundos"], 2), "post_por_s": round(k / envio["segundos"]),
        "rss_mb": rss, "get_estadisticas_con_k_vivas": resumen_latencias(lat),
    }


# -- c) ------------------------------------------------------------------------

async def exp_cpu(k: int, iteraciones: int, cpu_workers: int) -> dict:
    async with app_servidor(workers=max(k, 10), cpu_workers=cpu_workers):
        async with cliente() as s:
            t0 = time.monotonic()
            await enviar(s, k, "hashear", lambda i: {"texto": f"t{i}", "iteraciones": iteraciones})
            t1 = await esperar_terminadas(s, k)
    return {"tareas": k, "cpu_workers": cpu_workers, "total_s": round(t1 - t0, 2)}


# -- d) ------------------------------------------------------------------------

async def exp_descargas(k: int, ms: int = 1000) -> dict:
    async with app_servidor(workers=2):
        async with cliente() as s:
            urls = [f"{URL_LENTA}?ms={ms}&bytes=1000&i={i}" for i in range(k)]
            t0 = time.monotonic()
            estado, cuerpo = await crear(s, "descargar", {"urls": urls}, timeout=300)
            assert estado == 202, (estado, cuerpo)
            t1 = await esperar_terminadas(s, 1)
            est = await estadisticas(s)
    return {"urls": k, "total_s": round(t1 - t0, 2), "ideal_con_limite_100_s": math.ceil(k / 100) * ms / 1000,
            "completadas": est["por_estado"]["completada"]}


# -- e) ------------------------------------------------------------------------

async def exp_diluvio(k: int = 10_000) -> dict:
    async with app_servidor() as srv:  # configuración por defecto
        async with cliente() as s:
            sonda = Sonda(s, "/estadisticas")
            sonda.iniciar()
            envio = await enviar(s, k, "esperar", lambda i: {"segundos": 0.01})
            est = await estadisticas(s)
            aceptadas = envio["codigos"].get(202, 0)
            t0 = time.monotonic()
            await esperar_terminadas(s, aceptadas)
            drenaje = time.monotonic() - t0
            lat = await sonda.detener()
            rss = srv.rss_mb()
    return {
        "enviadas": k, "codigos": envio["codigos"], "post_s": round(envio["segundos"], 2),
        "post_por_s": round(k / envio["segundos"]), "pendientes_al_terminar_de_enviar": est["cola"]["pendientes"],
        "drenaje_s": round(drenaje, 2), "rss_mb": rss, "get_estadisticas": resumen_latencias(lat),
    }


async def principal(rapido: bool, solo: set[str]) -> None:
    resultado: dict = {}
    cuadros = lambda grande, chico: chico if rapido else grande

    if "a" in solo:
        print("\n== a) Techo del POOL: esperar 1 s, WORKERS=50 ==")
        filas = []
        for k in cuadros((50, 100, 200, 500, 1000, 2000), (50, 200, 500)):
            r = await exp_esperar(50, k, max_cola=5000)
            imprimir_esperar(r)
            filas.append(r)
        resultado["a_pool_workers"] = filas

    if "b" in solo:
        print("\n== b) Techo del EVENT LOOP: K tareas esperar(60 s), WORKERS=K ==")
        filas = []
        for k in cuadros((1000, 2000, 5000, 10_000, 20_000), (1000, 5000)):
            r = await exp_loop(k)
            g = r["get_estadisticas_con_k_vivas"]
            print(f"  K={k:>6} vivas={r['ocupados_simultaneos']:>6} | admisión {r['post_por_s']}/s ({r['post_s']}s) | "
                  f"RSS {r['rss_mb']} MB | GET con K vivas: p50 {g['p50_ms']} p99 {g['p99_ms']} max {g['max_ms']} ms")
            filas.append(r)
        resultado["b_event_loop"] = filas

    if "c" in solo:
        nucleos = os.cpu_count()
        tasa = _tasa()
        print(f"\n== c) Techo de CPU: hashear de ~1 s de CPU, {nucleos} núcleos ==")
        filas = []
        for k in cuadros((1, 2, 4, 6, 12, 24, 48), (1, 12, 24)):
            r = await exp_cpu(k, int(tasa), nucleos)
            r["ideal_s"] = math.ceil(k / nucleos)
            print(f"  K={k:>3} | total {r['total_s']:>6}s | ideal con {nucleos} núcleos {r['ideal_s']}s | "
                  f"secuencial sería {k}s")
            filas.append(r)
        resultado["c_cpu"] = {"iteraciones_por_s": round(tasa), "filas": filas}

    if "d" in solo:
        print("\n== d) Techo de CONEXIONES: 1 tarea con K URLs de 1 s, MAX_CONEXIONES=100 ==")
        filas = []
        async with objetivo_servidor():
            for k in cuadros((50, 100, 200, 400, 500), (100, 400)):
                r = await exp_descargas(k)
                print(f"  K={k:>4} | total {r['total_s']:>5}s | ideal con límite 100: {r['ideal_con_limite_100_s']}s "
                      f"| completadas {r['completadas']}")
                filas.append(r)
        resultado["d_conexiones"] = filas

    if "e" in solo:
        print("\n== e) 10.000 tareas de golpe, configuración por defecto (WORKERS=10, MAX_COLA=5000) ==")
        r = await exp_diluvio(2000 if rapido else 10_000)
        print(f"  {r}")
        resultado["e_diluvio"] = r

    guardar("medicion_3" + ("_rapida" if rapido else ""), resultado)


def _tasa() -> float:
    n = 1_000_000
    t0 = time.perf_counter()
    sha256_iterado(b"calibracion", n)
    return n / (time.perf_counter() - t0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rapido", action="store_true")
    ap.add_argument("--solo", nargs="+", default=list("abcde"), choices=list("abcde"))
    a = ap.parse_args()
    ejecutar(principal(a.rapido, set(a.solo)))
