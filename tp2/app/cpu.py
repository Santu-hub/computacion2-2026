"""Trabajo CPU-bound puro.

Este módulo se mantiene mínimo a propósito: con ``spawn`` (macOS, Windows y
el contexto que usamos acá) cada proceso del pool lo importa de cero, y no
queremos que de paso arrastre FastAPI o aiohttp.
"""

import hashlib


def sha256_iterado(semilla: bytes, iteraciones: int) -> bytes:
    """Aplica SHA-256 ``iteraciones`` veces, encadenando cada digest."""
    digest = semilla
    for _ in range(iteraciones):
        digest = hashlib.sha256(digest).digest()
    return digest
