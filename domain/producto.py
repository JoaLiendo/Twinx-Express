"""Entidad Producto y reglas de negocio asociadas.

Representa un producto tal como lo maneja el dominio, sin depender de
cómo se persiste (db/) ni de cómo se presenta (interfaces/). Valida
sus propios invariantes al construirse, de forma que no pueda existir
en memoria un `Producto` con datos inválidos.

Los precios se expresan en **centavos** (`int`), nunca en `float`, para
no arrastrar errores de precisión binaria en cálculos financieros (ver
`domain.dinero`).
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError

# Unidad de medida: por ahora es un dato puramente descriptivo (Fase 3C).
# No cambia cómo se guarda ni se opera `stock_actual` -- eso sigue siendo
# un conteo entero de "unidades" tal cual, sin conversión a gramos/mililitros
# todavía (esa conversión es una decisión de una fase posterior, si se
# decide soportar venta fraccionaria). Único lugar del proyecto donde se
# define este conjunto: cualquier otro módulo que necesite validar o listar
# unidades válidas debe importarlo de acá, no repetirlo.
# Cota técnica, no comercial: SQLite guarda enteros de 64 bits. Un valor mayor no se puede
# persistir (`OverflowError`), así que se rechaza como dato inválido antes de llegar a la base.
MAXIMO_ENTERO = 2**63 - 1

UNIDADES_VALIDAS = frozenset({"UNIDAD", "KG", "G", "LITRO", "ML"})


@dataclass
class Producto:
    """Producto del kiosco.

    `id` y `fecha_actualizacion` quedan en `None` para un producto
    todavía no persistido; la capa de datos los completa al guardarlo.
    """

    codigo_barras: str
    nombre: str
    precio_costo_centavos: int
    precio_venta_centavos: int
    stock_actual: int = 0
    stock_minimo: int = 0
    id: int | None = None
    fecha_actualizacion: str | None = None
    activo: bool = True
    categoria_id: int | None = None
    unidad_medida: str = "UNIDAD"
    imagen_archivo: str | None = None

    def __post_init__(self) -> None:
        self._validar()

    def _validar(self) -> None:
        if not self.codigo_barras or not self.codigo_barras.strip():
            raise DatosInvalidosError("El código de barras no puede estar vacío.")
        if not self.nombre or not self.nombre.strip():
            raise DatosInvalidosError("El nombre del producto no puede estar vacío.")
        if self.precio_costo_centavos < 0:
            raise DatosInvalidosError("El precio de costo no puede ser negativo.")
        if self.precio_venta_centavos < 0:
            raise DatosInvalidosError("El precio de venta no puede ser negativo.")
        if self.stock_actual < 0:
            raise DatosInvalidosError("El stock actual no puede ser negativo.")
        if self.stock_minimo < 0:
            raise DatosInvalidosError("El stock mínimo no puede ser negativo.")
        for etiqueta, valor in (
            ("El precio de costo", self.precio_costo_centavos),
            ("El precio de venta", self.precio_venta_centavos),
            ("El stock actual", self.stock_actual),
            ("El stock mínimo", self.stock_minimo),
        ):
            if valor > MAXIMO_ENTERO:
                raise DatosInvalidosError(f"{etiqueta} está fuera del rango admitido.")
        if self.unidad_medida not in UNIDADES_VALIDAS:
            raise DatosInvalidosError(
                f"Unidad de medida inválida: {self.unidad_medida!r}. "
                f"Debe ser una de {sorted(UNIDADES_VALIDAS)}."
            )

    @property
    def tiene_stock_critico(self) -> bool:
        """True si hay stock pero llegó al mínimo o está por debajo.

        Un producto sin stock (0) no es una alerta: todavía no se cargó
        nada (ej. el catálogo inicial de distribución) o ya se agotó, y
        ambos casos se resuelven comprando/ajustando, no con esta alerta.
        """
        return 0 < self.stock_actual <= self.stock_minimo

    def actualizar_stock(self, nuevo_stock: int) -> None:
        """Cambia el stock actual, validando que no quede negativo."""
        if nuevo_stock < 0:
            raise DatosInvalidosError("El stock actual no puede ser negativo.")
        self.stock_actual = nuevo_stock
