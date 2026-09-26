"""Neutralización de celdas de texto para archivos de planilla (CSV) y su regla inversa al importar.

Un texto que empiece con `=`, `+`, `-`, `@`, tabulación o retorno de carro lo interpretaría como fórmula Excel /
LibreOffice al abrir el archivo. La exportación le antepone un apóstrofo (`neutralizar_texto`); la importación de un
CSV propio lo quita (`desneutralizar_texto`) sin perder datos.

La regla es INYECTIVA, así el ciclo exportar -> editar -> reimportar devuelve exactamente el dato original:

  * se antepone `'` cuando el texto empieza con un prefijo peligroso **o con un apóstrofo**;
  * se quita UN apóstrofo inicial solo si lo que sigue empieza con un prefijo peligroso o con otro apóstrofo.

Un nombre que legítimamente empieza con apóstrofo (`'Cinco`) sale como `''Cinco` y vuelve como `'Cinco`; uno de la
forma `'=x` sale como `''=x` y vuelve igual; un `=x` sale como `'=x` y vuelve como `=x`. Un apóstrofo inicial que no
cumple la condición (`'Cinco` en un archivo armado a mano) se conserva tal cual.
"""

_PREFIJOS_DE_FORMULA = ("=", "+", "-", "@", "\t", "\r")
_PREFIJOS_A_ESCAPAR = _PREFIJOS_DE_FORMULA + ("'",)


class Numero(str):
    """Texto numérico generado por el sistema (cantidad, importe): nunca se neutraliza."""


def neutralizar_texto(texto: str | None) -> str:
    """Texto seguro para una celda: `None` es vacío; uno que empieza como fórmula (o con apóstrofo, para que la
    regla inversa sea inequívoca) lleva un `'` delante."""
    if not texto:
        return ""
    return "'" + texto if texto.startswith(_PREFIJOS_A_ESCAPAR) else texto


def desneutralizar_texto(texto: str) -> str:
    """Inversa exacta de `neutralizar_texto`: quita el apóstrofo inicial solo si la exportación lo pudo haber
    agregado; cualquier otro apóstrofo inicial es un dato del usuario y se respeta."""
    if texto.startswith("'") and texto[1:].startswith(_PREFIJOS_A_ESCAPAR):
        return texto[1:]
    return texto
