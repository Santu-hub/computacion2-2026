"""Volcado del estado a JSON y reporte del volcado anterior."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def escribir_volcado(directorio: str, contenido: dict[str, Any]) -> Path:
    """Escribe ``estado_<timestamp>.json`` de forma atómica.

    Se escribe a un temporal y se renombra: si el proceso muere a mitad de
    la escritura (por ejemplo un SIGKILL de docker tras el período de gracia)
    nunca queda un JSON truncado con el nombre definitivo.

    Es código bloqueante (disco): se llama con ``asyncio.to_thread``.
    """
    carpeta = Path(directorio)
    carpeta.mkdir(parents=True, exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = carpeta / f"estado_{marca}.json"
    temporal = destino.with_suffix(".json.tmp")
    with open(temporal, "w", encoding="utf-8") as f:
        json.dump(contenido, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporal, destino)
    return destino


def reportar_volcado_previo(directorio: str) -> None:
    """Loguea el volcado más reciente, si hay. No restaura nada."""
    carpeta = Path(directorio)
    candidatos = sorted(carpeta.glob("estado_*.json")) if carpeta.is_dir() else []
    if not candidatos:
        log.info("No hay volcados previos en %s", carpeta.resolve())
        return
    ultimo = candidatos[-1]
    try:
        datos = json.loads(ultimo.read_text(encoding="utf-8"))
        por_estado = datos.get("estadisticas", {}).get("por_estado", {})
        log.info(
            "Volcado previo encontrado: %s (generado %s, %d tareas, por estado: %s). "
            "No se restaura: el estado vive solo en memoria.",
            ultimo.name, datos.get("generado"), len(datos.get("tareas", [])), por_estado,
        )
    except (OSError, ValueError) as exc:
        log.warning("Volcado previo %s ilegible: %s", ultimo.name, exc)
