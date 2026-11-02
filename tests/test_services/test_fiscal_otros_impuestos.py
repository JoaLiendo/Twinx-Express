"""V1.16-H: «Otros Impuestos Nacionales Indirectos» (RG ARCA/AFIP 5614/2024).

Twinx no calcula esos impuestos: donde rige la transparencia (A, B y C a consumidor final) solo fiscaliza
productos que el dueño confirmó sin ellos (NO_APLICA, que aporta $ 0,00). Un producto SIN_CLASIFICAR o que
REQUIERE_TRATAMIENTO bloquea ANTES de llamar a ARCA: no hay intento, ni número consumido, ni FECAESolicitar,
y la venta sigue registrada. La C a otro receptor: `TestAlcanceFacturaC`.
"""

import pytest

from db.conexion import obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import fiscal as repositorio_fiscal
from domain.fiscal import otros_impuestos
from domain.fiscal.documento import armar
from domain.fiscal.iva import LineaFiscal, calcular_importes
from domain.fiscal.otros_impuestos import NO_APLICA, REQUIERE_TRATAMIENTO, SIN_CLASIFICAR
from excepciones import (
    DatosInvalidosError,
    ErrorBaseDatos,
    FiscalizacionBloqueadaError,
    FiscalizacionNoDisponibleError,
    PermisoDenegadoError,
)
from services import servicio_stock
from services.fiscal import documentos, servicio_caea, servicio_facturacion, servicio_parametros, servicio_perfil
from tests import utilidades_fiscales as uf


@pytest.fixture
def owner(base_datos_temporal):
    return uf.crear_owner()


@pytest.fixture
def fiscal(caja_abierta, arca_falso):
    uf.guardar_perfil_validado()
    servicio_parametros.refrescar(arca_falso)
    arca_falso.llamadas.clear()
    return arca_falso


def _venta(otros=NO_APLICA, nombre=None, tratamiento=uf.IVA_21):
    return uf.vender([(uf.producto(12100, tratamiento, nombre=nombre, otros_impuestos=otros), 1)])


def _intentos_totales() -> int:
    with obtener_conexion() as conexion:
        return conexion.execute("SELECT COUNT(*) FROM intentos_fiscales").fetchone()[0]


def _afirmar_nada_enviado(arca, venta_id: int) -> None:
    """Invariantes 5, 6 y 7: ni número, ni intento ENVIANDO, ni FECAESolicitar; la venta sigue ACTIVA."""
    assert arca.llamadas == []
    assert arca.autorizaciones == 0
    assert _intentos_totales() == 0
    assert all(c.numero is None and c.estado != "ENVIANDO" for c in servicio_facturacion.comprobantes_de_venta(venta_id))
    with obtener_conexion() as conexion:
        assert conexion.execute("SELECT estado FROM ventas WHERE id = ?", (venta_id,)).fetchone()[0] == "ACTIVA"


class TestDominio:
    def _linea(self, otros, nombre="Prod"):
        return LineaFiscal(1, "779", nombre, 1, 12100, 12100, uf.IVA_21, otros)

    def test_por_defecto_sin_clasificar(self):
        assert LineaFiscal(1, "779", "x", 1, 100, 100, None).otros_impuestos == SIN_CLASIFICAR

    def test_clasificacion_invalida(self):
        with pytest.raises(DatosInvalidosError):
            self._linea("CERO")

    @pytest.mark.parametrize("clase", ["A", "B", "C"])
    def test_sin_clasificar_bloquea_en_toda_clase(self, clase):
        with pytest.raises(FiscalizacionBloqueadaError) as error:
            calcular_importes([self._linea(SIN_CLASIFICAR, "Cigarrillos")], clase)
        assert error.value.motivos == ["Clasificá los otros impuestos nacionales indirectos de «Cigarrillos»."]

    @pytest.mark.parametrize("clase", ["A", "B", "C"])
    def test_requiere_tratamiento_bloquea_y_explica(self, clase):
        with pytest.raises(FiscalizacionBloqueadaError) as error:
            calcular_importes([self._linea(NO_APLICA), self._linea(REQUIERE_TRATAMIENTO, "Cerveza")], clase)
        assert error.value.motivos == [f"«Cerveza»: {otros_impuestos.MENSAJE_REQUIERE_TRATAMIENTO}"]

    def test_no_aplica_permite_y_no_informa_tributos(self):
        importes = calcular_importes([self._linea(NO_APLICA)], "B")
        assert importes.tributos_centavos == 0 and importes.iva_centavos == 2100

    def test_iva_y_otros_pendientes_se_informan_juntos(self):
        linea = LineaFiscal(1, "779", "Alfajor", 1, 100, 100, None, SIN_CLASIFICAR)
        with pytest.raises(FiscalizacionBloqueadaError) as error:
            calcular_importes([linea], "B")
        assert len(error.value.motivos) == 2

    def test_importe_solo_si_todas_confirmadas(self):
        assert otros_impuestos.importe_centavos([NO_APLICA, NO_APLICA]) == 0
        assert otros_impuestos.importe_centavos([NO_APLICA, SIN_CLASIFICAR]) is None
        assert otros_impuestos.importe_centavos([]) is None


class TestClasificacion:
    def test_producto_nuevo_nace_sin_clasificar(self, base_datos_temporal):
        """Invariante 1 (productos nuevos sin decisión del dueño); los históricos: `test_migracion_045`."""
        producto = servicio_stock.registrar_producto("7791234500001", "Nuevo", 100, 200)
        assert servicio_perfil.obtener_otros_impuestos(producto.id) == SIN_CLASIFICAR

    def test_owner_clasifica_y_queda_auditado(self, owner):
        producto = uf.producto(otros_impuestos=SIN_CLASIFICAR)
        assert servicio_perfil.clasificar_otros_impuestos({producto.id: REQUIERE_TRATAMIENTO}, owner) == 1
        assert servicio_perfil.clasificar_otros_impuestos({producto.id: REQUIERE_TRATAMIENTO}, owner) == 0
        assert servicio_perfil.clasificar_otros_impuestos({producto.id: SIN_CLASIFICAR}, owner) == 1
        assert servicio_perfil.obtener_otros_impuestos(producto.id) == SIN_CLASIFICAR
        acciones = [e for e in repositorio_auditoria.listar(limite=50) if e.accion == "PRODUCTO_OTROS_IMPUESTOS_CLASIFICADO"]
        assert len(acciones) == 2 and any("Sin clasificar -> Requiere tratamiento especial" in e.resumen for e in acciones)

    def test_cashier_no_puede_cambiar_la_clasificacion(self, owner):
        """Invariante 8."""
        producto = uf.producto(otros_impuestos=REQUIERE_TRATAMIENTO)
        with pytest.raises(PermisoDenegadoError):
            servicio_perfil.clasificar_otros_impuestos({producto.id: NO_APLICA}, uf.crear_cashier())
        assert servicio_perfil.obtener_otros_impuestos(producto.id) == REQUIERE_TRATAMIENTO

    def test_valor_invalido_no_guarda_nada(self, owner):
        a, b = uf.producto(otros_impuestos=SIN_CLASIFICAR), uf.producto(otros_impuestos=SIN_CLASIFICAR)
        with pytest.raises(DatosInvalidosError):
            servicio_perfil.clasificar_otros_impuestos({a.id: NO_APLICA, b.id: "CERO"}, owner)
        assert servicio_perfil.obtener_otros_impuestos(a.id) == SIN_CLASIFICAR

    def test_la_base_no_acepta_otro_valor(self, base_datos_temporal):
        producto = uf.producto(otros_impuestos=SIN_CLASIFICAR)
        with pytest.raises(ErrorBaseDatos):
            with obtener_conexion() as conexion:
                conexion.execute(
                    "INSERT INTO productos_otros_impuestos (producto_id, clasificacion) VALUES (?, 'CERO')", (producto.id,)
                )


class TestBloqueoAntesDeARCA:
    @pytest.mark.parametrize("clasificacion, texto", [
        (SIN_CLASIFICAR, "Clasificá los otros impuestos nacionales indirectos de «Tabaco H»"),
        (REQUIERE_TRATAMIENTO, otros_impuestos.MENSAJE_REQUIERE_TRATAMIENTO),
    ])
    def test_bloquea_sin_crear_comprobante_ni_llamar_a_arca(self, fiscal, owner, clasificacion, texto):
        """Invariantes 2, 3, 5, 6 y 7."""
        venta = _venta(clasificacion, nombre="Tabaco H")
        with pytest.raises(FiscalizacionBloqueadaError) as error:
            servicio_facturacion.fiscalizar_venta(venta.id, owner)
        assert any(texto in motivo for motivo in error.value.motivos)
        assert servicio_facturacion.comprobantes_de_venta(venta.id) == []
        _afirmar_nada_enviado(fiscal, venta.id)

    def test_una_linea_bloqueada_bloquea_toda_la_venta(self, fiscal, owner):
        venta = uf.vender([(uf.producto(12100, uf.IVA_21), 1), (uf.producto(5000, uf.IVA_21, otros_impuestos=REQUIERE_TRATAMIENTO), 1)])
        with pytest.raises(FiscalizacionBloqueadaError):
            servicio_facturacion.fiscalizar_venta(venta.id, owner)
        _afirmar_nada_enviado(fiscal, venta.id)

    def test_clase_c_tambien_bloquea(self, caja_abierta, arca_falso, owner):
        uf.guardar_perfil_validado("MONOTRIBUTO")
        servicio_parametros.refrescar(arca_falso)
        arca_falso.llamadas.clear()
        venta = _venta(SIN_CLASIFICAR, tratamiento=None)
        with pytest.raises(FiscalizacionBloqueadaError):
            servicio_facturacion.fiscalizar_venta(venta.id, owner)
        _afirmar_nada_enviado(arca_falso, venta.id)

    def test_no_aplica_permite_continuar(self, fiscal, owner):
        """Invariantes 4 y 11: autoriza, el snapshot congela NO_APLICA y ARCA recibe ImpTrib 0 sin tributos."""
        venta = _venta(NO_APLICA)
        comprobante = servicio_facturacion.fiscalizar_venta(venta.id, owner)
        assert comprobante.estado == "AUTORIZADO"
        assert [linea.otros_impuestos for linea in comprobante.lineas] == [NO_APLICA]
        enviada = fiscal.registro(3, 6, comprobante.numero).solicitud
        assert enviada.importes.tributos_centavos == 0 and enviada.como_dict()["ImpTrib"] == 0

    def test_cambio_posterior_a_requiere_tratamiento_bloquea_el_reintento(self, fiscal, owner):
        """Un PENDIENTE (ARCA no respondía) se revalida antes de volver a tocar ARCA."""
        producto = uf.producto(12100, uf.IVA_21)
        venta = uf.vender([(producto, 1)])
        fiscal.caido = True
        with pytest.raises(FiscalizacionNoDisponibleError):
            servicio_facturacion.fiscalizar_venta(venta.id, owner)
        pendiente = servicio_facturacion.comprobantes_de_venta(venta.id)[0]
        assert pendiente.estado == "PENDIENTE"
        servicio_perfil.clasificar_otros_impuestos({producto.id: REQUIERE_TRATAMIENTO}, owner)
        fiscal.caido = False
        fiscal.llamadas.clear()
        intentos = _intentos_totales()
        with pytest.raises(FiscalizacionBloqueadaError, match="todavía no calcula"):
            servicio_facturacion.reintentar(pendiente.id, owner)
        assert fiscal.llamadas == [] and _intentos_totales() == intentos
        assert servicio_facturacion.obtener(pendiente.id).estado == "PENDIENTE"

    def test_pendiente_sin_clasificacion_en_el_snapshot_no_se_envia(self, fiscal, owner):
        """Factura preparada antes de V1.16-H (líneas sin el dato): se descarta y se vuelve a fiscalizar."""
        venta = _venta(NO_APLICA)
        with obtener_conexion() as conexion:
            conexion.execute("DROP TRIGGER trg_comprobante_fiscal_lineas_otros_impuestos")
        nuevo = uf.nuevo_comprobante_para_venta(venta.id)
        legado = uf.insertar(
            repositorio_fiscal.NuevoComprobante(
                **{**nuevo.__dict__, "lineas": tuple(
                    LineaFiscal(**{**linea.__dict__, "otros_impuestos": SIN_CLASIFICAR}) for linea in nuevo.lineas
                )}
            )
        )
        with pytest.raises(FiscalizacionBloqueadaError, match="descartala"):
            servicio_facturacion.fiscalizar_venta(venta.id, owner)
        _afirmar_nada_enviado(fiscal, venta.id)
        servicio_facturacion.descartar(legado, owner)
        assert servicio_facturacion.fiscalizar_venta(venta.id, owner).estado == "AUTORIZADO"

    def test_la_base_rechaza_una_factura_con_lineas_sin_confirmar(self, fiscal):
        """Defensa en profundidad: aunque el código fallara, la factura no nace."""
        venta = _venta(NO_APLICA)
        nuevo = uf.nuevo_comprobante_para_venta(venta.id)
        lineas = tuple(LineaFiscal(**{**linea.__dict__, "otros_impuestos": SIN_CLASIFICAR}) for linea in nuevo.lineas)
        with pytest.raises(ErrorBaseDatos):
            uf.insertar(repositorio_fiscal.NuevoComprobante(**{**nuevo.__dict__, "lineas": lineas}))
        requiere = tuple(LineaFiscal(**{**linea.__dict__, "otros_impuestos": REQUIERE_TRATAMIENTO}) for linea in nuevo.lineas)
        with pytest.raises(ErrorBaseDatos):
            uf.insertar(repositorio_fiscal.NuevoComprobante(**{**nuevo.__dict__, "lineas": requiere}))
        assert servicio_facturacion.comprobantes_de_venta(venta.id) == []


class TestContingenciaCAEA:
    @pytest.fixture
    def contingencia(self, caja_abierta, arca_falso, owner):
        uf.guardar_perfil_validado(punto_venta_caea=4, contingencia_caea=True)
        servicio_parametros.refrescar(arca_falso)
        servicio_caea.solicitar(owner)
        arca_falso.caido = True
        return arca_falso

    @pytest.mark.parametrize("clasificacion", [SIN_CLASIFICAR, REQUIERE_TRATAMIENTO])
    def test_bajo_caea_tampoco_se_emite(self, contingencia, owner, clasificacion):
        venta = _venta(clasificacion)
        with pytest.raises(FiscalizacionBloqueadaError):
            servicio_caea.emitir_bajo_caea(venta.id, owner)
        assert servicio_facturacion.comprobantes_de_venta(venta.id) == []

    def test_bajo_caea_con_no_aplica(self, contingencia, owner):
        comprobante = servicio_caea.emitir_bajo_caea(_venta(NO_APLICA).id, owner)
        assert comprobante.estado == "CAEA_PENDIENTE_INFORMAR"
        assert ("Otros Impuestos Nacionales Indirectos", "$ 0,00") in armar(comprobante).transparencia


class TestDocumentos:
    def test_factura_b_muestra_iva_contenido_y_otros_impuestos_en_cero(self, fiscal, owner):
        """Invariantes 10 y 11, en el contenido compartido por A4, 80 mm y PDF."""
        comprobante = servicio_facturacion.fiscalizar_venta(_venta(NO_APLICA).id, owner)
        documento = armar(comprobante)
        assert documento.transparencia == [("IVA Contenido", "$ 21,00"), ("Otros Impuestos Nacionales Indirectos", "$ 0,00")]
        assert b"Otros Impuestos Nacionales Indirectos: $ 0,00" in documentos.pdf(documento)
        assert b"IVA Contenido: $ 21,00" in documentos.pdf(documento)

    def test_factura_a_no_cambia(self, fiscal, owner):
        from tests.test_services.test_fiscal_cae import _cliente

        cliente = _cliente(nombre="Distribuidora", tipo_cliente="PERSONA", condicion_fiscal="RESPONSABLE_INSCRIPTO", cuit=uf.CUIT_RECEPTOR_RI)
        venta = uf.vender([(uf.producto(12100, uf.IVA_21), 1)], cliente_id=cliente)
        documento = armar(servicio_facturacion.fiscalizar_venta(venta.id, owner))
        assert documento.letra == "A" and documento.transparencia == []

    def test_autorizado_no_cambia_si_despues_cambia_la_clasificacion(self, fiscal, owner):
        """Invariante 9: el documento sale del snapshot, nunca de la clasificación actual."""
        producto = uf.producto(12100, uf.IVA_21)
        comprobante = servicio_facturacion.fiscalizar_venta(uf.vender([(producto, 1)]).id, owner)
        antes = armar(comprobante)
        servicio_perfil.clasificar_otros_impuestos({producto.id: REQUIERE_TRATAMIENTO}, owner)
        despues = servicio_facturacion.obtener(comprobante.id)
        assert despues.lineas == comprobante.lineas and armar(despues) == antes
        with pytest.raises(ErrorBaseDatos):
            with obtener_conexion() as conexion:
                conexion.execute("UPDATE comprobante_fiscal_lineas SET otros_impuestos_nacionales = NULL")

    def test_nota_de_credito_replica_la_factura(self, fiscal, owner):
        from services.fiscal import servicio_notas

        venta = _venta(NO_APLICA)
        servicio_facturacion.fiscalizar_venta(venta.id, owner)
        nota = servicio_notas.emitir_nota_credito_total(venta.id, "ERROR_CARGA", None, owner)
        assert [linea.otros_impuestos for linea in nota.lineas] == [NO_APLICA]
        assert ("Otros Impuestos Nacionales Indirectos", "$ 0,00") in armar(
            nota, servicio_facturacion.obtener(nota.comprobante_asociado_id)
        ).transparencia

    def test_factura_b_anonima_a_consumidor_final(self, fiscal, owner):
        """Test 10: la venta anónima es consumidor final y su documento lleva el régimen completo."""
        comprobante = servicio_facturacion.fiscalizar_venta(_venta(NO_APLICA).id, owner)
        documento = armar(comprobante)
        assert comprobante.receptor.es_consumidor_final and "A CONSUMIDOR FINAL" in documento.leyendas
        assert [etiqueta for etiqueta, _ in documento.transparencia] == ["IVA Contenido", "Otros Impuestos Nacionales Indirectos"]


class TestAlcanceFacturaC:
    """Corrección del alcance de V1.16-H (migración 046): la transparencia depende de la operación (art. 99
    Ley 27.743: consumidores finales), no de la letra. En la C, SIN_CLASIFICAR bloquea solo a consumidor
    final; REQUIERE_TRATAMIENTO bloquea con cualquier receptor porque Twinx no sabe calcular ese tributo."""

    @pytest.fixture
    def monotributo(self, caja_abierta, arca_falso):
        uf.guardar_perfil_validado("MONOTRIBUTO")
        servicio_parametros.refrescar(arca_falso)
        arca_falso.llamadas.clear()
        return arca_falso

    @staticmethod
    def _cliente_inscripto() -> int:
        from tests.test_services.test_fiscal_cae import _cliente

        return _cliente(nombre="Distribuidora", tipo_cliente="PERSONA", condicion_fiscal="RESPONSABLE_INSCRIPTO",
                        cuit=uf.CUIT_RECEPTOR_RI)

    def _venta_c(self, otros, a_inscripto: bool):
        producto = uf.producto(12100, None, nombre="Cigarrillos", otros_impuestos=otros)
        return producto, uf.vender([(producto, 1)], cliente_id=self._cliente_inscripto() if a_inscripto else None)

    @staticmethod
    def _linea(otros):
        return LineaFiscal(1, "779", "Cigarrillos", 1, 12100, 12100, None, otros)

    # --- dominio ---

    @pytest.mark.parametrize("otros", [SIN_CLASIFICAR, REQUIERE_TRATAMIENTO])
    def test_dominio_c_a_consumidor_final_bloquea(self, otros):
        with pytest.raises(FiscalizacionBloqueadaError):
            calcular_importes([self._linea(otros)], "C", receptor_consumidor_final=True)

    def test_dominio_c_a_otro_receptor_sin_clasificar_permite(self):
        importes = calcular_importes([self._linea(SIN_CLASIFICAR)], "C", receptor_consumidor_final=False)
        assert (importes.total_centavos, importes.tributos_centavos) == (12100, 0)

    def test_dominio_c_a_otro_receptor_requiere_tratamiento_bloquea_por_calculo(self):
        with pytest.raises(FiscalizacionBloqueadaError, match="todavía no calcula") as error:
            calcular_importes([self._linea(SIN_CLASIFICAR), self._linea(REQUIERE_TRATAMIENTO)], "C",
                              receptor_consumidor_final=False)
        assert error.value.motivos == [f"«Cigarrillos»: {otros_impuestos.MENSAJE_REQUIERE_TRATAMIENTO}"]

    @pytest.mark.parametrize("clase", ["A", "B"])
    def test_dominio_a_y_b_no_cambian(self, clase):
        linea = LineaFiscal(1, "779", "Cigarrillos", 1, 12100, 12100, uf.IVA_21, SIN_CLASIFICAR)
        with pytest.raises(FiscalizacionBloqueadaError):
            calcular_importes([linea], clase, receptor_consumidor_final=False)

    # --- CAE ---

    @pytest.mark.parametrize("otros", [SIN_CLASIFICAR, REQUIERE_TRATAMIENTO])
    def test_c_a_consumidor_final_anonimo_bloquea_sin_tocar_arca(self, monotributo, owner, otros):
        """Tests 1, 2, 6 y 9: la venta anónima es consumidor final."""
        _, venta = self._venta_c(otros, a_inscripto=False)
        with pytest.raises(FiscalizacionBloqueadaError):
            servicio_facturacion.fiscalizar_venta(venta.id, owner)
        assert servicio_facturacion.comprobantes_de_venta(venta.id) == []
        _afirmar_nada_enviado(monotributo, venta.id)

    @pytest.mark.parametrize("condicion_emisor", ["MONOTRIBUTO", "EXENTO"])
    def test_c_a_consumidor_final_no_aplica_permite_y_muestra_otros_impuestos(
        self, caja_abierta, arca_falso, owner, condicion_emisor
    ):
        """Test 3 y 10: el documento (A4, 80 mm y PDF) muestra los Otros Impuestos Nacionales Indirectos y no
        afirma un IVA Contenido (la discriminación del IVA es del emisor responsable inscripto)."""
        uf.guardar_perfil_validado(condicion_emisor)
        servicio_parametros.refrescar(arca_falso)
        _, venta = self._venta_c(NO_APLICA, a_inscripto=False)
        comprobante = servicio_facturacion.fiscalizar_venta(venta.id, owner)
        assert (comprobante.estado, comprobante.clase, comprobante.receptor.condicion_iva_receptor_id) == ("AUTORIZADO", "C", 5)
        assert [linea.otros_impuestos for linea in comprobante.lineas] == [NO_APLICA]
        documento = armar(comprobante)
        assert documento.transparencia == [("Otros Impuestos Nacionales Indirectos", "$ 0,00")]
        pdf = documentos.pdf(documento)
        assert b"Otros Impuestos Nacionales Indirectos: $ 0,00" in pdf and b"IVA Contenido" not in pdf

    def test_c_a_inscripto_sin_clasificar_no_se_bloquea_por_transparencia(self, monotributo, owner):
        """Test 4: el snapshot no inventa NO_APLICA, ImpTrib 0 sin tributos y el documento no afirma transparencia."""
        _, venta = self._venta_c(SIN_CLASIFICAR, a_inscripto=True)
        comprobante = servicio_facturacion.fiscalizar_venta(venta.id, owner)
        assert (comprobante.estado, comprobante.clase, comprobante.receptor.condicion_iva_receptor_id) == ("AUTORIZADO", "C", 1)
        assert [linea.otros_impuestos for linea in comprobante.lineas] == [SIN_CLASIFICAR]
        assert monotributo.registro(3, 11, comprobante.numero).solicitud.como_dict()["ImpTrib"] == 0
        assert armar(comprobante).transparencia == []

    def test_c_a_inscripto_requiere_tratamiento_bloquea_sin_tocar_arca(self, monotributo, owner):
        """Test 5: invariante de cálculo (Twinx no sabe calcular el tributo), no de transparencia."""
        _, venta = self._venta_c(REQUIERE_TRATAMIENTO, a_inscripto=True)
        with pytest.raises(FiscalizacionBloqueadaError, match="todavía no calcula"):
            servicio_facturacion.fiscalizar_venta(venta.id, owner)
        _afirmar_nada_enviado(monotributo, venta.id)

    # --- reintento de un PENDIENTE (test 8) ---

    def _pendiente(self, arca, owner, otros, a_inscripto):
        producto, venta = self._venta_c(otros, a_inscripto)
        arca.caido = True
        with pytest.raises(FiscalizacionNoDisponibleError):
            servicio_facturacion.fiscalizar_venta(venta.id, owner)
        arca.caido = False
        arca.llamadas.clear()
        return producto, servicio_facturacion.comprobantes_de_venta(venta.id)[0]

    def test_reintento_c_a_inscripto_revalida_con_el_mismo_alcance(self, monotributo, owner):
        producto, pendiente = self._pendiente(monotributo, owner, SIN_CLASIFICAR, a_inscripto=True)
        assert pendiente.estado == "PENDIENTE"
        servicio_perfil.clasificar_otros_impuestos({producto.id: REQUIERE_TRATAMIENTO}, owner)
        intentos = _intentos_totales()
        with pytest.raises(FiscalizacionBloqueadaError, match="todavía no calcula"):
            servicio_facturacion.reintentar(pendiente.id, owner)
        assert monotributo.llamadas == [] and _intentos_totales() == intentos
        servicio_perfil.clasificar_otros_impuestos({producto.id: SIN_CLASIFICAR}, owner)
        assert servicio_facturacion.reintentar(pendiente.id, owner).estado == "AUTORIZADO"

    def test_reintento_c_a_consumidor_final_revalida(self, monotributo, owner):
        producto, pendiente = self._pendiente(monotributo, owner, NO_APLICA, a_inscripto=False)
        servicio_perfil.clasificar_otros_impuestos({producto.id: SIN_CLASIFICAR}, owner)
        with pytest.raises(FiscalizacionBloqueadaError, match="Clasificá"):
            servicio_facturacion.reintentar(pendiente.id, owner)
        assert monotributo.llamadas == [] and servicio_facturacion.obtener(pendiente.id).estado == "PENDIENTE"

    # --- CAEA (test 7) ---

    @pytest.fixture
    def contingencia_c(self, caja_abierta, arca_falso, owner):
        uf.guardar_perfil_validado("MONOTRIBUTO", punto_venta_caea=4, contingencia_caea=True)
        servicio_parametros.refrescar(arca_falso)
        servicio_caea.solicitar(owner)
        arca_falso.caido = True
        return arca_falso

    @pytest.mark.parametrize("otros, a_inscripto", [
        (SIN_CLASIFICAR, False), (REQUIERE_TRATAMIENTO, False), (REQUIERE_TRATAMIENTO, True),
    ])
    def test_caea_bloquea_igual_que_cae(self, contingencia_c, owner, otros, a_inscripto):
        _, venta = self._venta_c(otros, a_inscripto)
        with pytest.raises(FiscalizacionBloqueadaError):
            servicio_caea.emitir_bajo_caea(venta.id, owner)
        assert servicio_facturacion.comprobantes_de_venta(venta.id) == []

    def test_caea_c_a_inscripto_sin_clasificar_se_emite(self, contingencia_c, owner):
        _, venta = self._venta_c(SIN_CLASIFICAR, a_inscripto=True)
        comprobante = servicio_caea.emitir_bajo_caea(venta.id, owner)
        assert (comprobante.estado, comprobante.clase) == ("CAEA_PENDIENTE_INFORMAR", "C")
        assert [linea.otros_impuestos for linea in comprobante.lineas] == [SIN_CLASIFICAR]

    # --- base de datos (trigger de la 046) ---

    def test_la_base_acepta_c_a_inscripto_sin_clasificar_pero_no_a_consumidor_final(self, monotributo):
        from datetime import date

        from domain.fiscal.comprobante import resolver_receptor

        cliente_id = self._cliente_inscripto()
        venta = uf.vender([(uf.producto(12100, None, otros_impuestos=SIN_CLASIFICAR), 1)], cliente_id=cliente_id)
        a_inscripto = uf.nuevo_comprobante_para_venta(
            venta.id, clase="C", receptor=self._receptor_inscripto(cliente_id, venta.total_centavos)
        )
        a_consumidor_final = repositorio_fiscal.NuevoComprobante(
            **{**a_inscripto.__dict__, "receptor": resolver_receptor(None, venta.total_centavos, date.today())}
        )
        with pytest.raises(ErrorBaseDatos):
            uf.insertar(a_consumidor_final)
        assert servicio_facturacion.comprobantes_de_venta(venta.id) == []
        comprobante_id = uf.insertar(a_inscripto)
        assert [linea.otros_impuestos for linea in repositorio_fiscal.obtener_comprobante(comprobante_id).lineas] == [SIN_CLASIFICAR]

    @staticmethod
    def _receptor_inscripto(cliente_id: int, total_centavos: int):
        from datetime import date

        from db.repositorios import clientes as repositorio_clientes
        from domain.fiscal.comprobante import resolver_receptor

        with obtener_conexion() as conexion:
            cliente = repositorio_clientes.obtener_por_id_en_conexion(conexion, cliente_id)
        return resolver_receptor(cliente, total_centavos, date.today())
