"""Entidad Proveedor y reglas de negocio asociadas (Fase 4A), y su cuenta a pagar (V1.9).

`Proveedor` representa a quien se le compra mercadería, sin depender de cómo se persiste (db/) ni
de cómo se presenta (interfaces/). Valida sus propios invariantes al construirse, mismo patrón que
`domain.categoria.Categoria`.

La cuenta a pagar es un libro de `MovimientoProveedor` (migración 025): un `CARGO_COMPRA` por cada
compra a crédito, un `PAGO` por cada pago del kiosco al proveedor y una `REVERSA_COMPRA` al anular
una compra a crédito sin pagos posteriores (V1.9-B). El saldo nunca se guarda: es
`sum(CARGO_COMPRA) - sum(PAGO) - sum(REVERSA_COMPRA)` (ver
`db.repositorios.movimientos_proveedor.obtener_saldo_en_conexion`). Mismo patrón que
`domain.cliente` con `movimientos_cuenta`.
"""

import hashlib
import json
from dataclasses import dataclass

from excepciones import DatosInvalidosError, PagoProveedorInvalidoError

LONGITUD_MAXIMA_OBSERVACION_PAGO = 250

# Un cobro de cliente (domain.cliente) es siempre en efectivo; un pago a proveedor admite además
# transferencia, porque el kiosco sí paga a proveedores por ese medio en la práctica (V1.9-C).
MEDIOS_PAGO_PROVEEDOR_VALIDOS = frozenset({"EFECTIVO", "TRANSFERENCIA"})


@dataclass
class Proveedor:
    """Un proveedor de mercadería, validado al construirse.

    `id` y `fecha_creacion` quedan en `None` para un proveedor todavía
    no persistido; la capa de datos los completa al guardarlo.
    """

    nombre: str
    id: int | None = None
    contacto_nombre: str | None = None
    telefono: str | None = None
    email: str | None = None
    direccion: str | None = None
    notas: str | None = None
    activo: bool = True
    fecha_creacion: str | None = None

    def __post_init__(self) -> None:
        if not self.nombre or not self.nombre.strip():
            raise DatosInvalidosError("El nombre del proveedor no puede estar vacío.")


@dataclass(frozen=True)
class MovimientoProveedor:
    """Una fila del libro de cuenta a pagar de un proveedor (migración 025).

    `CARGO_COMPRA` y `REVERSA_COMPRA` referencian `compra_id`; un `PAGO` referencia
    `caja_movimiento_id` solo si es en efectivo (`None` si es por transferencia) y nunca una
    compra puntual (no se imputa a compras concretas, ver auditoría V1.9)."""

    id: int
    fecha: str
    proveedor_id: int
    tipo: str
    monto_centavos: int
    compra_id: int | None
    caja_movimiento_id: int | None
    medio_pago: str | None
    observacion: str | None
    usuario_id: int | None


def validar_observacion_pago(observacion: str | None) -> str | None:
    """Observación de un pago a proveedor, recortada; `None` si queda vacía."""
    if observacion is None:
        return None
    limpia = observacion.strip()
    if not limpia:
        return None
    if len(limpia) > LONGITUD_MAXIMA_OBSERVACION_PAGO:
        raise DatosInvalidosError(
            f"La observación del pago no puede superar los {LONGITUD_MAXIMA_OBSERVACION_PAGO} caracteres."
        )
    return limpia


def validar_medio_pago_proveedor(medio_pago: str) -> None:
    """Valida el medio de pago antes de decidir si el pago requiere caja abierta (efectivo) o no
    (transferencia): se valida primero para no tomar esa decisión con un medio inventado.

    Raises:
        PagoProveedorInvalidoError: si `medio_pago` no es 'EFECTIVO' ni 'TRANSFERENCIA'.
    """
    if medio_pago not in MEDIOS_PAGO_PROVEEDOR_VALIDOS:
        raise PagoProveedorInvalidoError(
            f"Medio de pago inválido: {medio_pago!r}. Debe ser uno de {sorted(MEDIOS_PAGO_PROVEEDOR_VALIDOS)}."
        )


def validar_pago_proveedor(monto_centavos: int, saldo_centavos: int) -> None:
    """Reglas comerciales de un pago a proveedor: `0 < monto <= saldo`. El medio ya se valida por
    separado (`validar_medio_pago_proveedor`), antes de conocer el saldo.

    Raises:
        PagoProveedorInvalidoError: si el monto está fuera de rango (incluye saldo 0: ningún
            monto positivo es válido).
    """
    if monto_centavos <= 0:
        raise PagoProveedorInvalidoError("El monto del pago debe ser mayor a cero.")
    if monto_centavos > saldo_centavos:
        raise PagoProveedorInvalidoError("El monto del pago no puede superar el saldo del proveedor.")


def calcular_hash_pago_proveedor(
    proveedor_id: int, monto_centavos: int, medio_pago: str, observacion: str | None
) -> str:
    """Hash canónico del contenido de un pago, para la idempotencia: la misma clave con otro
    proveedor, monto, medio u observación es un uso indebido de la clave."""
    contenido = {
        "proveedor_id": proveedor_id,
        "monto_centavos": monto_centavos,
        "medio_pago": medio_pago,
        "observacion": observacion,
    }
    texto = json.dumps(contenido, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()
