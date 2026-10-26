"""Crédito a clientes (V1.14): políticas por capas, estado financiero objetivo y bloqueos.

Reglas puras, sin SQL ni I/O. La política efectiva de un cliente sale de tres capas con precedencia
GENERAL -> TIPO (PERSONA/EMPRESA) -> INDIVIDUAL: gana la más específica configurada. Sin ninguna capa
configurada el cliente NO tiene crédito (nunca se inventa crédito ilimitado). Ver la migración 038
para la representación en la base.

Estado financiero (V1.14-E): se deriva de la antigüedad en días de la obligación pendiente más antigua
(NORMAL < 30 <= REGULAR < 45 <= DEMORADO < 60 <= CRÍTICA; sin deuda, SIN_DEUDA). Es objetivo: no lo
cambia ninguna calificación subjetiva. Entrar en CRÍTICA abre un episodio (cierre del crédito) que solo
el OWNER reabre; si el cliente sigue en CRÍTICA 30 días después del inicio del episodio, ese episodio
suma su (único) strike; con 3 strikes el crédito queda vetado para siempre.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from excepciones import DatosInvalidosError

ESTADO_SIN_DEUDA = "SIN_DEUDA"
ESTADO_NORMAL = "NORMAL"
ESTADO_REGULAR = "REGULAR"
ESTADO_DEMORADO = "DEMORADO"
ESTADO_CRITICA = "CRITICA"
ETIQUETAS_ESTADO = {
    ESTADO_SIN_DEUDA: "Sin deuda",
    ESTADO_NORMAL: "Normal",
    ESTADO_REGULAR: "Regular",
    ESTADO_DEMORADO: "Demorado",
    ESTADO_CRITICA: "Crítica",
}
DIAS_REGULAR = 30
DIAS_DEMORADO = 45
DIAS_CRITICA = 60
# Días que el cliente sigue en CRÍTICA, contados desde el inicio del episodio, hasta que el episodio suma su strike.
DIAS_HASTA_STRIKE = 30
STRIKES_PARA_VETO = 3
# Plazo máximo de una autorización temporal para operar en DEMORADO.
DIAS_MAXIMOS_AUTORIZACION = 30
LONGITUD_MAXIMA_TEXTO_CREDITO = 250

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


def estado_por_antiguedad(dias: int | None) -> str:
    """Estado financiero según los días de la obligación pendiente más antigua (`None`: no hay deuda)."""
    if dias is None:
        return ESTADO_SIN_DEUDA
    if dias >= DIAS_CRITICA:
        return ESTADO_CRITICA
    if dias >= DIAS_DEMORADO:
        return ESTADO_DEMORADO
    if dias >= DIAS_REGULAR:
        return ESTADO_REGULAR
    return ESTADO_NORMAL


def fecha_de(texto: str) -> date:
    """Día calendario de una fecha guardada como 'AAAA-MM-DD' o 'AAAA-MM-DD HH:MM:SS'."""
    return date.fromisoformat(texto[:10])


def dias_desde(fecha_origen: str, hoy: date) -> int:
    """Días enteros transcurridos desde la fecha de origen (el mismo día cuenta 0)."""
    return (hoy - fecha_de(fecha_origen)).days


def inicio_de_critica(fecha_origen: str) -> date:
    """Día en que una obligación cumple `DIAS_CRITICA`: el inicio teórico del episodio, determinista
    aunque se detecte días después (no hay un proceso en segundo plano)."""
    return fecha_de(fecha_origen) + timedelta(days=DIAS_CRITICA)


@dataclass(frozen=True)
class EpisodioCredito:
    """Un cierre de crédito por entrar en CRÍTICA. Abierto hasta que el OWNER lo reabre; suma como mucho
    un strike (`fecha_strike`)."""

    id: int
    cliente_id: int
    fecha_inicio: str
    fecha_registro: str
    fecha_strike: str | None
    fecha_reapertura: str | None
    reabierto_por: str | None
    observacion_reapertura: str | None

    @property
    def abierto(self) -> bool:
        return self.fecha_reapertura is None

    @property
    def fecha_limite_strike(self) -> date:
        return fecha_de(self.fecha_inicio) + timedelta(days=DIAS_HASTA_STRIKE)


@dataclass(frozen=True)
class AutorizacionCredito:
    """Permiso temporal del OWNER para seguir vendiendo a cuenta a un cliente DEMORADO, con un compromiso
    de pago y una fecha límite (inclusive). Vencida, no sirve: nunca es un desbloqueo permanente."""

    id: int
    cliente_id: int
    compromiso: str
    fecha_limite: str
    usuario: str
    fecha_creacion: str

    def vigente(self, hoy: date) -> bool:
        return fecha_de(self.fecha_limite) >= hoy


def validar_autorizacion(compromiso: str | None, fecha_limite: date, hoy: date) -> str:
    """Compromiso obligatorio y fecha límite entre mañana y `DIAS_MAXIMOS_AUTORIZACION` días.

    Raises:
        DatosInvalidosError: si falta el compromiso, es muy largo o la fecha está fuera de rango.
    """
    limpio = (compromiso or "").strip()
    if not limpio:
        raise DatosInvalidosError("La autorización requiere el compromiso de pago del cliente.")
    if len(limpio) > LONGITUD_MAXIMA_TEXTO_CREDITO:
        raise DatosInvalidosError(f"El compromiso no puede superar los {LONGITUD_MAXIMA_TEXTO_CREDITO} caracteres.")
    if fecha_limite <= hoy:
        raise DatosInvalidosError("La fecha límite de la autorización debe ser posterior a hoy.")
    if fecha_limite > hoy + timedelta(days=DIAS_MAXIMOS_AUTORIZACION):
        raise DatosInvalidosError(
            f"Una autorización temporal dura como máximo {DIAS_MAXIMOS_AUTORIZACION} días."
        )
    return limpio


def validar_observacion_reapertura(observacion: str | None) -> str:
    """La reapertura del crédito es una decisión explícita del OWNER: siempre explicada."""
    limpia = (observacion or "").strip()
    if not limpia:
        raise DatosInvalidosError("La reapertura del crédito requiere una observación.")
    if len(limpia) > LONGITUD_MAXIMA_TEXTO_CREDITO:
        raise DatosInvalidosError(f"La observación no puede superar los {LONGITUD_MAXIMA_TEXTO_CREDITO} caracteres.")
    return limpia


@dataclass(frozen=True)
class SituacionCredito:
    """Foto del crédito de un cliente a una fecha: política efectiva, deuda, estado financiero objetivo,
    episodio abierto (crédito cerrado), strikes y autorización vigente. El disponible nunca es negativo:
    si el límite bajó por debajo de la deuda, simplemente no queda nada para financiar."""

    politica: PoliticaEfectiva
    saldo_centavos: int
    hoy: date | None = None
    fecha_obligacion_mas_antigua: str | None = None
    episodio_abierto: EpisodioCredito | None = None
    strikes: int = 0
    autorizacion: AutorizacionCredito | None = None

    @property
    def dias_antiguedad(self) -> int | None:
        if self.fecha_obligacion_mas_antigua is None or self.hoy is None:
            return None
        return dias_desde(self.fecha_obligacion_mas_antigua, self.hoy)

    @property
    def estado(self) -> str:
        return estado_por_antiguedad(self.dias_antiguedad)

    @property
    def estado_etiqueta(self) -> str:
        return ETIQUETAS_ESTADO[self.estado]

    @property
    def vetado(self) -> bool:
        return self.strikes >= STRIKES_PARA_VETO

    @property
    def credito_cerrado(self) -> bool:
        return self.episodio_abierto is not None

    @property
    def autorizacion_vigente(self) -> AutorizacionCredito | None:
        if self.autorizacion is None or self.hoy is None or not self.autorizacion.vigente(self.hoy):
            return None
        return self.autorizacion

    @property
    def requiere_autorizacion(self) -> bool:
        """DEMORADO sin una autorización vigente del OWNER: no se le financia nada nuevo."""
        return self.estado == ESTADO_DEMORADO and self.autorizacion_vigente is None

    @property
    def disponible_centavos(self) -> int:
        if not self.politica.habilitado:
            return 0
        return max(0, self.politica.limite_centavos - self.saldo_centavos)

    @property
    def bloqueo(self) -> str | None:
        """Por qué el cliente no puede aumentar su deuda hoy, sin importar el monto; `None` si puede.
        Consultar, cobrar y vender al contado nunca se bloquean."""
        if self.vetado:
            return "Crédito vetado para siempre: el cliente acumuló 3 strikes."
        if self.credito_cerrado:
            return "Crédito cerrado por deuda crítica: solo el dueño puede reabrirlo."
        if self.estado == ESTADO_CRITICA:
            return "Deuda crítica (60 días o más): no se puede vender a cuenta."
        if self.requiere_autorizacion:
            return "Cliente demorado: vender a cuenta requiere una autorización temporal vigente del dueño."
        return None

    def motivo_rechazo(self, credito_centavos: int) -> str | None:
        """Por qué no puede financiarse `credito_centavos` ahora, o `None` si puede."""
        if self.bloqueo is not None:
            return self.bloqueo
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
