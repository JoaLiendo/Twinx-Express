"""Registro de auditoría de operaciones administrativas (V1.2).

Solo describe QUÉ pasó, QUIÉN lo hizo y sobre QUÉ entidad, con un resumen
corto. Nunca datos sensibles (contraseñas, hashes, cookies, claves de
idempotencia): el resumen lo arma cada servicio con texto propio.
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError

ACCION_BACKUP_MANUAL_OK = "BACKUP_MANUAL_OK"
ACCION_BACKUP_AUTOMATICO_OK = "BACKUP_AUTOMATICO_OK"
ACCION_BACKUP_CIERRE_CAJA_OK = "BACKUP_CIERRE_CAJA_OK"
ACCION_BACKUP_ERROR = "BACKUP_ERROR"
ACCION_BACKUP_SECUNDARIO_OK = "BACKUP_SECUNDARIO_OK"
ACCION_BACKUP_SECUNDARIO_ERROR = "BACKUP_SECUNDARIO_ERROR"
ACCION_PRE_UPDATE_BACKUP_OK = "PRE_UPDATE_BACKUP_OK"
ACCION_RESTORE = "RESTORE"
ACCION_ROLLBACK = "ROLLBACK"
ACCION_CONFIGURACION_BACKUP_CAMBIADA = "CONFIGURACION_BACKUP_CAMBIADA"

ACCIONES_BACKUP = (
    ACCION_BACKUP_MANUAL_OK,
    ACCION_BACKUP_AUTOMATICO_OK,
    ACCION_BACKUP_CIERRE_CAJA_OK,
    ACCION_BACKUP_ERROR,
    ACCION_BACKUP_SECUNDARIO_OK,
    ACCION_BACKUP_SECUNDARIO_ERROR,
    ACCION_PRE_UPDATE_BACKUP_OK,
    ACCION_RESTORE,
    ACCION_ROLLBACK,
    ACCION_CONFIGURACION_BACKUP_CAMBIADA,
)
"""Eventos de backup (V1.15). Los del sistema (arranque, actualización,
restore por consola) se registran sin usuario: no hay nadie autenticado y
nunca se inventa uno. Los errores de un backup que el usuario está esperando
y no se completa por otro backup en curso no se registran: no son fallos."""

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
        "COMPRA_ANULADA",
        "VENCIMIENTO_COMPRA_MODIFICADO",
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
        "CLIENTE_CREADO",
        "CLIENTE_EDITADO",
        "CLIENTE_DESACTIVADO",
        "CLIENTE_REACTIVADO",
        "CLIENTE_AUTORIZADO_ALTA",
        "CLIENTE_AUTORIZADO_BAJA",
        "POLITICA_CREDITO_CAMBIADA",
        "CREDITO_CIERRE",
        "CREDITO_STRIKE",
        "CREDITO_VETO",
        "CREDITO_REAPERTURA",
        "CREDITO_AUTORIZACION_DEMORADO",
        "VENTA_A_CUENTA",
        "VENTA_COMPRADOR_EXCEPCIONAL",
        "VENTA_VENCIDOS_AUTORIZADA",
        "PRECIO_LIQUIDACION_APLICADO",
        "CONFIGURACION_VENCIMIENTOS_CAMBIADA",
        "COBRO_CUENTA",
        "COBRO_IMPUTACION_MANUAL",
        "PROVEEDOR_CREADO",
        "PROVEEDOR_EDITADO",
        "PROVEEDOR_BAJA",
        "PROVEEDOR_REACTIVADO",
        "PROVEEDOR_CATEGORIAS_CAMBIADAS",
        "PROVEEDOR_EVALUADO",
        "RECLAMO_PROVEEDOR_CREADO",
        "RECLAMO_PROVEEDOR_ESTADO",
        "DOCUMENTO_PROVEEDOR_REGISTRADO",
        "PRODUCTO_PROVEEDOR_VINCULADO",
        "PRODUCTO_PROVEEDOR_QUITADO",
        "PRODUCTO_PROVEEDOR_PRINCIPAL",
        "PAGO_PROVEEDOR_REGISTRADO",
        "INVENTARIO_CREADO",
        "INVENTARIO_CONFIRMADO",
        "INVENTARIO_CANCELADO",
        *ACCIONES_BACKUP,
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
