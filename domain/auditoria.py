"""Registro de auditoría de operaciones administrativas (V1.2).

Solo describe QUÉ pasó, QUIÉN lo hizo y sobre QUÉ entidad, con un resumen
corto. Nunca datos sensibles (contraseñas, hashes, cookies, claves de
idempotencia): el resumen lo arma cada servicio con texto propio.
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError

ACCIONES_VALIDAS = frozenset(
    {
        "PRODUCTO_CREADO",
        "PRODUCTO_EDITADO",
        "PRODUCTO_BAJA",
        "PRODUCTO_REACTIVADO",
        "PRODUCTOS_IMPORTADOS",
        "PRECIOS_ACTUALIZACION_MASIVA",
        "AJUSTE_STOCK",
        "COMPRA_REGISTRADA",
        "VENTA_ANULADA",
        "CAJA_APERTURA",
        "CAJA_CIERRE",
        "CAJA_INGRESO",
        "CAJA_EGRESO",
        "USUARIO_CREADO",
        "USUARIO_ROL_CAMBIADO",
        "USUARIO_ACTIVADO",
        "USUARIO_DESACTIVADO",
        "USUARIO_PASSWORD_RESETEADA",
        "CONFIGURACION_COMERCIO_CAMBIADA",
    }
)

LONGITUD_MAXIMA_RESUMEN = 300


@dataclass(frozen=True)
class EntradaAuditoria:
    """Una operación auditada, con el nombre del usuario ya resuelto."""

    id: int
    fecha: str
    usuario_id: int | None
    usuario_nombre_completo: str | None
    accion: str
    entidad: str
    entidad_id: int | None
    resumen: str


def normalizar(accion: str, resumen: str) -> str:
    """Valida la acción y devuelve el resumen en una sola línea, acotado."""
    if accion not in ACCIONES_VALIDAS:
        raise DatosInvalidosError(f"Acción de auditoría inválida: {accion!r}.")
    limpio = " ".join((resumen or "").split())
    if not limpio:
        raise DatosInvalidosError("El resumen de auditoría no puede estar vacío.")
    if len(limpio) > LONGITUD_MAXIMA_RESUMEN:
        limpio = limpio[: LONGITUD_MAXIMA_RESUMEN - 1] + "…"
    return limpio
