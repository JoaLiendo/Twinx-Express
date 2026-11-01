"""Dominio fiscal puro (V1.16-A): IVA desde precios finales, reglas fechadas, clase y receptor."""

from datetime import date
from decimal import Decimal

import pytest

from domain.cliente import Cliente
from domain.fiscal import reglas, tablas_arca
from domain.fiscal.comprobante import resolver_receptor
from domain.fiscal.emisor import PerfilFiscal
from domain.fiscal.iva import ImportesFiscales, LineaFiscal, TratamientoIva, calcular_importes
from excepciones import DatosInvalidosError, FiscalizacionBloqueadaError

IVA_21 = TratamientoIva("GRAVADO", 5)
IVA_105 = TratamientoIva("GRAVADO", 4)
IVA_0 = TratamientoIva("GRAVADO", 3)
EXENTO = TratamientoIva("EXENTO")
NO_GRAVADO = TratamientoIva("NO_GRAVADO")
HOY = date(2026, 11, 1)
UMBRAL = 1_000_000_000


def linea(precio: int, cantidad: int = 1, tratamiento: TratamientoIva | None = IVA_21, nombre: str = "Prod") -> LineaFiscal:
    return LineaFiscal(1, "779", nombre, cantidad, precio, precio * cantidad, tratamiento)


class TestTratamiento:
    def test_gravado_requiere_alicuota_conocida(self):
        with pytest.raises(DatosInvalidosError):
            TratamientoIva("GRAVADO")
        with pytest.raises(DatosInvalidosError):
            TratamientoIva("GRAVADO", 999)

    def test_exento_no_lleva_alicuota(self):
        with pytest.raises(DatosInvalidosError):
            TratamientoIva("EXENTO", 5)

    def test_texto_ida_y_vuelta(self):
        for opcion in (IVA_21, IVA_105, EXENTO, NO_GRAVADO):
            assert TratamientoIva.desde_texto(opcion.como_texto) == opcion
        with pytest.raises(DatosInvalidosError):
            TratamientoIva.desde_texto("GRAVADO:")
        with pytest.raises(DatosInvalidosError):
            TratamientoIva.desde_texto("")

    def test_etiquetas(self):
        assert IVA_21.etiqueta == "Gravado 21%"
        assert IVA_105.etiqueta == "Gravado 10,5%"
        assert IVA_0.etiqueta == "Gravado 0%"


class TestCalculoIva:
    def test_una_unidad_21(self):
        importes = calcular_importes([linea(12100)], "B")
        assert (importes.neto_centavos, importes.iva_centavos, importes.total_centavos) == (10000, 2100, 12100)
        assert importes.alicuotas[0].alicuota_iva_id == 5

    def test_cantidades(self):
        importes = calcular_importes([linea(12100, 3)], "A")
        assert (importes.neto_centavos, importes.iva_centavos) == (30000, 6300)

    def test_multiples_alicuotas_exento_y_no_gravado(self):
        lineas = [linea(12100), linea(11050, tratamiento=IVA_105), linea(500, tratamiento=EXENTO), linea(300, tratamiento=NO_GRAVADO)]
        importes = calcular_importes(lineas, "B")
        assert importes.exento_centavos == 500
        assert importes.no_gravado_centavos == 300
        assert [a.alicuota_iva_id for a in importes.alicuotas] == [4, 5]
        assert importes.total_centavos == 12100 + 11050 + 500 + 300
        assert importes.neto_centavos + importes.iva_centavos == 12100 + 11050

    def test_alicuota_cero_va_en_el_detalle_con_iva_cero(self):
        importes = calcular_importes([linea(1000, tratamiento=IVA_0)], "B")
        assert importes.alicuotas[0].importe_centavos == 0
        assert importes.neto_centavos == 1000

    @pytest.mark.parametrize("bruto", [1, 2, 3, 99, 100, 101, 121, 122, 12099, 12101, 99999, 123456789])
    def test_reconcilia_al_centavo_con_redondeo_explicable(self, bruto):
        importes = calcular_importes([linea(bruto)], "B")
        assert importes.neto_centavos + importes.iva_centavos == bruto
        exacto = Decimal(bruto) / Decimal("1.21")
        assert abs(Decimal(importes.neto_centavos) - exacto) <= Decimal("0.5")

    def test_redondeo_acumulado_es_por_alicuota_no_por_linea(self):
        # 3 líneas de $0,01: por línea darían neto 0 + IVA 1 cada una; por grupo, neto = round(3/1,21) = 2.
        importes = calcular_importes([linea(1), linea(1), linea(1)], "B")
        assert (importes.neto_centavos, importes.iva_centavos) == (2, 1)

    def test_barrido_de_totales_siempre_reconcilia(self):
        for bruto in range(1, 3000):
            for tratamiento in (IVA_21, IVA_105):
                importes = calcular_importes([linea(bruto, tratamiento=tratamiento)], "A")
                assert importes.total_centavos == bruto

    def test_clase_c_no_discrimina_ni_exige_clasificacion(self):
        importes = calcular_importes([linea(12100, tratamiento=None)], "C")
        assert (importes.neto_centavos, importes.iva_centavos, importes.alicuotas) == (12100, 0, ())

    def test_producto_sin_clasificar_bloquea_y_nombra_todos(self):
        with pytest.raises(FiscalizacionBloqueadaError) as error:
            calcular_importes([linea(100, tratamiento=None, nombre="Chicle"), linea(100, tratamiento=None, nombre="Agua"), linea(100)], "B")
        assert any("Chicle" in m for m in error.value.motivos)
        assert any("Agua" in m for m in error.value.motivos)

    def test_importes_inconsistentes_se_rechazan(self):
        with pytest.raises(DatosInvalidosError):
            ImportesFiscales(100, 0, 80, 0, 0, 21)
        with pytest.raises(DatosInvalidosError):
            ImportesFiscales(-1, 0, 0, 0, 0, -1)

    def test_linea_con_subtotal_manipulado(self):
        with pytest.raises(DatosInvalidosError):
            LineaFiscal(1, "x", "x", 2, 100, 199, IVA_21)


class TestUmbralConsumidorFinal:
    def test_un_centavo_debajo_igual_y_encima(self):
        assert not reglas.consumidor_final_requiere_identificacion(UMBRAL - 1, HOY)
        assert reglas.consumidor_final_requiere_identificacion(UMBRAL, HOY)
        assert reglas.consumidor_final_requiere_identificacion(UMBRAL + 1, HOY)

    def test_fecha_anterior_a_toda_regla_bloquea(self):
        with pytest.raises(FiscalizacionBloqueadaError):
            reglas.consumidor_final_requiere_identificacion(1, date(2020, 1, 1))

    def test_anonimo_debajo_del_umbral(self):
        receptor = resolver_receptor(None, UMBRAL - 1, HOY)
        assert (receptor.doc_tipo, receptor.doc_nro, receptor.condicion_iva_receptor_id) == (99, "0", 5)

    @pytest.mark.parametrize("importe", [UMBRAL, UMBRAL + 1])
    def test_anonimo_en_o_sobre_el_umbral_bloquea(self, importe):
        with pytest.raises(FiscalizacionBloqueadaError):
            resolver_receptor(None, importe, HOY)


class TestReceptor:
    def test_cliente_sin_condicion_fiscal_bloquea(self):
        with pytest.raises(FiscalizacionBloqueadaError, match="condición fiscal"):
            resolver_receptor(Cliente(nombre="Histórico", id=1), 100, HOY)

    def test_inscripto_sin_cuit_bloquea(self):
        cliente = Cliente(nombre="Juan", id=1, tipo_cliente="PERSONA", condicion_fiscal="RESPONSABLE_INSCRIPTO")
        with pytest.raises(FiscalizacionBloqueadaError, match="CUIT"):
            resolver_receptor(cliente, 100, HOY)

    def test_inscripto_con_cuit(self):
        cliente = Cliente(nombre="Juan", id=1, tipo_cliente="PERSONA", condicion_fiscal="RESPONSABLE_INSCRIPTO", cuit="20-11111111-2")
        receptor = resolver_receptor(cliente, 100, HOY)
        assert (receptor.doc_tipo, receptor.doc_nro, receptor.condicion_iva_receptor_id) == (80, "20111111112", 1)

    def test_persona_y_empresa_no_deciden_la_condicion(self):
        empresa_cf = Cliente(
            nombre="Club", id=2, tipo_cliente="EMPRESA", condicion_fiscal="EXENTO", razon_social="Club SA",
            cuit="30712345671", direccion="X 1", telefono="1", contacto="c",
        )
        receptor = resolver_receptor(empresa_cf, 100, HOY)
        assert receptor.condicion_iva == "EXENTO" and receptor.nombre == "Club SA"

    def test_consumidor_final_identificado_sin_cuit(self):
        cliente = Cliente(nombre="Ana", id=3, condicion_fiscal="CONSUMIDOR_FINAL")
        assert resolver_receptor(cliente, UMBRAL - 1, HOY).doc_tipo == 99
        with pytest.raises(FiscalizacionBloqueadaError):
            resolver_receptor(cliente, UMBRAL, HOY)


class TestClase:
    @pytest.mark.parametrize(
        "emisor, receptor, clase",
        [
            ("RESPONSABLE_INSCRIPTO", "RESPONSABLE_INSCRIPTO", "A"),
            ("RESPONSABLE_INSCRIPTO", "MONOTRIBUTO", "A"),
            ("RESPONSABLE_INSCRIPTO", "CONSUMIDOR_FINAL", "B"),
            ("RESPONSABLE_INSCRIPTO", "EXENTO", "B"),
            ("MONOTRIBUTO", "RESPONSABLE_INSCRIPTO", "C"),
            ("MONOTRIBUTO", "CONSUMIDOR_FINAL", "C"),
            ("EXENTO", "RESPONSABLE_INSCRIPTO", "C"),
        ],
    )
    def test_matriz(self, emisor, receptor, clase):
        assert reglas.resolver_clase(emisor, receptor) == clase

    def test_a_con_leyenda_ex_m_bloquea_en_vez_de_emitir_a(self):
        with pytest.raises(FiscalizacionBloqueadaError, match="5762"):
            reglas.resolver_clase("RESPONSABLE_INSCRIPTO", "RESPONSABLE_INSCRIPTO", "OPERACION_SUJETA_A_RETENCION")
        assert reglas.resolver_clase("RESPONSABLE_INSCRIPTO", "CONSUMIDOR_FINAL", "OPERACION_SUJETA_A_RETENCION") == "B"

    def test_tipos(self):
        assert reglas.tipo_comprobante("A", "PRINCIPAL") == tablas_arca.FACTURA_A == 1
        assert reglas.tipo_comprobante("B", "NOTA_CREDITO") == 8
        assert reglas.tipo_comprobante("C", "NOTA_CREDITO") == 13


class TestPerfil:
    def _perfil(self, **cambios):
        datos = dict(cuit="20-11111111-2", razon_social="Kiosco", condicion_iva="MONOTRIBUTO", domicilio="Calle 1")
        datos.update(cambios)
        return PerfilFiscal(**datos)

    def test_valida_cuit(self):
        with pytest.raises(DatosInvalidosError):
            self._perfil(cuit="20111111111")

    def test_caea_distinto_de_cae(self):
        with pytest.raises(DatosInvalidosError, match="distinto"):
            self._perfil(punto_venta_cae=3, punto_venta_caea=3)

    def test_punto_de_venta_no_validado_no_esta_listo(self):
        perfil = self._perfil(punto_venta_cae=3)
        assert not perfil.punto_venta_cae_listo
        assert self._perfil(punto_venta_cae=3, punto_venta_cae_validado="HOMOLOGACION").punto_venta_cae_listo

    def test_validacion_de_otro_ambiente_no_sirve(self):
        perfil = self._perfil(punto_venta_cae=3, punto_venta_cae_validado="PRODUCCION")
        assert not perfil.punto_venta_cae_listo
