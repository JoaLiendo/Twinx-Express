"""Representaciones del comprobante fiscal: QR (SVG para HTML, módulos para PDF) y PDF A4.

El PDF se escribe directamente (PDF 1.4, fuentes estándar Helvetica con WinAnsiEncoding, el QR como
rectángulos vectoriales): sin navegador embebido ni dependencias de dibujo, y empaquetable con PyInstaller.
El QR lo codifica `segno` (Python puro).
"""

import segno

from domain.fiscal.documento import DocumentoFiscal

_ANCHO, _ALTO = 595.28, 841.89
_MARGEN = 40


def qr_svg(url: str) -> str:
    """SVG en línea del QR (corrección de errores M), sin XML declaration."""
    return segno.make(url, error="m").svg_inline(scale=3, border=2)


def _modulos(url: str) -> list[list[bool]]:
    return [[bool(celda) for celda in fila] for fila in segno.make(url, error="m").matrix_iter(scale=1, border=2)]


def _texto_pdf(texto: str) -> str:
    codificado = texto.encode("cp1252", errors="replace").decode("latin-1")
    return codificado.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class _Lienzo:
    def __init__(self) -> None:
        self.paginas: list[list[str]] = [[]]
        self.y = _ALTO - _MARGEN

    @property
    def _ops(self) -> list[str]:
        return self.paginas[-1]

    def nueva_pagina(self) -> None:
        self.paginas.append([])
        self.y = _ALTO - _MARGEN

    def asegurar(self, alto: float) -> None:
        if self.y - alto < _MARGEN:
            self.nueva_pagina()

    def texto(self, x: float, y: float, texto: str, tamano: float = 9, negrita: bool = False) -> None:
        fuente = "F2" if negrita else "F1"
        self._ops.append(f"BT /{fuente} {tamano} Tf {x:.2f} {y:.2f} Td ({_texto_pdf(texto)}) Tj ET")

    def texto_derecha(self, x_derecha: float, y: float, texto: str, tamano: float = 9, negrita: bool = False) -> None:
        # Helvetica: ~0.5 em promedio por carácter; suficiente para alinear cifras.
        self.texto(x_derecha - len(texto) * tamano * 0.5, y, texto, tamano, negrita)

    def linea(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self._ops.append(f"0.6 w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def rect(self, x: float, y: float, ancho: float, alto: float, relleno: bool = False) -> None:
        self._ops.append(f"{x:.2f} {y:.2f} {ancho:.2f} {alto:.2f} re {'f' if relleno else 'S'}")

    def renglon(self, alto: float = 12) -> float:
        self.asegurar(alto)
        self.y -= alto
        return self.y


def _dibujar_qr(lienzo: _Lienzo, url: str, x: float, y_superior: float, lado: float) -> None:
    modulos = _modulos(url)
    tamano = lado / len(modulos)
    for indice_fila, fila in enumerate(modulos):
        y = y_superior - (indice_fila + 1) * tamano
        inicio = None
        for indice_col, oscuro in enumerate(fila + [False]):
            if oscuro and inicio is None:
                inicio = indice_col
            elif not oscuro and inicio is not None:
                lienzo.rect(x + inicio * tamano, y, (indice_col - inicio) * tamano, tamano, relleno=True)
                inicio = None


def pdf(documento: DocumentoFiscal) -> bytes:
    lienzo = _Lienzo()
    derecha = _ANCHO - _MARGEN
    if documento.ambiente_prueba:
        lienzo.texto(_MARGEN, lienzo.renglon(14), "HOMOLOGACIÓN (PRUEBAS) - SIN VALIDEZ FISCAL", 11, True)
    lienzo.y -= 6
    arriba = lienzo.y
    lienzo.rect(_ANCHO / 2 - 22, arriba - 44, 44, 44)
    lienzo.texto(_ANCHO / 2 - 9, arriba - 30, documento.letra, 24, True)
    lienzo.texto(_ANCHO / 2 - 17, arriba - 41, documento.codigo_tipo, 6)
    y = arriba - 12
    for indice, renglon in enumerate(documento.emisor):
        lienzo.texto(_MARGEN, y, renglon, 11 if indice == 0 else 8, indice == 0)
        y -= 13 if indice == 0 else 11
    y_der = arriba - 12
    for texto, negrita in ((documento.nombre_tipo.upper(), True), (f"N° {documento.numero}", True), (f"Fecha de emisión: {documento.fecha}", False)):
        lienzo.texto(_ANCHO / 2 + 40, y_der, texto, 10 if negrita else 9, negrita)
        y_der -= 14
    lienzo.y = min(y, y_der, arriba - 50) - 6
    lienzo.linea(_MARGEN, lienzo.y, derecha, lienzo.y)
    for renglon in documento.receptor:
        lienzo.texto(_MARGEN, lienzo.renglon(), renglon)
    for leyenda in documento.leyendas:
        lienzo.texto(_MARGEN, lienzo.renglon(), leyenda, 9, True)
    if documento.asociado:
        lienzo.texto(_MARGEN, lienzo.renglon(), documento.asociado)
    lienzo.y -= 4
    lienzo.linea(_MARGEN, lienzo.y, derecha, lienzo.y)

    columnas = (_MARGEN, 330, 400, 470, derecha)
    y = lienzo.renglon(14)
    lienzo.texto(columnas[0], y, "Descripción", 8, True)
    lienzo.texto_derecha(columnas[2] - 10, y, "Cant.", 8, True)
    lienzo.texto_derecha(columnas[3] - 6, y, "P. unitario", 8, True)
    lienzo.texto_derecha(columnas[4], y, "Subtotal", 8, True)
    for renglon in documento.renglones:
        y = lienzo.renglon()
        descripcion = renglon.descripcion if len(renglon.descripcion) <= 58 else renglon.descripcion[:57] + "…"
        if renglon.alicuota:
            descripcion += f" ({renglon.alicuota})"
        lienzo.texto(columnas[0], y, descripcion, 8)
        lienzo.texto_derecha(columnas[2] - 10, y, str(renglon.cantidad), 8)
        lienzo.texto_derecha(columnas[3] - 6, y, renglon.precio_unitario, 8)
        lienzo.texto_derecha(columnas[4], y, renglon.subtotal, 8)
    lienzo.y -= 4
    lienzo.linea(_MARGEN, lienzo.y, derecha, lienzo.y)
    for indice, (etiqueta, valor) in enumerate(documento.totales):
        ultimo = indice == len(documento.totales) - 1
        y = lienzo.renglon(15 if ultimo else 12)
        lienzo.texto_derecha(derecha - 110, y, etiqueta + ":", 10 if ultimo else 9, ultimo)
        lienzo.texto_derecha(derecha, y, valor, 10 if ultimo else 9, ultimo)
    if documento.transparencia:
        lienzo.y -= 6
        lienzo.texto(_MARGEN, lienzo.renglon(), "Régimen de Transparencia Fiscal al Consumidor (Ley 27.743)", 8, True)
        for etiqueta, valor in documento.transparencia:
            lienzo.texto(_MARGEN, lienzo.renglon(), f"{etiqueta}: {valor}", 8)

    lienzo.asegurar(110)
    lienzo.y -= 14
    arriba_qr = lienzo.y
    _dibujar_qr(lienzo, documento.qr_url, _MARGEN, arriba_qr, 90)
    y = arriba_qr - 20
    for etiqueta, valor in documento.autorizacion:
        lienzo.texto(_MARGEN + 110, y, f"{etiqueta}: {valor}", 10, True)
        y -= 16
    lienzo.y = arriba_qr - 96
    return _serializar(lienzo.paginas)


def _serializar(paginas: list[list[str]]) -> bytes:
    objetos: list[bytes] = []

    def agregar(contenido: bytes) -> int:
        objetos.append(contenido)
        return len(objetos)

    agregar(b"<< /Type /Catalog /Pages 2 0 R >>")
    agregar(b"")  # Pages: se completa al final
    fuente = agregar(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    negrita = agregar(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    hojas = []
    for operaciones in paginas:
        flujo = "\n".join(operaciones).encode("latin-1")
        contenido = agregar(b"<< /Length %d >>\nstream\n" % len(flujo) + flujo + b"\nendstream")
        hojas.append(
            agregar(
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_ANCHO} {_ALTO}] "
                    f"/Resources << /Font << /F1 {fuente} 0 R /F2 {negrita} 0 R >> >> /Contents {contenido} 0 R >>"
                ).encode("ascii")
            )
        )
    objetos[1] = f"<< /Type /Pages /Kids [{' '.join(f'{h} 0 R' for h in hojas)}] /Count {len(hojas)} >>".encode("ascii")
    salida = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    posiciones = []
    for numero, contenido in enumerate(objetos, start=1):
        posiciones.append(len(salida))
        salida += f"{numero} 0 obj\n".encode("ascii") + contenido + b"\nendobj\n"
    inicio_xref = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode("ascii")
    for posicion in posiciones:
        salida += f"{posicion:010d} 00000 n \n".encode("ascii")
    salida += f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{inicio_xref}\n%%EOF\n".encode("ascii")
    return bytes(salida)
