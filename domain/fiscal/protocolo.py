"""Tipos que cruzan la frontera del gateway ARCA: lo que Twinx pide y lo que ARCA contesta, sin SOAP.

Los montos viajan como centavos enteros; la conversión a decimal para el XML la hace el cliente WSFEv1.
"""

from dataclasses import dataclass, field

from domain.fiscal.iva import AlicuotaFiscal, ImportesFiscales


@dataclass(frozen=True)
class MensajeARCA:
    """Un `Err`, `Obs` o `Evt` de WSFEv1."""

    codigo: int
    mensaje: str

    def como_dict(self) -> dict:
        return {"codigo": self.codigo, "mensaje": self.mensaje}


@dataclass(frozen=True)
class EstadoServidores:
    aplicacion: str
    base_datos: str
    autenticacion: str

    @property
    def ok(self) -> bool:
        return self.aplicacion == self.base_datos == self.autenticacion == "OK"


@dataclass(frozen=True)
class PuntoVentaARCA:
    numero: int
    emision_tipo: str
    bloqueado: bool
    fecha_baja: str | None

    @property
    def habilitado(self) -> bool:
        return not self.bloqueado and self.fecha_baja is None


@dataclass(frozen=True)
class ParametroARCA:
    """Un renglón de una tabla `FEParamGet*` (o de `FEParamGetCondicionIvaReceptor`, con `clase`)."""

    id: str
    descripcion: str
    desde: str | None = None
    hasta: str | None = None
    clase: str | None = None

    def vigente_en(self, fecha_aaaammdd: str) -> bool:
        desde_ok = not self.desde or self.desde == "NULL" or self.desde <= fecha_aaaammdd
        hasta_ok = not self.hasta or self.hasta == "NULL" or self.hasta >= fecha_aaaammdd
        return desde_ok and hasta_ok


@dataclass(frozen=True)
class ComprobanteAsociado:
    tipo: int
    punto_venta: int
    numero: int
    cuit: str
    fecha: str  # AAAAMMDD


@dataclass(frozen=True)
class SolicitudAutorizacion:
    """Un comprobante listo para `FECAESolicitar` (o para informarse con `FECAEARegInformativo`)."""

    punto_venta: int
    tipo: int
    numero: int
    fecha: str  # AAAAMMDD
    concepto: int
    doc_tipo: int
    doc_nro: str
    condicion_iva_receptor_id: int
    importes: ImportesFiscales
    moneda: str
    cotizacion: str
    asociados: tuple[ComprobanteAsociado, ...] = ()
    caea: str | None = None
    fecha_hora_generacion: str | None = None  # AAAAMMDDhhmmss (solo CAEA)

    @property
    def alicuotas(self) -> tuple[AlicuotaFiscal, ...]:
        return self.importes.alicuotas

    def como_dict(self) -> dict:
        """Forma canónica del pedido fiscal (sin credenciales): se guarda su hash en el intento."""
        importes = self.importes
        return {
            "PtoVta": self.punto_venta,
            "CbteTipo": self.tipo,
            "CbteDesde": self.numero,
            "CbteHasta": self.numero,
            "CbteFch": self.fecha,
            "Concepto": self.concepto,
            "DocTipo": self.doc_tipo,
            "DocNro": self.doc_nro,
            "CondicionIVAReceptorId": self.condicion_iva_receptor_id,
            "ImpTotal": importes.total_centavos,
            "ImpTotConc": importes.no_gravado_centavos,
            "ImpNeto": importes.neto_centavos,
            "ImpOpEx": importes.exento_centavos,
            "ImpTrib": importes.tributos_centavos,
            "ImpIVA": importes.iva_centavos,
            "Iva": [[a.alicuota_iva_id, a.base_imponible_centavos, a.importe_centavos] for a in importes.alicuotas],
            "MonId": self.moneda,
            "MonCotiz": self.cotizacion,
            "CbtesAsoc": [[a.tipo, a.punto_venta, a.numero, a.cuit, a.fecha] for a in self.asociados],
            "CAEA": self.caea,
            "CbteFchHsGen": self.fecha_hora_generacion,
        }


@dataclass(frozen=True)
class RespuestaAutorizacion:
    """Resultado de `FECAESolicitar`: `A` aprobado (con CAE), `R` rechazado (sin CAE)."""

    resultado: str
    numero: int | None
    fecha_comprobante: str | None  # AAAAMMDD
    cae: str | None
    vencimiento: str | None  # AAAAMMDD
    fecha_proceso: str | None
    reproceso: str | None = None
    observaciones: tuple[MensajeARCA, ...] = ()
    errores: tuple[MensajeARCA, ...] = ()
    eventos: tuple[MensajeARCA, ...] = ()


@dataclass(frozen=True)
class ComprobanteConsultado:
    """Lo que ARCA tiene registrado para un comprobante (`FECompConsultar`)."""

    tipo: int
    punto_venta: int
    numero: int
    fecha: str
    doc_tipo: int
    doc_nro: str
    total_centavos: int
    codigo_autorizacion: str | None
    emision_tipo: str | None
    vencimiento: str | None
    resultado: str | None
    fecha_proceso: str | None
    observaciones: tuple[MensajeARCA, ...] = field(default=())


@dataclass(frozen=True)
class CAEAObtenido:
    """Lo que devuelve `FECAEASolicitar` / `FECAEAConsultar` (fechas en AAAAMMDD)."""

    codigo: str
    periodo: int
    orden: int
    vigente_desde: str
    vigente_hasta: str
    tope_informe: str
    fecha_proceso: str | None
    observaciones: tuple[MensajeARCA, ...] = ()


@dataclass(frozen=True)
class RespuestaInforme:
    """Resultado de informar a ARCA (`FECAEARegInformativo`, `FECAEASinMovimientoInformar`)."""

    resultado: str
    fecha_proceso: str | None = None
    observaciones: tuple[MensajeARCA, ...] = ()
    errores: tuple[MensajeARCA, ...] = ()
