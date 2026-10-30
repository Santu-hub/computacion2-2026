"""Estados, entidad Tarea y esquemas de la API."""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

TIMEOUT_MAX = 3600.0


class Estado(str, Enum):
    PENDIENTE = "pendiente"
    EJECUTANDO = "ejecutando"
    COMPLETADA = "completada"
    FALLIDA = "fallida"
    CANCELADA = "cancelada"


TERMINALES = frozenset({Estado.COMPLETADA, Estado.FALLIDA, Estado.CANCELADA})


def ahora() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Entidad interna
# --------------------------------------------------------------------------

@dataclass
class Tarea:
    id: str
    tipo: str
    parametros: dict[str, Any]
    timeout: float
    estado: Estado = Estado.PENDIENTE
    creada: datetime = field(default_factory=ahora)
    iniciada: datetime | None = None
    terminada: datetime | None = None
    resultado: Any = None
    error: str | None = None

    # Estado interno del gestor (no se serializa).
    # Los instantes monotónicos sirven para medir duraciones sin que un
    # ajuste del reloj del sistema las altere.
    m_creada: float = field(default_factory=time.monotonic, repr=False)
    m_inicio: float | None = field(default=None, repr=False)
    m_fin: float | None = field(default=None, repr=False)
    asyncio_task: asyncio.Task | None = field(default=None, repr=False)
    motivo_cancelacion: str | None = field(default=None, repr=False)

    @property
    def espera_segundos(self) -> float | None:
        if self.m_inicio is None:
            return None
        return round(self.m_inicio - self.m_creada, 4)

    @property
    def duracion_segundos(self) -> float | None:
        if self.m_inicio is None or self.m_fin is None:
            return None
        return round(self.m_fin - self.m_inicio, 4)

    def a_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tipo": self.tipo,
            "estado": self.estado.value,
            "parametros": self.parametros,
            "timeout": self.timeout,
            "creada": self.creada.isoformat(),
            "iniciada": self.iniciada.isoformat() if self.iniciada else None,
            "terminada": self.terminada.isoformat() if self.terminada else None,
            "espera_segundos": self.espera_segundos,
            "duracion_segundos": self.duracion_segundos,
            "resultado": self.resultado,
            "error": self.error,
        }


# --------------------------------------------------------------------------
# Esquemas de entrada
# --------------------------------------------------------------------------

class _Estricto(BaseModel):
    """Rechaza campos desconocidos: un typo en el JSON debe dar 422, no ignorarse."""

    model_config = ConfigDict(extra="forbid")


class ParamsDescargar(_Estricto):
    urls: list[AnyHttpUrl] = Field(min_length=1, max_length=500)
    timeout_url: float = Field(10.0, gt=0, le=300, description="Timeout por URL, en segundos")


class ParamsHashear(_Estricto):
    texto: str = Field(max_length=10_000)
    iteraciones: int = Field(ge=1, le=2_000_000_000)


class ParamsEsperar(_Estricto):
    segundos: float = Field(ge=0, le=TIMEOUT_MAX)


class _SolicitudBase(_Estricto):
    timeout: float | None = Field(
        None, gt=0, le=TIMEOUT_MAX,
        description="Segundos máximos de ejecución. Si se omite, se usa el default del servidor.",
    )


class SolicitudDescargar(_SolicitudBase):
    tipo: Literal["descargar"]
    parametros: ParamsDescargar


class SolicitudHashear(_SolicitudBase):
    tipo: Literal["hashear"]
    parametros: ParamsHashear


class SolicitudEsperar(_SolicitudBase):
    tipo: Literal["esperar"]
    parametros: ParamsEsperar


Solicitud = Annotated[
    Union[SolicitudDescargar, SolicitudHashear, SolicitudEsperar],
    Field(discriminator="tipo"),
]


# --------------------------------------------------------------------------
# Esquemas de salida
# --------------------------------------------------------------------------

class TareaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tipo: str
    estado: Estado
    parametros: dict[str, Any]
    timeout: float
    creada: datetime
    iniciada: datetime | None
    terminada: datetime | None
    espera_segundos: float | None
    duracion_segundos: float | None
    resultado: Any
    error: str | None
