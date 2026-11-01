"""Servicios de facturación fiscal ARCA (V1.16).

Frontera explícita: Venta → SolicitudFiscal → GatewayARCA → ComprobanteFiscal. Ninguna ruta web ni el
servicio de ventas habla SOAP: el protocolo vive en `services.fiscal.arca`, detrás de un gateway que
también tiene un doble determinista para tests.
"""
