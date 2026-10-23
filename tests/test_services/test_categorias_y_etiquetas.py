"""V1.11-E: subcategorías, etiquetas y listado administrativo de productos (capa de servicios)."""

import pytest

from excepciones import (
    CategoriaNoEncontradaError,
    DatosInvalidosError,
    EtiquetaNoEncontradaError,
    NombreEtiquetaDuplicadoError,
)
from services import servicio_categorias, servicio_etiquetas, servicio_stock
from tests.utilidades_clientes import consultar, usuario_logueado


@pytest.fixture
def arbol(base_datos_temporal):
    alimentos = servicio_categorias.crear_categoria("Alimentos")
    panificados = servicio_categorias.crear_categoria("Panificados", alimentos.id)
    criollos = servicio_categorias.crear_categoria("Criollos", panificados.id)
    bebidas = servicio_categorias.crear_categoria("Bebidas")
    return type("A", (), {"ruta": base_datos_temporal, "alimentos": alimentos, "panificados": panificados,
                          "criollos": criollos, "bebidas": bebidas})


class TestSubcategorias:
    def test_crear_dentro_de_otra_y_ruta(self, arbol):
        assert arbol.criollos.categoria_padre_id == arbol.panificados.id
        assert servicio_categorias.obtener_arbol().ruta(arbol.criollos.id) == "Alimentos › Panificados › Criollos"

    def test_no_admite_un_cuarto_nivel(self, arbol):
        with pytest.raises(DatosInvalidosError, match="3 niveles"):
            servicio_categorias.crear_categoria("Medialunas", arbol.criollos.id)
        assert servicio_categorias.obtener_por_nombre("Medialunas") is None

    def test_mover_una_rama_completa(self, arbol):
        servicio_categorias.actualizar_categoria(arbol.panificados.id, "Panificados", arbol.bebidas.id)
        assert servicio_categorias.obtener_arbol().ruta(arbol.criollos.id) == "Bebidas › Panificados › Criollos"

    def test_no_se_mueve_dentro_de_su_propia_rama(self, arbol):
        with pytest.raises(DatosInvalidosError, match="sí misma"):
            servicio_categorias.actualizar_categoria(arbol.alimentos.id, "Alimentos", arbol.criollos.id)
        assert servicio_categorias.obtener_por_id(arbol.alimentos.id).categoria_padre_id is None

    def test_renombrar_sin_indicar_ubicacion_la_deja_donde_estaba(self, arbol):
        renombrada = servicio_categorias.actualizar_categoria(arbol.criollos.id, "Facturas")
        assert renombrada.nombre == "Facturas" and renombrada.categoria_padre_id == arbol.panificados.id

    def test_pasar_a_principal(self, arbol):
        movida = servicio_categorias.actualizar_categoria(arbol.criollos.id, "Criollos", None)
        assert movida.categoria_padre_id is None

    def test_opciones_de_ubicacion_excluyen_ciclos_y_exceso_de_niveles(self, arbol):
        para_nueva = [o.categoria.id for o in servicio_categorias.opciones_de_categoria_superior()]
        assert arbol.criollos.id not in para_nueva  # sería el cuarto nivel
        assert set(para_nueva) == {arbol.alimentos.id, arbol.panificados.id, arbol.bebidas.id}
        para_panificados = [o.categoria.id for o in servicio_categorias.opciones_de_categoria_superior(arbol.panificados.id)]
        assert para_panificados == [arbol.alimentos.id, arbol.bebidas.id]

    def test_no_se_da_de_baja_con_subcategorias_activas(self, arbol):
        with pytest.raises(DatosInvalidosError, match="Panificados"):
            servicio_categorias.eliminar_categoria(arbol.alimentos.id)
        assert servicio_categorias.obtener_por_id(arbol.alimentos.id).activa

    def test_baja_de_la_rama_de_abajo_hacia_arriba_y_reactivacion_de_arriba_hacia_abajo(self, arbol):
        assert servicio_categorias.eliminar_categoria(arbol.criollos.id) is False  # sin uso: física
        assert servicio_categorias.obtener_por_id(arbol.criollos.id) is None
        hija = servicio_categorias.crear_categoria("Facturas", arbol.panificados.id)
        servicio_categorias.eliminar_categoria(hija.id)
        assert servicio_categorias.obtener_por_id(hija.id) is None
        servicio_stock.registrar_producto("1", "Pan", 10, 20, categoria_id=arbol.panificados.id)
        assert servicio_categorias.eliminar_categoria(arbol.panificados.id) is True  # tiene un producto: lógica
        assert servicio_categorias.eliminar_categoria(arbol.alimentos.id) is True  # su hija inactiva la referencia
        with pytest.raises(DatosInvalidosError, match="Alimentos"):
            servicio_categorias.reactivar_categoria(arbol.panificados.id)
        servicio_categorias.reactivar_categoria(arbol.alimentos.id)
        assert servicio_categorias.reactivar_categoria(arbol.panificados.id).activa

    def test_no_se_crea_dentro_de_una_inactiva_ni_inexistente(self, arbol):
        servicio_stock.registrar_producto("1", "Agua", 10, 20, categoria_id=arbol.bebidas.id)
        servicio_categorias.eliminar_categoria(arbol.bebidas.id)
        with pytest.raises(DatosInvalidosError, match="dada de baja"):
            servicio_categorias.crear_categoria("Aguas", arbol.bebidas.id)
        with pytest.raises(DatosInvalidosError, match="no existe"):
            servicio_categorias.crear_categoria("Aguas", 999)

    def test_actualizar_inexistente(self, arbol):
        with pytest.raises(CategoriaNoEncontradaError):
            servicio_categorias.actualizar_categoria(999, "X")


class TestEtiquetas:
    def test_crud_y_nombre_normalizado_unico(self, base_datos_temporal):
        frio = servicio_etiquetas.crear_etiqueta("  Frío  ")
        assert frio.nombre == "Frío"
        with pytest.raises(NombreEtiquetaDuplicadoError):
            servicio_etiquetas.crear_etiqueta("FRÍO".replace("Í", "í"))
        with pytest.raises(DatosInvalidosError):
            servicio_etiquetas.crear_etiqueta("   ")
        with pytest.raises(DatosInvalidosError):
            servicio_etiquetas.crear_etiqueta("x" * 41)
        assert servicio_etiquetas.renombrar_etiqueta(frio.id, "Heladera").nombre == "Heladera"
        servicio_etiquetas.eliminar_etiqueta(frio.id)
        assert servicio_etiquetas.listar() == []
        with pytest.raises(EtiquetaNoEncontradaError):
            servicio_etiquetas.eliminar_etiqueta(frio.id)

    def test_alta_y_edicion_de_producto_con_etiquetas_auditadas(self, base_datos_temporal):
        owner, _ = usuario_logueado("OWNER", "duenio")
        frio = servicio_etiquetas.crear_etiqueta("Frío")
        oferta = servicio_etiquetas.crear_etiqueta("Oferta")
        producto = servicio_stock.registrar_producto("1", "Yogur", 10, 20, usuario_id=owner.id, etiqueta_ids=[frio.id])
        assert servicio_etiquetas.ids_de_producto(producto.id) == [frio.id]

        servicio_stock.actualizar_producto(
            producto.id, "1", "Yogur", 10, 20, 0, usuario_id=owner.id, etiqueta_ids=[oferta.id]
        )
        assert servicio_etiquetas.ids_de_producto(producto.id) == [oferta.id]
        resumenes = [f[0] for f in consultar(base_datos_temporal, "SELECT resumen FROM auditoria ORDER BY id")]
        assert resumenes[-1] == "Yogur: se modificaron las etiquetas"

        servicio_stock.actualizar_producto(producto.id, "1", "Yogur", 10, 20, 0, usuario_id=owner.id)
        assert servicio_etiquetas.ids_de_producto(producto.id) == [oferta.id]  # None no las toca
        servicio_stock.actualizar_producto(
            producto.id, "1", "Yogur", 10, 20, 0, usuario_id=owner.id, etiqueta_ids=[oferta.id]
        )
        assert len(consultar(base_datos_temporal, "SELECT 1 FROM auditoria")) == len(resumenes)  # sin cambios

    def test_etiqueta_inexistente_no_deja_nada_a_medias(self, base_datos_temporal):
        with pytest.raises(DatosInvalidosError, match="etiquetas"):
            servicio_stock.registrar_producto("1", "Yogur", 10, 20, stock_actual=5, etiqueta_ids=[999])
        assert consultar(base_datos_temporal, "SELECT COUNT(*) FROM productos")[0][0] == 0
        producto = servicio_stock.registrar_producto("1", "Yogur", 10, 20)
        with pytest.raises(DatosInvalidosError):
            servicio_stock.actualizar_producto(producto.id, "1", "Otro nombre", 10, 20, 0, etiqueta_ids=[999])
        assert servicio_stock.obtener_por_id(producto.id).nombre == "Yogur"

    def test_un_producto_sin_historia_con_etiquetas_se_borra_fisicamente(self, base_datos_temporal):
        frio = servicio_etiquetas.crear_etiqueta("Frío")
        producto = servicio_stock.registrar_producto("1", "Yogur", 10, 20, etiqueta_ids=[frio.id])
        assert servicio_stock.eliminar_producto(producto.id) is False
        assert servicio_etiquetas.contar_productos_por_etiqueta() == {}


class TestListadoAdministrativo:
    @pytest.fixture
    def catalogo(self, arbol):
        frio = servicio_etiquetas.crear_etiqueta("Frío")
        r = servicio_stock.registrar_producto
        productos = {
            "criollo": r("7790001", "Criollo", 10, 20, stock_actual=5, stock_minimo=10, categoria_id=arbol.criollos.id),
            "pan": r("7790002", "Pan lactal", 10, 20, stock_actual=0, categoria_id=arbol.panificados.id),
            "agua": r("7790003", "Agua", 10, 20, stock_actual=30, categoria_id=arbol.bebidas.id, etiqueta_ids=[frio.id]),
            "suelto": r("8880004", "Ñoquis", 10, 20, stock_actual=3),
        }
        return type("C", (), {**productos, "arbol": arbol, "frio": frio})

    def _nombres(self, **filtros):
        return [p.nombre for p in servicio_stock.listar_para_administracion(**filtros)]

    def test_categoria_incluye_subcategorias(self, catalogo):
        assert self._nombres(categoria_id=catalogo.arbol.alimentos.id) == ["Criollo", "Pan lactal"]
        assert self._nombres(categoria_id=catalogo.arbol.criollos.id) == ["Criollo"]

    def test_estado_de_stock(self, catalogo):
        assert self._nombres(estado_stock="critico") == ["Criollo"]
        assert self._nombres(estado_stock="sin_stock") == ["Pan lactal"]
        assert self._nombres(estado_stock="con_stock") == ["Agua", "Criollo", "Ñoquis"]
        assert len(self._nombres(estado_stock="cualquiera")) == 4

    def test_busqueda_por_nombre_sin_mayusculas_o_por_codigo(self, catalogo):
        assert self._nombres(texto="ñoq") == ["Ñoquis"]
        assert self._nombres(texto="7790003") == ["Agua"]
        assert self._nombres(texto="888") == ["Ñoquis"]

    def test_etiqueta_y_filtros_combinados(self, catalogo):
        assert self._nombres(etiqueta_id=catalogo.frio.id) == ["Agua"]
        assert self._nombres(etiqueta_id=catalogo.frio.id, estado_stock="sin_stock") == []
        assert self._nombres(texto="pan", categoria_id=catalogo.arbol.alimentos.id, estado_stock="sin_stock") == [
            "Pan lactal"
        ]

    def test_inactivos(self, catalogo):
        assert servicio_stock.eliminar_producto(catalogo.agua.id) is True  # su stock inicial lo conserva
        assert self._nombres(inactivos=True) == ["Agua"]
        assert "Agua" not in self._nombres()
