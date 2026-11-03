"""RC4: qué historia fiscal de la base actual queda cubierta por la base objetivo de una recuperación."""

from domain.fiscal.historia import (
    CaeaOtorgado,
    ComprobanteConocido,
    HistoriaFiscal,
    InformeSinMovimiento,
    describir,
    no_cubierta_por,
)


def _comprobante(numero=1, estado="AUTORIZADO", codigo="71234567890123", tipo=6):
    return ComprobanteConocido("HOMOLOGACION", "20111111112", 3, tipo, numero, estado, codigo)


def _historia(*comprobantes, caeas=(), informes=()):
    return HistoriaFiscal(tuple(comprobantes), frozenset(caeas), frozenset(informes))


def test_historia_vacia_siempre_esta_cubierta():
    assert no_cubierta_por(_historia(), _historia()).vacia


def test_el_mismo_comprobante_en_el_mismo_estado_esta_cubierto():
    assert no_cubierta_por(_historia(_comprobante()), _historia(_comprobante())).vacia


def test_un_comprobante_ausente_del_objetivo_no_esta_cubierto():
    faltante = no_cubierta_por(_historia(_comprobante(1), _comprobante(2)), _historia(_comprobante(1)))
    assert faltante.comprobantes == (_comprobante(2),)


def test_mismo_numero_con_otro_codigo_de_autorizacion_no_esta_cubierto():
    actual = _historia(_comprobante(codigo="71234567890123"))
    falsificado = _historia(_comprobante(codigo="79999999999999"))
    assert not no_cubierta_por(actual, falsificado).vacia


def test_mismo_numero_de_otro_tipo_no_esta_cubierto():
    assert not no_cubierta_por(_historia(_comprobante(tipo=6)), _historia(_comprobante(tipo=1))).vacia


def test_un_estado_menos_resuelto_en_el_objetivo_no_cubre():
    # Restaurar un INCIERTO sobre un AUTORIZADO perdería el CAE local.
    actual = _historia(_comprobante(estado="AUTORIZADO"))
    objetivo = _historia(_comprobante(estado="INCIERTO", codigo=None))
    assert not no_cubierta_por(actual, objetivo).vacia


def test_un_estado_que_resuelve_al_actual_lo_cubre():
    actual = _historia(_comprobante(estado="INCIERTO", codigo=None))
    objetivo = _historia(_comprobante(estado="AUTORIZADO"))
    assert no_cubierta_por(actual, objetivo).vacia


def test_caea_informado_no_queda_cubierto_por_uno_pendiente_de_informar():
    actual = _historia(_comprobante(estado="CAEA_INFORMADO", codigo="31234567890123"))
    objetivo = _historia(_comprobante(estado="CAEA_PENDIENTE_INFORMAR", codigo="31234567890123"))
    assert not no_cubierta_por(actual, objetivo).vacia


def test_caea_otorgado_e_informe_sin_movimiento_ausentes_no_estan_cubiertos():
    caea = CaeaOtorgado("HOMOLOGACION", "20111111112", "31234567890123", 202611, 1)
    informe = InformeSinMovimiento("HOMOLOGACION", "31234567890123", 4)
    faltante = no_cubierta_por(_historia(caeas=[caea], informes=[informe]), _historia())
    assert faltante.caeas == frozenset({caea}) and faltante.informes == frozenset({informe})
    assert no_cubierta_por(_historia(caeas=[caea], informes=[informe]), _historia(caeas=[caea], informes=[informe])).vacia


def test_describir_resume_por_estado_sin_datos_sensibles():
    faltante = _historia(
        _comprobante(1),
        _comprobante(2),
        _comprobante(3, estado="INCIERTO", codigo=None),
        caeas=[CaeaOtorgado("HOMOLOGACION", "20111111112", "31234567890123", 202611, 1)],
    )
    texto = describir(faltante)
    assert "2 comprobante(s) autorizado(s)" in texto
    assert "1 comprobante(s) con resultado incierto" in texto
    assert "1 CAEA otorgado(s)" in texto
    assert "71234567890123" not in texto
