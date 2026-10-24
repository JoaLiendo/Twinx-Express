"""Entidad Proveedor y reglas de negocio asociadas (Fase 4A), su cuenta a pagar (V1.9) y, desde V1.13-A, sus
datos fiscales declarados y su clasificación por categorías del catálogo.

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
from collections.abc import Iterable
from dataclasses import dataclass

from domain.categoria import ArbolCategorias
from excepciones import DatosInvalidosError, PagoProveedorInvalidoError

LONGITUD_MAXIMA_OBSERVACION_PAGO = 250
LONGITUD_MAXIMA_RAZON_SOCIAL = 150
LONGITUD_MAXIMA_INGRESOS_BRUTOS = 30

# Condición frente al IVA tal como la declara el proveedor (V1.13-A). Es un dato informativo: ninguna
# regla del sistema deduce de ella qué comprobantes puede emitir (eso es V1.16, fiscal).
CONDICIONES_FISCALES = {
    "RESPONSABLE_INSCRIPTO": "Responsable inscripto",
    "MONOTRIBUTO": "Monotributo",
    "EXENTO": "Exento",
    "OTRA": "Otra",
}

_PESOS_DIGITO_VERIFICADOR_CUIT = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)

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
    razon_social: str | None = None
    cuit: str | None = None
    condicion_fiscal: str | None = None
    ingresos_brutos: str | None = None
    convenio_multilateral: bool = False

    def __post_init__(self) -> None:
        if not self.nombre or not self.nombre.strip():
            raise DatosInvalidosError("El nombre del proveedor no puede estar vacío.")
        if self.razon_social is not None and len(self.razon_social) > LONGITUD_MAXIMA_RAZON_SOCIAL:
            raise DatosInvalidosError(
                f"La razón social no puede superar los {LONGITUD_MAXIMA_RAZON_SOCIAL} caracteres."
            )
        if self.cuit is not None and normalizar_cuit(self.cuit) != self.cuit:
            raise DatosInvalidosError("El CUIT debe guardarse normalizado (11 dígitos, sin guiones).")
        if self.condicion_fiscal is not None and self.condicion_fiscal not in CONDICIONES_FISCALES:
            raise DatosInvalidosError(f"Condición fiscal inválida: {self.condicion_fiscal!r}.")
        if self.ingresos_brutos is not None and len(self.ingresos_brutos) > LONGITUD_MAXIMA_INGRESOS_BRUTOS:
            raise DatosInvalidosError(
                f"La inscripción de Ingresos Brutos no puede superar los {LONGITUD_MAXIMA_INGRESOS_BRUTOS} caracteres."
            )

    @property
    def cuit_formateado(self) -> str | None:
        """CUIT legible: "30-71234567-1"."""
        return f"{self.cuit[:2]}-{self.cuit[2:10]}-{self.cuit[10]}" if self.cuit else None

    @property
    def condicion_fiscal_etiqueta(self) -> str | None:
        return CONDICIONES_FISCALES.get(self.condicion_fiscal) if self.condicion_fiscal else None


@dataclass(frozen=True)
class CategoriaDeProveedor:
    """Una categoría del catálogo que clasifica a un proveedor (V1.13-A), con su ruta en el árbol."""

    id: int
    nombre: str
    ruta: str
    activa: bool


def normalizar_cuit(texto: str | None) -> str | None:
    """CUIT de 11 dígitos sin separadores, o `None` si viene vacío. Acepta guiones, puntos y espacios
    ("30-71234567-1"). Solo valida el formato y el dígito verificador (módulo 11): un error de tipeo no
    llega a la base, pero nada de esto afirma que el CUIT exista ni su situación fiscal.

    Raises:
        DatosInvalidosError: si no son 11 dígitos o el dígito verificador no corresponde.
    """
    if texto is None:
        return None
    limpio = "".join(caracter for caracter in texto if caracter not in "-. ")
    if not limpio:
        return None
    if len(limpio) != 11 or not limpio.isascii() or not limpio.isdigit():
        raise DatosInvalidosError("El CUIT debe tener 11 dígitos (por ejemplo 30-71234567-1).")
    resto = sum(int(digito) * peso for digito, peso in zip(limpio, _PESOS_DIGITO_VERIFICADOR_CUIT)) % 11
    verificador = 0 if resto == 0 else 11 - resto
    if verificador == 10 or verificador != int(limpio[10]):
        raise DatosInvalidosError("El CUIT no es válido: revisá los dígitos (el verificador no coincide).")
    return limpio


def cambios_de_categorias(
    elegidas: Iterable[int], actuales: Iterable[int], arbol: ArbolCategorias
) -> tuple[set[int], set[int]]:
    """`(agregar, quitar)` para dejar al proveedor clasificado exactamente en `elegidas`.

    Una categoría nueva para el proveedor tiene que existir y estar activa; una que ya lo clasificaba se
    conserva aunque después se haya dado de baja (el vínculo no se pierde por editar otros datos). Elegir
    una categoría y también una de sus subcategorías es válido: la clasificación es organizativa.

    Raises:
        DatosInvalidosError: si una categoría nueva no existe o está dada de baja.
    """
    elegidas_set, actuales_set = set(elegidas), set(actuales)
    agregar = elegidas_set - actuales_set
    for categoria_id in sorted(agregar):
        categoria = arbol.obtener(categoria_id)
        if categoria is None:
            raise DatosInvalidosError("Una de las categorías elegidas no existe.")
        if not categoria.activa:
            raise DatosInvalidosError(f"La categoría '{categoria.nombre}' está dada de baja.")
    return agregar, actuales_set - elegidas_set


def texto_opcional(texto: str | None) -> str | None:
    """Recorta un dato opcional de la ficha; `None` si queda vacío."""
    if texto is None:
        return None
    limpio = texto.strip()
    return limpio or None


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
