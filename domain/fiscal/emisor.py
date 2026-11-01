"""Perfil fiscal del emisor (el negocio) y su validación."""

from dataclasses import dataclass
from datetime import date

from domain.fiscal import reglas
from domain.fiscal.comprobante import AMBIENTE_HOMOLOGACION, AMBIENTES, SnapshotEmisor
from domain.proveedor import normalizar_cuit
from excepciones import DatosInvalidosError

LONGITUD_MAXIMA_RAZON_SOCIAL = 150
LONGITUD_MAXIMA_DOMICILIO = 200
LONGITUD_MAXIMA_IIBB = 30
PUNTO_VENTA_MAXIMO = 99998


def _texto(valor: str | None, campo: str, maximo: int, obligatorio: bool) -> str | None:
    limpio = " ".join((valor or "").split())
    if not limpio:
        if obligatorio:
            raise DatosInvalidosError(f"Completá {campo}.")
        return None
    if len(limpio) > maximo:
        raise DatosInvalidosError(f"{campo.capitalize()} admite hasta {maximo} caracteres.")
    return limpio


def validar_punto_venta(valor: int | None, campo: str) -> int | None:
    if valor is None:
        return None
    if not 1 <= valor <= PUNTO_VENTA_MAXIMO:
        raise DatosInvalidosError(f"El {campo} debe estar entre 1 y {PUNTO_VENTA_MAXIMO}.")
    return valor


@dataclass(frozen=True)
class PerfilFiscal:
    """Datos fiscales del emisor. El punto de venta no se inventa: queda sin validar hasta que ARCA lo
    confirma por `FEParamGetPtosVenta` en el ambiente vigente."""

    cuit: str
    razon_social: str
    condicion_iva: str
    domicilio: str
    ingresos_brutos: str | None = None
    inicio_actividades: str | None = None
    habilitacion_clase_a: str = "NORMAL"
    punto_venta_cae: int | None = None
    punto_venta_cae_validado: str | None = None
    punto_venta_caea: int | None = None
    punto_venta_caea_validado: str | None = None
    contingencia_caea: bool = False
    ambiente: str = AMBIENTE_HOMOLOGACION

    def __post_init__(self) -> None:
        object.__setattr__(self, "cuit", normalizar_cuit(self.cuit) or "")
        if not self.cuit:
            raise DatosInvalidosError("Completá el CUIT del emisor.")
        object.__setattr__(self, "razon_social", _texto(self.razon_social, "la razón social", LONGITUD_MAXIMA_RAZON_SOCIAL, True))
        object.__setattr__(self, "domicilio", _texto(self.domicilio, "el domicilio fiscal", LONGITUD_MAXIMA_DOMICILIO, True))
        object.__setattr__(self, "ingresos_brutos", _texto(self.ingresos_brutos, "Ingresos Brutos", LONGITUD_MAXIMA_IIBB, False))
        if self.condicion_iva not in reglas.CONDICIONES_IVA_EMISOR:
            raise DatosInvalidosError("Elegí la condición frente al IVA del emisor.")
        if self.habilitacion_clase_a not in reglas.HABILITACIONES_CLASE_A:
            raise DatosInvalidosError("Habilitación de clase A inválida.")
        if self.ambiente not in AMBIENTES:
            raise DatosInvalidosError("Ambiente fiscal inválido.")
        inicio = (self.inicio_actividades or "").strip() or None
        if inicio is not None:
            try:
                date.fromisoformat(inicio)
            except ValueError as error:
                raise DatosInvalidosError("La fecha de inicio de actividades no es válida.") from error
        object.__setattr__(self, "inicio_actividades", inicio)
        validar_punto_venta(self.punto_venta_cae, "punto de venta")
        validar_punto_venta(self.punto_venta_caea, "punto de venta CAEA")
        if self.punto_venta_cae is not None and self.punto_venta_cae == self.punto_venta_caea:
            raise DatosInvalidosError("El punto de venta CAEA debe ser distinto del punto de venta CAE (lo exige ARCA).")

    @property
    def condicion_etiqueta(self) -> str:
        return reglas.CONDICIONES_IVA_EMISOR[self.condicion_iva]

    @property
    def punto_venta_cae_listo(self) -> bool:
        return self.punto_venta_cae is not None and self.punto_venta_cae_validado == self.ambiente

    @property
    def punto_venta_caea_listo(self) -> bool:
        return self.punto_venta_caea is not None and self.punto_venta_caea_validado == self.ambiente

    def snapshot(self, punto_venta: int) -> SnapshotEmisor:
        return SnapshotEmisor(
            cuit=self.cuit,
            razon_social=self.razon_social,
            condicion_iva=self.condicion_iva,
            domicilio=self.domicilio,
            ingresos_brutos=self.ingresos_brutos,
            inicio_actividades=self.inicio_actividades,
            punto_venta=punto_venta,
        )
