"""Reglas puras de la actualización masiva de precios (V1.2).

Toda la aritmética es entera y exacta (centavos y puntos básicos): nunca
floats. Sin SQL ni I/O.

Reglas verificadas contra el diseño actual del sistema (no se inventan
reglas comerciales):
- Los precios ya son enteros en centavos (`domain.dinero`).
- El sistema no tenía ningún redondeo: por eso `redondeo` es una opción
  explícita del usuario y por defecto no redondea (`NINGUNO`); `ENTERO`
  redondea al peso más cercano (medio peso hacia arriba).
- Un producto con precio de venta 0 ("precio no configurado", ver el seed
  inicial) queda fuera: aplicarle un porcentaje daría 0.
- Nunca se acepta un precio resultante menor o igual a cero.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from domain.dinero import texto_a_centavos
from domain.producto import MAXIMO_ENTERO
from excepciones import DatosInvalidosError

TIPOS_ACTUALIZACION = frozenset({"PORCENTAJE", "MONTO"})
DIRECCIONES = frozenset({"AUMENTAR", "DISMINUIR"})
REDONDEOS = frozenset({"NINGUNO", "ENTERO"})

# Tope de sanidad contra errores de tipeo (ej. "1000" en vez de "10"): no es una
# regla comercial, es una barrera contra un aumento absurdo.
PORCENTAJE_MAXIMO_BASIS_POINTS = 100_000  # 1000 %

ALCANCE_TODOS = "TODOS"
ALCANCE_SIN_CATEGORIA = "SIN_CATEGORIA"
PREFIJO_ALCANCE_CATEGORIA = "CATEGORIA:"
_PATRON_ALCANCE = re.compile(r"(TODOS|SIN_CATEGORIA|CATEGORIA:[1-9][0-9]{0,17})")
_PATRON_ALCANCE_CATEGORIA = re.compile(r"CATEGORIA:([1-9][0-9]{0,17})")
LONGITUD_MAXIMA_CLAVE = 100

ESTADO_OK = "OK"
ESTADO_SIN_CAMBIO = "SIN_CAMBIO"
ESTADO_INVALIDO = "INVALIDO"

_CENTAVOS_POR_PESO = 100


def porcentaje_a_basis_points(texto: str) -> int:
    """`"10"`/`"10,5"`/`"10.25"` -> puntos básicos (1000/1050/1025). Solo
    positivos y hasta 2 decimales: el sentido (subir/bajar) es un campo aparte."""
    try:
        valor = Decimal((texto or "").strip().replace(",", "."))
    except InvalidOperation:
        raise DatosInvalidosError(f"Porcentaje inválido: {texto!r}.") from None
    if not valor.is_finite() or valor <= 0:
        raise DatosInvalidosError("El porcentaje debe ser un número mayor a cero.")
    basis_points = valor * 100
    if basis_points != basis_points.to_integral_value():
        raise DatosInvalidosError("El porcentaje admite hasta 2 decimales.")
    if basis_points > PORCENTAJE_MAXIMO_BASIS_POINTS:
        raise DatosInvalidosError("El porcentaje no puede superar 1000 %.")
    return int(basis_points)


def monto_a_centavos_positivo(texto: str) -> int:
    centavos = texto_a_centavos(texto)
    if centavos <= 0:
        raise DatosInvalidosError("El monto debe ser mayor a cero.")
    return centavos


def validar_alcance(alcance: str) -> str:
    """`TODOS`, `SIN_CATEGORIA` o `CATEGORIA:<id>`: cualquier otro texto se rechaza
    (el alcance llega del formulario y nunca se confía en él)."""
    if not isinstance(alcance, str) or _PATRON_ALCANCE.fullmatch(alcance) is None:
        raise DatosInvalidosError("El alcance de la actualización no es válido.")
    return alcance


def categoria_del_alcance(alcance: str) -> int | None:
    """Id de la categoría de un alcance `CATEGORIA:<id>` (ya validado), o `None`."""
    coincidencia = _PATRON_ALCANCE_CATEGORIA.fullmatch(alcance)
    return int(coincidencia.group(1)) if coincidencia else None


def validar_seleccion(precios_esperados: dict[int, int]) -> None:
    """Ids y precios de la selección: enteros positivos dentro del rango que admite
    la base (un valor fuera de rango es un dato inválido, nunca un error interno)."""
    if not precios_esperados:
        raise DatosInvalidosError("Elegí al menos un producto para actualizar.")
    for producto_id, precio in precios_esperados.items():
        if not (0 < producto_id <= MAXIMO_ENTERO) or not (0 < precio <= MAXIMO_ENTERO):
            raise DatosInvalidosError("La selección de productos no es válida.")


@dataclass(frozen=True)
class CriterioActualizacion:
    """Cómo se calcula el precio nuevo. `valor`: puntos básicos si `tipo` es
    `PORCENTAJE`, centavos si es `MONTO`."""

    tipo: str
    direccion: str
    valor: int
    redondeo: str = "NINGUNO"

    def __post_init__(self) -> None:
        if self.tipo not in TIPOS_ACTUALIZACION:
            raise DatosInvalidosError(f"Tipo de actualización inválido: {self.tipo!r}.")
        if self.direccion not in DIRECCIONES:
            raise DatosInvalidosError(f"Dirección inválida: {self.direccion!r}.")
        if self.redondeo not in REDONDEOS:
            raise DatosInvalidosError(f"Redondeo inválido: {self.redondeo!r}.")
        if self.valor <= 0:
            raise DatosInvalidosError("El valor de la actualización debe ser mayor a cero.")
        # La confirmación de la operación llega por un formulario que un cliente
        # puede manipular: los topes se validan acá, no solo al interpretar el texto.
        if self.tipo == "PORCENTAJE" and self.valor > PORCENTAJE_MAXIMO_BASIS_POINTS:
            raise DatosInvalidosError("El porcentaje no puede superar 1000 %.")
        if self.tipo == "MONTO" and self.valor > MAXIMO_ENTERO:
            raise DatosInvalidosError("El monto está fuera del rango admitido.")


def crear_criterio(tipo: str, direccion: str, valor_texto: str, redondeo: str = "NINGUNO") -> CriterioActualizacion:
    """Arma el criterio desde el texto que escribió el usuario, validándolo."""
    if tipo == "PORCENTAJE":
        valor = porcentaje_a_basis_points(valor_texto)
    elif tipo == "MONTO":
        valor = monto_a_centavos_positivo(valor_texto)
    else:
        raise DatosInvalidosError(f"Tipo de actualización inválido: {tipo!r}.")
    return CriterioActualizacion(tipo=tipo, direccion=direccion, valor=valor, redondeo=redondeo)


def calcular_precio_nuevo(precio_actual_centavos: int, criterio: CriterioActualizacion) -> int:
    """Precio nuevo en centavos, aritmética entera (porcentaje: medio centavo
    hacia arriba). Puede dar <= 0: quien lo usa decide qué hacer (ver `evaluar`)."""
    signo = 1 if criterio.direccion == "AUMENTAR" else -1
    if criterio.tipo == "PORCENTAJE":
        nuevo = (precio_actual_centavos * (10_000 + signo * criterio.valor) + 5_000) // 10_000
    else:
        nuevo = precio_actual_centavos + signo * criterio.valor
    if criterio.redondeo == "ENTERO" and nuevo > 0:
        nuevo = ((nuevo + _CENTAVOS_POR_PESO // 2) // _CENTAVOS_POR_PESO) * _CENTAVOS_POR_PESO
    return nuevo


def evaluar(precio_actual_centavos: int, criterio: CriterioActualizacion) -> tuple[int, str]:
    """`(precio_nuevo, estado)`: `INVALIDO` si no quedaría un precio positivo,
    `SIN_CAMBIO` si el redondeo o el valor lo dejan igual, `OK` si cambia."""
    nuevo = calcular_precio_nuevo(precio_actual_centavos, criterio)
    if nuevo <= 0 or nuevo > MAXIMO_ENTERO:
        return nuevo, ESTADO_INVALIDO
    if nuevo == precio_actual_centavos:
        return nuevo, ESTADO_SIN_CAMBIO
    return nuevo, ESTADO_OK


@dataclass(frozen=True)
class PropuestaPrecio:
    """Un producto y el precio que resultaría de aplicar el criterio."""

    producto_id: int
    codigo_barras: str
    nombre: str
    precio_actual_centavos: int
    precio_nuevo_centavos: int
    estado: str

    @property
    def diferencia_centavos(self) -> int:
        return self.precio_nuevo_centavos - self.precio_actual_centavos
