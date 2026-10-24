"""Reclamos e incidentes con proveedores (V1.13-C).

Un reclamo es información objetiva, separada de las evaluaciones subjetivas: nunca resta estrellas ni
toca la calificación. Sus datos de origen no cambian; su estado avanza solo mediante cambios auditados
(ver migración 035), y nada se borra.

Las relaciones con producto, compra, línea de compra o ajuste se guardan solo si se conocen de verdad.
En particular, el proveedor de un ajuste FALLA_FABRICA solo se considera identificado si TODAS las
unidades dadas de baja salieron de partidas ingresadas por compras de ese único proveedor, elegidas por
el usuario o únicas (`ELEGIDA`/`UNICA`). Si alguna salió por FEFO (asignación técnica: la partida real
no se conocía), de existencias heredadas o de varios proveedores, no se identifica a nadie: lo elige el
OWNER. Nunca se toma "el de la última compra".
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from excepciones import DatosInvalidosError, TransicionReclamoInvalidaError

LONGITUD_MAXIMA_TEXTO = 500

TIPOS_RECLAMO = {
    "FALLA_FABRICA": "Falla de fábrica",
    "ENTREGA": "Entrega",
    "CALIDAD": "Calidad",
    "FACTURACION": "Facturación",
    "OTRO": "Otro",
}

ESTADOS_RECLAMO = {
    "PENDIENTE": "Pendiente",
    "RECONOCIDO": "Reconocido",
    "RECHAZADO": "Rechazado",
    "RESUELTO": "Resuelto",
}

# Un reclamo está abierto mientras el proveedor no lo rechazó ni se resolvió.
ESTADOS_ABIERTOS = frozenset({"PENDIENTE", "RECONOCIDO"})

TRANSICIONES = {
    "PENDIENTE": ("RECONOCIDO", "RECHAZADO", "RESUELTO"),
    "RECONOCIDO": ("RESUELTO", "RECHAZADO"),
    "RECHAZADO": ("PENDIENTE",),
    "RESUELTO": ("PENDIENTE",),
}

_CRITERIOS_CONOCIDOS = frozenset({"ELEGIDA", "UNICA"})


@dataclass(frozen=True)
class CambioEstadoReclamo:
    id: int
    reclamo_id: int
    estado_anterior: str
    estado_nuevo: str
    observacion: str
    usuario_id: int
    usuario_nombre_completo: str | None
    fecha: str


@dataclass(frozen=True)
class ReclamoProveedor:
    """Un reclamo con los nombres ya resueltos para mostrarlo."""

    id: int
    proveedor_id: int
    proveedor_nombre: str
    tipo: str
    estado: str
    descripcion: str
    producto_id: int | None
    producto_nombre: str | None
    compra_id: int | None
    detalle_compra_id: int | None
    ajuste_id: int | None
    cantidad: int | None
    usuario_id: int
    usuario_nombre_completo: str | None
    fecha: str
    cambios: list[CambioEstadoReclamo] = field(default_factory=list)

    @property
    def abierto(self) -> bool:
        return self.estado in ESTADOS_ABIERTOS

    @property
    def tipo_etiqueta(self) -> str:
        return TIPOS_RECLAMO[self.tipo]

    @property
    def estado_etiqueta(self) -> str:
        return ESTADOS_RECLAMO[self.estado]

    @property
    def transiciones_posibles(self) -> tuple[str, ...]:
        return TRANSICIONES[self.estado]


@dataclass(frozen=True)
class ConsumoDeAjuste:
    """Una partida de la que salió parte de un ajuste, con lo que se sabe de su origen."""

    criterio: str
    origen_partida: str
    detalle_compra_id: int | None
    compra_id: int | None
    proveedor_id: int | None


@dataclass(frozen=True)
class OrigenIdentificado:
    """De dónde salió de verdad la mercadería de un ajuste: siempre un proveedor; la compra y su línea
    solo si fueron únicas."""

    proveedor_id: int
    compra_id: int | None
    detalle_compra_id: int | None


def identificar_origen(consumos: Iterable[ConsumoDeAjuste]) -> OrigenIdentificado | None:
    """Proveedor (y compra/línea si son únicas) del que salió TODO un ajuste, o `None` si no se puede
    afirmar (ver docstring del módulo)."""
    lista = list(consumos)
    if not lista:
        return None
    if any(c.criterio not in _CRITERIOS_CONOCIDOS or c.origen_partida != "COMPRA" for c in lista):
        return None
    proveedores = {c.proveedor_id for c in lista}
    if len(proveedores) != 1 or None in proveedores:
        return None
    compras = {c.compra_id for c in lista}
    detalles = {c.detalle_compra_id for c in lista}
    return OrigenIdentificado(
        proveedor_id=proveedores.pop(),
        compra_id=next(iter(compras)) if len(compras) == 1 else None,
        detalle_compra_id=next(iter(detalles)) if len(detalles) == 1 else None,
    )


def validar_texto(texto: str | None, que: str) -> str:
    """Descripción u observación obligatoria, recortada."""
    limpio = (texto or "").strip()
    if not limpio:
        raise DatosInvalidosError(f"{que} es obligatoria.")
    if len(limpio) > LONGITUD_MAXIMA_TEXTO:
        raise DatosInvalidosError(f"{que} no puede superar los {LONGITUD_MAXIMA_TEXTO} caracteres.")
    return limpio


def validar_tipo(tipo: str) -> None:
    if tipo not in TIPOS_RECLAMO:
        raise DatosInvalidosError(f"Tipo de reclamo inválido: {tipo!r}.")


def validar_cantidad(cantidad: int | None) -> None:
    if cantidad is not None and (isinstance(cantidad, bool) or not isinstance(cantidad, int) or cantidad <= 0):
        raise DatosInvalidosError("La cantidad reclamada debe ser un número entero mayor a cero.")


def validar_transicion(estado_actual: str, estado_nuevo: str) -> None:
    """Raises:
        TransicionReclamoInvalidaError: si el cambio no está permitido (incluye repetir el estado)."""
    if estado_nuevo not in ESTADOS_RECLAMO:
        raise TransicionReclamoInvalidaError(f"Estado de reclamo inválido: {estado_nuevo!r}.")
    if estado_nuevo not in TRANSICIONES[estado_actual]:
        raise TransicionReclamoInvalidaError(
            f"Un reclamo {ESTADOS_RECLAMO[estado_actual].lower()} no puede pasar a "
            f"{ESTADOS_RECLAMO[estado_nuevo].lower()}."
        )
