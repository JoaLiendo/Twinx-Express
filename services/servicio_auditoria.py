"""Consulta de la auditoría de operaciones administrativas (V1.2).

La escritura NO pasa por acá: cada operación auditada llama a
`db.repositorios.auditoria.registrar_en_conexion` con la misma conexión con la
que escribe el cambio, así la operación y su rastro se confirman o se revierten
juntos (si la auditoría falla, la operación no se confirma). Solo se audita con
un usuario autenticado (`usuario_id` no `None`): las operaciones sin usuario (CLI,
siembra inicial del catálogo, tests) no generan registros.
"""

from db.repositorios import auditoria as repositorio_auditoria
from domain.auditoria import EntradaAuditoria


def listar(
    accion: str | None = None,
    usuario_id: int | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    limite: int = 200,
) -> list[EntradaAuditoria]:
    """Entradas de auditoría con filtros opcionales, la más reciente primero."""
    return repositorio_auditoria.listar(accion, usuario_id, fecha_desde, fecha_hasta, limite)
