"""Evaluaciones de proveedores (V1.13-B).

La estrella general es una decisión humana independiente: nunca se calcula a partir de los criterios
complementarios, que son opcionales y no hace falta completar todos. Las evaluaciones son historia: la
calificación actual es la de la más reciente y corregir una es registrar otra (ver migración 034).

Una calificación actual de 1 o 2 estrellas solo dispara una advertencia al cargar una compra: no la
bloquea, no pide autorización ni cambia nada de la compra.
"""

from dataclasses import dataclass, field

from excepciones import DatosInvalidosError

ESTRELLAS_MINIMAS = 1
ESTRELLAS_MAXIMAS = 5
ESTRELLAS_CON_ADVERTENCIA = 2
LONGITUD_MAXIMA_COMENTARIO = 500

# Criterio -> etiqueta, en el orden en que se muestran. Las claves son también las columnas de la tabla.
CRITERIOS = {
    "PRECIO": "Precio",
    "CALIDAD": "Calidad",
    "ENTREGA": "Entrega",
    "CUMPLIMIENTO": "Cumplimiento",
    "CONDICIONES_PAGO": "Condiciones de pago",
    "ATENCION": "Atención",
}


@dataclass(frozen=True)
class EvaluacionProveedor:
    """Una evaluación registrada. `criterios` solo contiene los que se completaron."""

    id: int
    proveedor_id: int
    estrellas: int
    comentario: str
    usuario_id: int
    usuario_nombre_completo: str | None
    fecha: str
    criterios: dict[str, int] = field(default_factory=dict)

    @property
    def requiere_advertencia(self) -> bool:
        return requiere_advertencia(self.estrellas)


def requiere_advertencia(estrellas: int | None) -> bool:
    """`True` si una calificación actual amerita advertir al comprarle (1 o 2 estrellas). Sin
    calificación no hay advertencia: no tener evaluaciones no es una mala evaluación."""
    return estrellas is not None and estrellas <= ESTRELLAS_CON_ADVERTENCIA


def _puntaje(valor: int, nombre: str) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or not ESTRELLAS_MINIMAS <= valor <= ESTRELLAS_MAXIMAS:
        raise DatosInvalidosError(f"{nombre} debe ser un número entero de {ESTRELLAS_MINIMAS} a {ESTRELLAS_MAXIMAS}.")
    return valor


def validar_evaluacion(
    estrellas: int, comentario: str | None, criterios: dict[str, int | None]
) -> tuple[int, str, dict[str, int]]:
    """Valida y normaliza una evaluación nueva: `(estrellas, comentario recortado, criterios completados)`.

    Raises:
        DatosInvalidosError: estrellas o un criterio fuera de 1..5, comentario vacío o demasiado largo,
            o un criterio desconocido.
    """
    estrellas = _puntaje(estrellas, "La calificación general")
    limpio = (comentario or "").strip()
    if not limpio:
        raise DatosInvalidosError("El comentario de la evaluación es obligatorio.")
    if len(limpio) > LONGITUD_MAXIMA_COMENTARIO:
        raise DatosInvalidosError(f"El comentario no puede superar los {LONGITUD_MAXIMA_COMENTARIO} caracteres.")
    completados: dict[str, int] = {}
    for criterio, valor in criterios.items():
        if criterio not in CRITERIOS:
            raise DatosInvalidosError(f"Criterio de evaluación desconocido: {criterio!r}.")
        if valor is not None:
            completados[criterio] = _puntaje(valor, f"El criterio {CRITERIOS[criterio]}")
    return estrellas, limpio, completados
