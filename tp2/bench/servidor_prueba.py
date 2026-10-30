"""Servidor HTTP con latencia controlada, para medir descargas sin depender de internet.

    GET /lento?ms=500&bytes=20000   -> espera 500 ms y devuelve 20000 bytes
"""

import argparse
import asyncio

from aiohttp import web


async def lento(request: web.Request) -> web.Response:
    ms = int(request.query.get("ms", 500))
    cuerpo = int(request.query.get("bytes", 1024))
    await asyncio.sleep(ms / 1000)
    return web.Response(body=b"x" * cuerpo)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()
    app = web.Application()
    app.router.add_get("/lento", lento)
    web.run_app(app, host="127.0.0.1", port=args.port, print=None, backlog=4096)


if __name__ == "__main__":
    main()
