"""Consulta de la auditoría de operaciones administrativas (V1.2).

La escritura NO pasa por acá: cada operación auditada llama a
`db.repositorios.auditoria.registrar_en_conexion` con la misma conexión con la
que escribe el cambio, así la operación y su rastro se confirman o se revierten
juntos (si la auditoría falla, la operación no se confirma). Solo se audita con
un usuario autenticado (`usuario_id` no `None`): las operaciones sin usuario (CLI,
siembra inicial del catálogo, tests) no generan registros.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from math import ceil

from db.repositorios import auditoria as repositorio_auditoria
from domain.auditoria import EntradaAuditoria

ENTRADAS_POR_PAGINA = 50
TAMANO_LOTE_EXPORTACION = 500


@dataclass(frozen=True)
class PaginaAuditoria:
    entradas: list[EntradaAuditoria]
    total: int
    pagina: int
    total_paginas: int


def listar(
    accion: str | None = None,
    usuario_id: int | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    limite: int = 200,
) -> list[EntradaAuditoria]:
    """Entradas de auditoría con filtros opcionales, la más reciente primero."""
    return repositorio_auditoria.listar(accion, usuario_id, fecha_desde, fecha_hasta, limite)


def listar_pagina(
    accion: str | None, usuario_id: int | None, fecha_desde: str | None, fecha_hasta: str | None, pagina: int
) -> PaginaAuditoria:
    """Una página de la auditoría, recortada en SQL. Una página fuera de rango se lleva a la última."""
    total = repositorio_auditoria.contar(accion, usuario_id, fecha_desde, fecha_hasta)
    total_paginas = max(1, ceil(total / ENTRADAS_POR_PAGINA))
    pagina_efectiva = min(pagina, total_paginas)
    entradas = repositorio_auditoria.listar(
        accion, usuario_id, fecha_desde, fecha_hasta, ENTRADAS_POR_PAGINA, (pagina_efectiva - 1) * ENTRADAS_POR_PAGINA
    )
    return PaginaAuditoria(entradas, total, pagina_efectiva, total_paginas)


def lotes_para_exportar(
    accion: str | None, usuario_id: int | None, fecha_desde: str | None, fecha_hasta: str | None
) -> Iterator[list[EntradaAuditoria]]:
    """Todas las entradas filtradas, de a lotes: la auditoría crece sin tope y no conviene tenerla entera
    en memoria. Cada lote abre y cierra su propia conexión."""
    antes_de_id = None
    while lote := repositorio_auditoria.listar_anteriores(
        accion, usuario_id, fecha_desde, fecha_hasta, antes_de_id, TAMANO_LOTE_EXPORTACION
    ):
        yield lote
        antes_de_id = lote[-1].id
