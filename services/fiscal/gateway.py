"""Frontera entre los servicios fiscales y ARCA.

`GatewayARCA` es la interfaz: la implementan `GatewayARCAReal` (WSAA + WSFEv1 por HTTPS) y, en tests y E2E,
un ARCA falso determinista (`tests/fake_arca.py`). Los servicios piden el gateway del perfil vigente con
`gateway_para(perfil)`; nunca construyen SOAP ni conocen Token/Sign.

El ARCA falso NO forma parte del programa entregado: se inyecta solo desde tests y el servidor E2E con
`establecer_fabrica`, así el ejecutable nunca puede fabricar una autorización.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Protocol

from domain.fiscal.emisor import PerfilFiscal
from domain.fiscal.protocolo import (
    CAEAObtenido,
    ComprobanteConsultado,
    EstadoServidores,
    ParametroARCA,
    PuntoVentaARCA,
    RespuestaAutorizacion,
    RespuestaInforme,
    SolicitudAutorizacion,
)
from services.fiscal.arca import endpoints
from services.fiscal.arca.transporte import TransporteHTTPS
from services.fiscal.arca.wsaa import CacheTickets, ClienteWSAA
from services.fiscal.arca.wsfe import ClienteWSFE


class GatewayARCA(Protocol):
    ambiente: str

    def dummy(self) -> EstadoServidores: ...

    def ultimo_autorizado(self, punto_venta: int, tipo: int) -> int: ...

    def consultar(self, punto_venta: int, tipo: int, numero: int) -> ComprobanteConsultado | None: ...

    def solicitar_cae(self, solicitud: SolicitudAutorizacion) -> RespuestaAutorizacion: ...

    def puntos_venta(self) -> list[PuntoVentaARCA]: ...

    def parametros(self, tabla: str) -> list[ParametroARCA]: ...

    def condiciones_iva_receptor(self, clase: str) -> list[ParametroARCA]: ...

    def cotizacion(self, moneda: str) -> Decimal: ...

    def solicitar_caea(self, periodo: int, orden: int) -> CAEAObtenido: ...

    def consultar_caea(self, periodo: int, orden: int) -> CAEAObtenido | None: ...

    def informar_caea(self, solicitud: SolicitudAutorizacion) -> RespuestaInforme: ...

    def informar_sin_movimiento(self, punto_venta: int, caea: str) -> RespuestaInforme: ...

    def consultar_sin_movimiento(self, punto_venta: int, caea: str) -> bool: ...

    def invalidar_credenciales(self) -> None: ...


class GatewayARCAReal:
    def __init__(self, ambiente: str, cuit: str, cache: CacheTickets) -> None:
        self.ambiente = ambiente
        urls = endpoints.ENDPOINTS[ambiente]
        transporte = TransporteHTTPS()
        self._wsaa = ClienteWSAA(transporte, urls.wsaa, ambiente, cache)
        self._wsfe = ClienteWSFE(transporte, urls.wsfe, cuit, self._wsaa.ticket)

    def dummy(self) -> EstadoServidores:
        return self._wsfe.dummy()

    def ultimo_autorizado(self, punto_venta: int, tipo: int) -> int:
        return self._wsfe.ultimo_autorizado(punto_venta, tipo)

    def consultar(self, punto_venta: int, tipo: int, numero: int) -> ComprobanteConsultado | None:
        return self._wsfe.consultar(punto_venta, tipo, numero)

    def solicitar_cae(self, solicitud: SolicitudAutorizacion) -> RespuestaAutorizacion:
        return self._wsfe.solicitar_cae(solicitud)

    def puntos_venta(self) -> list[PuntoVentaARCA]:
        return self._wsfe.puntos_venta()

    def parametros(self, tabla: str) -> list[ParametroARCA]:
        return self._wsfe.parametros(tabla)

    def condiciones_iva_receptor(self, clase: str) -> list[ParametroARCA]:
        return self._wsfe.condiciones_iva_receptor(clase)

    def cotizacion(self, moneda: str) -> Decimal:
        return self._wsfe.cotizacion(moneda)

    def solicitar_caea(self, periodo: int, orden: int) -> CAEAObtenido:
        return self._wsfe.solicitar_caea(periodo, orden)

    def consultar_caea(self, periodo: int, orden: int) -> CAEAObtenido | None:
        return self._wsfe.consultar_caea(periodo, orden)

    def informar_caea(self, solicitud: SolicitudAutorizacion) -> RespuestaInforme:
        return self._wsfe.informar_caea(solicitud)

    def informar_sin_movimiento(self, punto_venta: int, caea: str) -> RespuestaInforme:
        return self._wsfe.informar_sin_movimiento(punto_venta, caea)

    def consultar_sin_movimiento(self, punto_venta: int, caea: str) -> bool:
        return self._wsfe.consultar_sin_movimiento(punto_venta, caea)

    def invalidar_credenciales(self) -> None:
        self._wsaa.invalidar()


_cache_tickets = CacheTickets()


def _fabrica_real(perfil: PerfilFiscal) -> GatewayARCA:
    return GatewayARCAReal(perfil.ambiente, perfil.cuit, _cache_tickets)


_fabrica: Callable[[PerfilFiscal], GatewayARCA] = _fabrica_real


def gateway_para(perfil: PerfilFiscal) -> GatewayARCA:
    """Gateway del ambiente del perfil. Producción solo existe si el perfil ya está en PRODUCCION, lo que
    la base permite únicamente con la habilitación del técnico."""
    return _fabrica(perfil)


def establecer_fabrica(fabrica: Callable[[PerfilFiscal], GatewayARCA] | None) -> None:
    """Solo tests/E2E: reemplaza la fábrica (`None` restaura la real)."""
    global _fabrica
    _fabrica = fabrica or _fabrica_real


def invalidar_tickets(ambiente: str) -> None:
    """Tras cambiar el certificado de un ambiente, el TA anterior deja de servir."""
    _cache_tickets.invalidar(ambiente)
