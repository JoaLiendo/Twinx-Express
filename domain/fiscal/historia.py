"""Historia fiscal que una recuperación (restore / rollback) no puede perder en silencio (RC4).

Restaurar un SQLite anterior no cambia la verdad de ARCA: un comprobante autorizado, uno que pudo haber
llegado a ARCA (ENVIANDO / INCIERTO), un CAEA otorgado o un informe aceptado siguen existiendo allá
aunque desaparezcan de la base local. Por eso, antes de reemplazar la base, se compara la historia de la
base actual con la de la base objetivo: todo hecho actual tiene que estar en el objetivo, en el mismo
estado o en uno que lo resuelve (p. ej. INCIERTO -> AUTORIZADO). PENDIENTE, DESCARTADO y RECHAZADO no
cuentan: nunca consumieron un número ni una autorización en ARCA.
"""

from collections import Counter
from dataclasses import dataclass

ESTADOS_CONOCIDOS_POR_ARCA = frozenset(
    {"ENVIANDO", "INCIERTO", "AUTORIZADO", "CAEA_PENDIENTE_INFORMAR", "CAEA_INFORMADO", "CAEA_INFORME_RECHAZADO"}
)

# Transiciones de la máquina de estados (migración 041) que agregan conocimiento sobre el comprobante.
_RESUELVE_A = {
    "ENVIANDO": {"INCIERTO", "AUTORIZADO", "RECHAZADO", "PENDIENTE"},
    "INCIERTO": {"AUTORIZADO", "RECHAZADO", "PENDIENTE"},
    "CAEA_PENDIENTE_INFORMAR": {"CAEA_INFORMADO", "CAEA_INFORME_RECHAZADO"},
    "CAEA_INFORME_RECHAZADO": {"CAEA_INFORMADO"},
}

_DESCRIPCION_ESTADO = {
    "AUTORIZADO": "comprobante(s) autorizado(s) con CAE",
    "ENVIANDO": "comprobante(s) enviado(s) a ARCA sin respuesta registrada",
    "INCIERTO": "comprobante(s) con resultado incierto",
    "CAEA_PENDIENTE_INFORMAR": "comprobante(s) bajo CAEA pendiente(s) de informar",
    "CAEA_INFORMADO": "comprobante(s) bajo CAEA ya informado(s)",
    "CAEA_INFORME_RECHAZADO": "comprobante(s) bajo CAEA con informe rechazado",
}


def _resoluciones(estado: str) -> frozenset[str]:
    """Estados alcanzables desde `estado` (incluido él mismo) por transiciones que agregan conocimiento."""
    alcanzados, pendientes = {estado}, [estado]
    while pendientes:
        for siguiente in _RESUELVE_A.get(pendientes.pop(), ()):
            if siguiente not in alcanzados:
                alcanzados.add(siguiente)
                pendientes.append(siguiente)
    return frozenset(alcanzados)


@dataclass(frozen=True)
class ComprobanteConocido:
    """Comprobante que ARCA conoce o pudo conocer, identificado por su numeración oficial."""

    ambiente: str
    cuit_emisor: str
    punto_venta: int
    tipo_comprobante: int
    numero: int
    estado: str
    codigo_autorizacion: str | None

    @property
    def clave(self) -> tuple[str, str, int, int, int]:
        return (self.ambiente, self.cuit_emisor, self.punto_venta, self.tipo_comprobante, self.numero)

    def cubierto_por(self, otro: "ComprobanteConocido") -> bool:
        if otro.clave != self.clave or otro.estado not in _resoluciones(self.estado):
            return False
        # Un código de autorización ya conocido tiene que seguir siendo el mismo.
        return self.codigo_autorizacion is None or otro.codigo_autorizacion == self.codigo_autorizacion


@dataclass(frozen=True)
class CaeaOtorgado:
    ambiente: str
    cuit: str
    codigo: str
    periodo: int
    orden: int


@dataclass(frozen=True)
class InformeSinMovimiento:
    """Informe "sin movimiento" de un punto de venta CAEA que ARCA aceptó."""

    ambiente: str
    codigo_caea: str
    punto_venta: int


@dataclass(frozen=True)
class HistoriaFiscal:
    comprobantes: tuple[ComprobanteConocido, ...] = ()
    caeas: frozenset[CaeaOtorgado] = frozenset()
    informes: frozenset[InformeSinMovimiento] = frozenset()

    @property
    def vacia(self) -> bool:
        return not (self.comprobantes or self.caeas or self.informes)


def no_cubierta_por(actual: HistoriaFiscal, objetivo: HistoriaFiscal) -> HistoriaFiscal:
    """Hechos de `actual` que se perderían si la base pasara a ser `objetivo`."""
    por_clave: dict[tuple, list[ComprobanteConocido]] = {}
    for comprobante in objetivo.comprobantes:
        por_clave.setdefault(comprobante.clave, []).append(comprobante)
    comprobantes = tuple(
        c for c in actual.comprobantes if not any(c.cubierto_por(o) for o in por_clave.get(c.clave, ()))
    )
    return HistoriaFiscal(comprobantes, actual.caeas - objetivo.caeas, actual.informes - objetivo.informes)


def describir(historia: HistoriaFiscal) -> str:
    """Resumen legible por cantidades (sin CAE ni CUIT: el mensaje puede quedar en un log o en pantalla)."""
    partes = []
    por_estado = Counter(c.estado for c in historia.comprobantes)
    for estado, descripcion in _DESCRIPCION_ESTADO.items():
        if por_estado[estado]:
            partes.append(f"{por_estado[estado]} {descripcion}")
    if historia.caeas:
        partes.append(f"{len(historia.caeas)} CAEA otorgado(s) por ARCA")
    if historia.informes:
        partes.append(f"{len(historia.informes)} informe(s) CAEA sin movimiento aceptado(s)")
    return "; ".join(partes)
