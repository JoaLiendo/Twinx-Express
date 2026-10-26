"""Crédito a clientes (V1.14): políticas por capas y su resolución.

Reglas puras, sin SQL ni I/O. La política efectiva de un cliente sale de tres capas con precedencia
GENERAL -> TIPO (PERSONA/EMPRESA) -> INDIVIDUAL: gana la más específica configurada. Sin ninguna capa
configurada el cliente NO tiene crédito (nunca se inventa crédito ilimitado). Ver la migración 038
para la representación en la base.
"""

from dataclasses import dataclass

from excepciones import DatosInvalidosError

AMBITO_GENERAL = "GENERAL"
AMBITO_CLIENTE = "CLIENTE"
AMBITOS_POR_TIPO = frozenset({"PERSONA", "EMPRESA"})
AMBITOS_GLOBALES = (AMBITO_GENERAL, "PERSONA", "EMPRESA")

MODO_HEREDAR = "HEREDAR"
MODO_SIN_CREDITO = "SIN_CREDITO"
MODO_LIMITE = "LIMITE"
MODOS = {
    MODO_HEREDAR: "Heredar",
    MODO_SIN_CREDITO: "Sin crédito",
    MODO_LIMITE: "Con límite",
}

# Origen de una política efectiva cuando ninguna capa está configurada.
ORIGEN_SIN_CONFIGURAR = "SIN_CONFIGURAR"
ETIQUETAS_ORIGEN = {
    AMBITO_GENERAL: "General",
    "PERSONA": "Por tipo (persona)",
    "EMPRESA": "Por tipo (empresa)",
    AMBITO_CLIENTE: "Individual",
    ORIGEN_SIN_CONFIGURAR: "Sin configurar",
}

# Mismo tope que el CHECK de la migración 038: mil millones de pesos.
LIMITE_MAXIMO_CENTAVOS = 100_000_000_000


@dataclass(frozen=True)
class PoliticaCredito:
    """Una capa configurada. `version` 0 representa una capa que todavía no tiene fila."""

    ambito: str
    modo: str
    limite_centavos: int | None = None
    cliente_id: int | None = None
    version: int = 0
    fecha_actualizacion: str | None = None
    usuario: str | None = None

    @property
    def configurada(self) -> bool:
        return self.modo != MODO_HEREDAR


@dataclass(frozen=True)
class PoliticaEfectiva:
    """Resultado de resolver las capas para un cliente: si tiene crédito habilitado, hasta cuánto y
    qué capa lo decidió. `limite_centavos` es 0 cuando no está habilitado."""

    habilitado: bool
    limite_centavos: int
    origen: str

    @property
    def origen_etiqueta(self) -> str:
        return ETIQUETAS_ORIGEN[self.origen]


@dataclass(frozen=True)
class SituacionCredito:
    """Foto del crédito de un cliente: su política efectiva y su deuda. El disponible nunca es negativo:
    si el límite bajó por debajo de la deuda, simplemente no queda nada para financiar."""

    politica: PoliticaEfectiva
    saldo_centavos: int

    @property
    def disponible_centavos(self) -> int:
        if not self.politica.habilitado:
            return 0
        return max(0, self.politica.limite_centavos - self.saldo_centavos)

    def motivo_rechazo(self, credito_centavos: int) -> str | None:
        """Por qué no puede financiarse `credito_centavos` ahora, o `None` si puede."""
        if not self.politica.habilitado:
            if self.politica.origen == ORIGEN_SIN_CONFIGURAR:
                return "El crédito a clientes no está configurado: el dueño debe definir una política de crédito."
            return "Este cliente no tiene crédito habilitado."
        if credito_centavos > self.disponible_centavos:
            return "El monto a cuenta supera el crédito disponible del cliente."
        return None


def validar_politica(ambito: str, modo: str, limite_centavos: int | None, cliente_id: int | None) -> None:
    """Valida una capa antes de guardarla (el esquema repite estas reglas como última defensa).

    Raises:
        DatosInvalidosError: si el ámbito o el modo no existen, GENERAL pide heredar, un límite falta o
            sobra, es negativo o supera `LIMITE_MAXIMO_CENTAVOS`.
    """
    if ambito not in (*AMBITOS_GLOBALES, AMBITO_CLIENTE):
        raise DatosInvalidosError(f"Ámbito de política inválido: {ambito!r}.")
    if (ambito == AMBITO_CLIENTE) != (cliente_id is not None):
        raise DatosInvalidosError("Solo una política individual indica cliente.")
    if modo not in MODOS:
        raise DatosInvalidosError(f"Modo de crédito inválido: {modo!r}.")
    if ambito == AMBITO_GENERAL and modo == MODO_HEREDAR:
        raise DatosInvalidosError("La política general no hereda de nada: elegí sin crédito o un límite.")
    if modo == MODO_LIMITE:
        if limite_centavos is None:
            raise DatosInvalidosError("Indicá el límite de crédito (puede ser 0).")
        if limite_centavos < 0:
            raise DatosInvalidosError("El límite de crédito no puede ser negativo.")
        if limite_centavos > LIMITE_MAXIMO_CENTAVOS:
            raise DatosInvalidosError("El límite de crédito es demasiado alto.")
    elif limite_centavos is not None:
        raise DatosInvalidosError("Solo el modo 'con límite' lleva un monto.")


def resolver_politica(
    general: PoliticaCredito | None,
    por_tipo: PoliticaCredito | None,
    individual: PoliticaCredito | None,
) -> PoliticaEfectiva:
    """Precedencia GENERAL -> TIPO -> INDIVIDUAL: la capa configurada más específica gana. Un cliente sin
    clasificar no tiene capa por tipo (quien llama pasa `None`)."""
    for capa in (individual, por_tipo, general):
        if capa is not None and capa.configurada:
            habilitado = capa.modo == MODO_LIMITE
            return PoliticaEfectiva(
                habilitado=habilitado,
                limite_centavos=capa.limite_centavos if habilitado else 0,
                origen=capa.ambito,
            )
    return PoliticaEfectiva(habilitado=False, limite_centavos=0, origen=ORIGEN_SIN_CONFIGURAR)
