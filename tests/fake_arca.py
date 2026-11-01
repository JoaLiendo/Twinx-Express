"""ARCA falso, determinista y en memoria, para tests y para el servidor E2E. NUNCA forma parte del programa
entregado: sus "CAE" no tienen ningún valor fiscal.

Valida lo esencial como WSFEv1 (número = último + 1, importes que suman, IVA por alícuota, clase C sin IVA,
condición IVA del receptor presente) y permite inyectar fallas reproducibles:

- `caido`: todas las llamadas fallan sin enviarse (ARCA/Internet no disponible).
- `fallar_antes_de_enviar`: las próximas N llamadas fallan sin enviarse.
- `timeout_sin_procesar`: la próxima autorización se "envía" y no hay respuesta, sin procesarse.
- `perder_respuesta`: la próxima autorización SE PROCESA (queda autorizada en ARCA) y la respuesta se pierde.
- `respuesta_invalida`: la próxima autorización se procesa y responde algo ininterpretable.
- `rechazar_proximo` / `observar_proximo`: la próxima autorización se rechaza u observa con esos mensajes.
- `emitir_externo(pto, tipo)`: otro sistema autorizó un comprobante en esa secuencia.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from domain.fiscal.protocolo import (
    ComprobanteConsultado,
    EstadoServidores,
    MensajeARCA,
    ParametroARCA,
    PuntoVentaARCA,
    RespuestaAutorizacion,
    SolicitudAutorizacion,
)
from excepciones import ErrorComunicacionARCA, RespuestaARCAInvalidaError

_TASAS = {3: Decimal("0"), 4: Decimal("0.105"), 5: Decimal("0.21"), 6: Decimal("0.27"), 8: Decimal("0.05"), 9: Decimal("0.025")}
_CLASE_DE_TIPO = {1: "A", 2: "A", 3: "A", 6: "B", 7: "B", 8: "B", 11: "C", 12: "C", 13: "C"}

TABLAS = {
    "TiposCbte": [(str(i), d) for i, d in ((1, "Factura A"), (2, "Nota de Débito A"), (3, "Nota de Crédito A"), (6, "Factura B"),
                                            (7, "Nota de Débito B"), (8, "Nota de Crédito B"), (11, "Factura C"),
                                            (12, "Nota de Débito C"), (13, "Nota de Crédito C"), (51, "Factura M"))],
    "TiposConcepto": [("1", "Producto"), ("2", "Servicios"), ("3", "Productos y Servicios")],
    "TiposDoc": [("80", "CUIT"), ("86", "CUIL"), ("96", "DNI"), ("99", "Doc. (Otro)")],
    "TiposIva": [("3", "0%"), ("4", "10.5%"), ("5", "21%"), ("6", "27%"), ("8", "5%"), ("9", "2.5%")],
    "TiposMonedas": [("PES", "Pesos Argentinos"), ("DOL", "Dólar Estadounidense")],
    "TiposOpcional": [("2", "RG Empresas Promovidas")],
    "TiposTributos": [("99", "Otro")],
}
CONDICIONES = {
    "A": [("1", "IVA Responsable Inscripto"), ("6", "Responsable Monotributo"), ("13", "Monotributista Social")],
    "B": [("4", "IVA Sujeto Exento"), ("5", "Consumidor Final"), ("7", "Sujeto No Categorizado"), ("15", "IVA No Alcanzado")],
    "C": [("1", "IVA Responsable Inscripto"), ("4", "IVA Sujeto Exento"), ("5", "Consumidor Final"), ("6", "Responsable Monotributo")],
}


@dataclass
class Registro:
    solicitud: SolicitudAutorizacion
    cae: str
    vencimiento: str
    emision: str = "CAE"


@dataclass
class ARCAFalso:
    ambiente: str = "HOMOLOGACION"
    puntos: list[PuntoVentaARCA] = field(
        default_factory=lambda: [PuntoVentaARCA(3, "CAE", False, None), PuntoVentaARCA(4, "CAEA", False, None)]
    )
    caido: bool = False
    fallar_antes_de_enviar: int = 0
    timeout_sin_procesar: bool = False
    perder_respuesta: bool = False
    respuesta_invalida: bool = False
    rechazar_proximo: list[MensajeARCA] | None = None
    observar_proximo: list[MensajeARCA] | None = None
    llamadas: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._ultimos: dict[tuple[int, int], int] = {}
        self._registros: dict[tuple[int, int, int], Registro] = {}
        self._cae = 70000000000000
        self._candado = threading.Lock()

    # --- utilidades de test ---

    def emitir_externo(self, punto_venta: int, tipo: int) -> int:
        with self._candado:
            numero = self._ultimos.get((punto_venta, tipo), 0) + 1
            self._ultimos[(punto_venta, tipo)] = numero
            return numero

    def registro(self, punto_venta: int, tipo: int, numero: int) -> Registro | None:
        return self._registros.get((punto_venta, tipo, numero))

    @property
    def autorizaciones(self) -> int:
        return len(self._registros)

    def _entrar(self, operacion: str) -> None:
        self.llamadas.append(operacion)
        if self.caido:
            raise ErrorComunicacionARCA("ARCA falso caído.", enviado=False)
        if self.fallar_antes_de_enviar:
            self.fallar_antes_de_enviar -= 1
            raise ErrorComunicacionARCA("ARCA falso: conexión rechazada.", enviado=False)

    # --- GatewayARCA ---

    def invalidar_credenciales(self) -> None:
        self.llamadas.append("invalidar_credenciales")

    def dummy(self) -> EstadoServidores:
        self._entrar("FEDummy")
        return EstadoServidores("OK", "OK", "OK")

    def ultimo_autorizado(self, punto_venta: int, tipo: int) -> int:
        self._entrar("FECompUltimoAutorizado")
        return self._ultimos.get((punto_venta, tipo), 0)

    def consultar(self, punto_venta: int, tipo: int, numero: int) -> ComprobanteConsultado | None:
        self._entrar("FECompConsultar")
        registro = self._registros.get((punto_venta, tipo, numero))
        if registro is None:
            return None
        s = registro.solicitud
        return ComprobanteConsultado(
            tipo=tipo, punto_venta=punto_venta, numero=numero, fecha=s.fecha, doc_tipo=s.doc_tipo, doc_nro=s.doc_nro,
            total_centavos=s.importes.total_centavos, codigo_autorizacion=registro.cae, emision_tipo=registro.emision,
            vencimiento=registro.vencimiento, resultado="A", fecha_proceso=s.fecha,
        )

    def _validar(self, s: SolicitudAutorizacion) -> list[MensajeARCA]:
        errores = []
        esperado = self._ultimos.get((s.punto_venta, s.tipo), 0) + 1
        if s.numero != esperado:
            errores.append(MensajeARCA(10016, f"El numero o fecha del comprobante no se corresponde con el proximo a autorizar ({esperado})."))
        i = s.importes
        if i.total_centavos != i.no_gravado_centavos + i.neto_centavos + i.exento_centavos + i.tributos_centavos + i.iva_centavos:
            errores.append(MensajeARCA(10048, "El campo 'Importe Total' ImpTotal, debe ser igual a la suma de los demás importes."))
        clase = _CLASE_DE_TIPO.get(s.tipo)
        if clase == "C" and (i.iva_centavos or i.alicuotas):
            errores.append(MensajeARCA(10071, "Para comprobantes tipo C el objeto IVA no debe informarse."))
        if clase in ("A", "B"):
            if i.neto_centavos and not i.alicuotas:
                errores.append(MensajeARCA(10018, "Si ImpNeto es mayor a 0 el objeto IVA es obligatorio."))
            if sum(a.importe_centavos for a in i.alicuotas) != i.iva_centavos:
                errores.append(MensajeARCA(10023, "La suma de los importes de IVA no coincide con ImpIVA."))
            for a in i.alicuotas:
                exacto = Decimal(a.base_imponible_centavos) * _TASAS[a.alicuota_iva_id]
                if abs(Decimal(a.importe_centavos) - exacto) > Decimal(1):
                    errores.append(MensajeARCA(10051, f"El importe de IVA de la alícuota {a.alicuota_iva_id} no corresponde a su base."))
        if clase is None:
            errores.append(MensajeARCA(10007, "Tipo de comprobante no habilitado."))
        if not s.condicion_iva_receptor_id:
            errores.append(MensajeARCA(10242, "El campo Condicion Frente al IVA del receptor es obligatorio."))
        if s.tipo in (3, 8, 13) and not s.asociados:
            errores.append(MensajeARCA(10197, "Para notas de crédito es obligatorio informar el comprobante asociado."))
        for asociado in s.asociados:
            if (asociado.punto_venta, asociado.tipo, asociado.numero) not in self._registros:
                errores.append(MensajeARCA(10151, "El comprobante asociado no existe."))
        return errores

    def solicitar_cae(self, solicitud: SolicitudAutorizacion) -> RespuestaAutorizacion:
        self._entrar("FECAESolicitar")
        with self._candado:
            if self.timeout_sin_procesar:
                self.timeout_sin_procesar = False
                raise ErrorComunicacionARCA("ARCA falso: timeout de lectura.", enviado=True)
            errores = self._validar(solicitud)
            if self.rechazar_proximo:
                errores, self.rechazar_proximo = list(self.rechazar_proximo), None
            if errores:
                return RespuestaAutorizacion("R", solicitud.numero, solicitud.fecha, None, None, solicitud.fecha,
                                             reproceso="N", observaciones=tuple(errores))
            self._cae += 1
            fecha = datetime.strptime(solicitud.fecha, "%Y%m%d").date()
            vencimiento = (fecha + timedelta(days=10)).strftime("%Y%m%d")
            registro = Registro(solicitud, str(self._cae), vencimiento)
            self._registros[(solicitud.punto_venta, solicitud.tipo, solicitud.numero)] = registro
            self._ultimos[(solicitud.punto_venta, solicitud.tipo)] = solicitud.numero
            observaciones = tuple(self.observar_proximo or ())
            self.observar_proximo = None
            if self.perder_respuesta:
                self.perder_respuesta = False
                raise ErrorComunicacionARCA("ARCA falso: se cortó la conexión con la respuesta.", enviado=True)
            if self.respuesta_invalida:
                self.respuesta_invalida = False
                raise RespuestaARCAInvalidaError("ARCA falso: respuesta ininterpretable.")
            return RespuestaAutorizacion(
                "A", solicitud.numero, solicitud.fecha, registro.cae, vencimiento, solicitud.fecha,
                reproceso="N", observaciones=observaciones,
            )

    def puntos_venta(self) -> list[PuntoVentaARCA]:
        self._entrar("FEParamGetPtosVenta")
        return list(self.puntos)

    def parametros(self, tabla: str) -> list[ParametroARCA]:
        self._entrar(f"FEParamGet{tabla}")
        return [ParametroARCA(id=i, descripcion=d, desde="20100101", hasta="NULL") for i, d in TABLAS[tabla]]

    def condiciones_iva_receptor(self, clase: str) -> list[ParametroARCA]:
        self._entrar("FEParamGetCondicionIvaReceptor")
        return [ParametroARCA(id=i, descripcion=d, clase=clase) for i, d in CONDICIONES[clase]]

    def cotizacion(self, moneda: str) -> Decimal:
        self._entrar("FEParamGetCotizacion")
        return Decimal("1") if moneda == "PES" else Decimal("1000")


