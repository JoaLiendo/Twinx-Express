"""Propuesta de precio de liquidación por vencimiento (V1.12-E).

El sistema propone; el OWNER decide (acepta, modifica o descarta) y el cambio se registra como cualquier
otro cambio de precio. Sin inflación externa ni "precio óptimo": un descuento objetivo sobre el precio
vigente, acotado por un margen mínimo sobre el costo relevante (el real de las partidas que vencen, si
se conoce). Todo en centavos enteros.
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError

DESCUENTO_OBJETIVO_POR_DEFECTO = 20
MARGEN_MINIMO_POR_DEFECTO = 10
DESCUENTO_MAXIMO = 90
MARGEN_MAXIMO = 500


def _porcentaje(texto: str, nombre: str, maximo: int) -> int:
    try:
        valor = int(str(texto).strip())
    except ValueError:
        raise DatosInvalidosError(f"{nombre} debe ser un número entero de porcentaje.") from None
    if not 0 <= valor <= maximo:
        raise DatosInvalidosError(f"{nombre} debe estar entre 0 y {maximo} %.")
    return valor


@dataclass(frozen=True)
class ParametrosLiquidacion:
    descuento_objetivo: int
    margen_minimo: int

    @classmethod
    def desde_texto(cls, descuento: str, margen: str) -> "ParametrosLiquidacion":
        return cls(
            _porcentaje(descuento, "El descuento objetivo", DESCUENTO_MAXIMO),
            _porcentaje(margen, "El margen mínimo", MARGEN_MAXIMO),
        )


@dataclass(frozen=True)
class PropuestaLiquidacion:
    precio_vigente_centavos: int
    precio_propuesto_centavos: int | None
    precio_piso_centavos: int | None
    costo_relevante_centavos: int | None
    costo_de_partida: bool
    aviso: str | None


def proponer_precio(
    precio_vigente_centavos: int,
    costo_relevante_centavos: int | None,
    costo_de_partida: bool,
    parametros: ParametrosLiquidacion,
) -> PropuestaLiquidacion:
    """Precio vigente menos el descuento objetivo, nunca por debajo de costo × (1 + margen mínimo).

    Sin costo conocido no hay piso: se propone el descuento y se avisa. Si el piso ya alcanza o supera el
    precio vigente, no hay propuesta (bajar el precio no respeta el margen mínimo)."""
    objetivo = precio_vigente_centavos * (100 - parametros.descuento_objetivo) // 100
    piso = None
    if costo_relevante_centavos is not None:
        piso = -(-costo_relevante_centavos * (100 + parametros.margen_minimo) // 100)  # redondeo hacia arriba
    if piso is not None and piso >= precio_vigente_centavos:
        return PropuestaLiquidacion(
            precio_vigente_centavos, None, piso, costo_relevante_centavos, costo_de_partida,
            "Con el margen mínimo configurado no conviene bajar el precio.",
        )
    propuesto = max(objetivo, piso) if piso is not None else objetivo
    if propuesto >= precio_vigente_centavos:
        return PropuestaLiquidacion(
            precio_vigente_centavos, None, piso, costo_relevante_centavos, costo_de_partida,
            "Con el descuento configurado el precio no cambia.",
        )
    aviso = None
    if costo_relevante_centavos is None:
        aviso = "Costo desconocido: la propuesta no garantiza el margen mínimo."
    elif not costo_de_partida:
        aviso = "El costo de las partidas que vencen no se conoce: se usa el costo vigente del producto."
    return PropuestaLiquidacion(
        precio_vigente_centavos, propuesto, piso, costo_relevante_centavos, costo_de_partida, aviso
    )
