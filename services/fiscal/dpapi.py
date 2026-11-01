"""Cifrado local con DPAPI de Windows (CryptProtectData), alcance usuario actual.

Protege el material secreto fiscal en disco sin que Twinx tenga que guardar ninguna contraseña: solo
el mismo usuario de Windows, en la misma PC, puede descifrarlo. Un archivo copiado a otra máquina (o
restaurado por otro usuario) es ilegible: hay que reimportar el certificado, que es lo correcto.
"""

import ctypes
import sys
from ctypes import wintypes

from excepciones import CredencialesFiscalesError

# Atadura extra: un blob DPAPI de otra aplicación del mismo usuario no se descifra como uno de Twinx.
_ENTROPIA = b"TwinxExpress/fiscal/v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(datos: bytes) -> tuple[_Blob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(datos, len(datos))
    return _Blob(len(datos), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _api():
    if sys.platform != "win32":
        raise CredencialesFiscalesError("La protección de credenciales fiscales requiere Windows (DPAPI).")
    return ctypes.windll.crypt32, ctypes.windll.kernel32


def proteger(datos: bytes) -> bytes:
    crypt32, kernel32 = _api()
    entrada, _buf_entrada = _blob(datos)
    entropia, _buf_entropia = _blob(_ENTROPIA)
    salida = _Blob()
    if not crypt32.CryptProtectData(
        ctypes.byref(entrada), None, ctypes.byref(entropia), None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(salida)
    ):
        raise CredencialesFiscalesError("Windows no pudo proteger la credencial fiscal (DPAPI).")
    try:
        return ctypes.string_at(salida.pbData, salida.cbData)
    finally:
        kernel32.LocalFree(salida.pbData)


def desproteger(datos: bytes) -> bytes:
    crypt32, kernel32 = _api()
    entrada, _buf_entrada = _blob(datos)
    entropia, _buf_entropia = _blob(_ENTROPIA)
    salida = _Blob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(entrada), None, ctypes.byref(entropia), None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(salida)
    ):
        raise CredencialesFiscalesError(
            "La credencial fiscal no puede descifrarse en esta PC o con este usuario de Windows: reimportá el certificado."
        )
    try:
        return ctypes.string_at(salida.pbData, salida.cbData)
    finally:
        kernel32.LocalFree(salida.pbData)
