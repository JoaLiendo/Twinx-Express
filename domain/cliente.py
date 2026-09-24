"""Clientes y cuenta corriente (migración 020, V1.3).

`Cliente` modela a quien se le puede vender a cuenta. La cuenta corriente es un
libro de `MovimientoCuenta`: un CARGO por cada venta a cuenta y un COBRO por cada
pago en efectivo del cliente. El saldo nunca se guarda: es
`sum(CARGO) - sum(COBRO)` (ver `db.repositorios.clientes.obtener_saldo_en_conexion`).
"""

import hashlib
import json
from dataclasses import dataclass

from excepciones import CobroInvalidoError, DatosInvalidosError

LONGITUD_MAXIMA_NOMBRE = 120
LONGITUD_MAXIMA_TELEFONO = 30
LONGITUD_MAXIMA_EMAIL = 150
LONGITUD_MAXIMA_DIRECCION = 200
LONGITUD_MAXIMA_OBSERVACIONES = 500
LONGITUD_MAXIMA_DESCRIPCION_MOVIMIENTO = 250

TIPOS_MOVIMIENTO_CUENTA_VALIDOS = frozenset({"CARGO", "COBRO"})

# Los cobros de cuenta corriente son solo en efectivo: cada uno genera un INGRESO de caja.
MEDIO_PAGO_COBRO = "EFECTIVO"


def _limpiar_opcional(valor: str | None, campo: str, longitud_maxima: int) -> str | None:
    if valor is None:
        return None
    limpio = valor.strip()
    if not limpio:
        return None
    if len(limpio) > longitud_maxima:
        raise DatosInvalidosError(f"{campo} no puede superar los {longitud_maxima} caracteres.")
    return limpio


@dataclass
class Cliente:
    """Un cliente, validado y normalizado (espacios recortados, opcionales vacíos
    a `None`) al construirse. Los nombres pueden repetirse. El email no se valida
    en su formato, solo en su longitud.

    `id` y `fecha_creacion` quedan en `None` hasta que se persiste.
    """

    nombre: str
    id: int | None = None
    telefono: str | None = None
    email: str | None = None
    direccion: str | None = None
    observaciones: str | None = None
    activo: bool = True
    fecha_creacion: str | None = None

    def __post_init__(self) -> None:
        self.nombre = (self.nombre or "").strip()
        if not self.nombre:
            raise DatosInvalidosError("El nombre del cliente no puede estar vacío.")
        if len(self.nombre) > LONGITUD_MAXIMA_NOMBRE:
            raise DatosInvalidosError(f"El nombre no puede superar los {LONGITUD_MAXIMA_NOMBRE} caracteres.")
        self.telefono = _limpiar_opcional(self.telefono, "El teléfono", LONGITUD_MAXIMA_TELEFONO)
        self.email = _limpiar_opcional(self.email, "El email", LONGITUD_MAXIMA_EMAIL)
        self.direccion = _limpiar_opcional(self.direccion, "La dirección", LONGITUD_MAXIMA_DIRECCION)
        self.observaciones = _limpiar_opcional(self.observaciones, "Las observaciones", LONGITUD_MAXIMA_OBSERVACIONES)


@dataclass(frozen=True)
class MovimientoCuenta:
    """Una fila del libro de cuenta corriente. Un CARGO referencia `venta_id`; un
    COBRO referencia `caja_movimiento_id` (el INGRESO de caja que lo respalda)."""

    id: int
    fecha: str
    cliente_id: int
    tipo: str
    monto_centavos: int
    descripcion: str | None
    venta_id: int | None
    caja_movimiento_id: int | None
    usuario_id: int | None


@dataclass(frozen=True)
class ClienteConSaldo:
    """Un cliente con su saldo actual de cuenta corriente (listados y buscador del POS)."""

    cliente: Cliente
    saldo_centavos: int


@dataclass(frozen=True)
class ResumenCuenta:
    """Totales de la cuenta corriente de un cliente, calculados juntos sobre el mismo libro.

    Invariante: `saldo_centavos == total_cargos_centavos - total_cobros_centavos`.
    """

    saldo_centavos: int
    total_cargos_centavos: int
    total_cobros_centavos: int


@dataclass(frozen=True)
class EstadoCuenta:
    """Ficha de un cliente: datos, totales y movimientos (el más reciente primero), leídos
    en una única transacción para que saldo y libro nunca queden desfasados entre sí."""

    cliente: Cliente
    resumen: ResumenCuenta
    movimientos: list[MovimientoCuenta]


ESTADOS_LISTADO_CLIENTES = frozenset({"activos", "inactivos", "todos"})


def validar_descripcion_movimiento(descripcion: str | None) -> str | None:
    """Descripción de un movimiento de cuenta, recortada; `None` si queda vacía."""
    return _limpiar_opcional(descripcion, "La descripción del movimiento", LONGITUD_MAXIMA_DESCRIPCION_MOVIMIENTO)


def validar_cobro(monto_centavos: int, saldo_centavos: int, medio_pago: str = MEDIO_PAGO_COBRO) -> None:
    """Reglas comerciales de un cobro: solo efectivo y `0 < monto <= saldo`.

    Raises:
        CobroInvalidoError: si el medio no es efectivo o el monto está fuera de rango.
    """
    if medio_pago != MEDIO_PAGO_COBRO:
        raise CobroInvalidoError("Los cobros de cuenta corriente solo pueden ser en efectivo.")
    if monto_centavos <= 0:
        raise CobroInvalidoError("El monto del cobro debe ser mayor a cero.")
    if monto_centavos > saldo_centavos:
        raise CobroInvalidoError("El monto del cobro no puede superar el saldo del cliente.")


def calcular_hash_cobro(cliente_id: int, monto_centavos: int, descripcion: str | None) -> str:
    """Hash canónico del contenido de un cobro, para la idempotencia: la misma
    clave con otro cliente, monto o descripción es un uso indebido de la clave."""
    contenido = {"cliente_id": cliente_id, "monto_centavos": monto_centavos, "descripcion": descripcion}
    texto = json.dumps(contenido, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()
