"""Dominio fiscal ARCA (V1.16): reglas puras, sin SQL, sin SOAP ni I/O.

Una venta interna NO es un comprobante fiscal, y un comprobante fiscal no está autorizado hasta que
ARCA devuelve su CAE (o se emite bajo un CAEA vigente). Este paquete modela esa frontera.
"""
