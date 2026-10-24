"""V1.13-A: clasificación de proveedores por categorías del catálogo y datos fiscales.

Contra una base SQLite temporal real (ver tests/conftest.py)."""

import pytest

from domain.compra import ItemCompra
from domain.proveedor import normalizar_cuit
from excepciones import (
    CuitProveedorDuplicadoError,
    DatosInvalidosError,
    ErrorBaseDatos,
)
from services import servicio_categorias, servicio_compras, servicio_proveedores
from services.servicio_proveedores import DatosFiscales
from tests.utilidades_clientes import consultar, crear_owner, crear_producto, escribir

CUIT_A = "30-71234567-1"
CUIT_B = "20-12345678-6"


@pytest.fixture
def cat(base_datos_temporal):
    bebidas = servicio_categorias.crear_categoria("Bebidas")
    gaseosas = servicio_categorias.crear_categoria("Gaseosas", bebidas.id)
    golosinas = servicio_categorias.crear_categoria("Golosinas")
    limpieza = servicio_categorias.crear_categoria("Limpieza")
    return type("Cat", (), {"ruta": base_datos_temporal, "bebidas": bebidas, "gaseosas": gaseosas,
                            "golosinas": golosinas, "limpieza": limpieza})


def _ids(proveedor_id: int) -> set[int]:
    return servicio_proveedores.ids_categorias(proveedor_id)


# --- CUIT --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("texto", ["30-71234567-1", "30712345671", " 30.71234567.1 ", "30 71234567 1"])
def test_normalizar_cuit_acepta_separadores(texto):
    assert normalizar_cuit(texto) == "30712345671"


@pytest.mark.parametrize("texto", ["30-71234567-2", "3071234567", "307123456712", "30-7123456A-1", "٣0712345671"])
def test_normalizar_cuit_rechaza_formato_o_verificador(texto):
    with pytest.raises(DatosInvalidosError):
        normalizar_cuit(texto)


@pytest.mark.parametrize("texto", [None, "", "  ", "--"])
def test_cuit_vacio_es_none(texto):
    assert normalizar_cuit(texto) is None


def test_crear_con_datos_fiscales_y_varias_categorias(cat):
    proveedor = servicio_proveedores.crear_proveedor(
        "Distribuidora X",
        fiscales=DatosFiscales(
            razon_social="  Distribuidora X SRL ", cuit=CUIT_A, condicion_fiscal="RESPONSABLE_INSCRIPTO",
            ingresos_brutos="901-123456-7", convenio_multilateral=True,
        ),
        categoria_ids=[cat.bebidas.id, cat.golosinas.id, cat.limpieza.id],
    )

    assert (proveedor.razon_social, proveedor.cuit, proveedor.cuit_formateado) == (
        "Distribuidora X SRL", "30712345671", CUIT_A
    )
    assert proveedor.condicion_fiscal_etiqueta == "Responsable inscripto"
    assert proveedor.ingresos_brutos == "901-123456-7" and proveedor.convenio_multilateral is True
    assert _ids(proveedor.id) == {cat.bebidas.id, cat.golosinas.id, cat.limpieza.id}
    ficha = servicio_proveedores.obtener_ficha(proveedor.id)
    assert [c.ruta for c in ficha.categorias] == ["Bebidas", "Golosinas", "Limpieza"]


def test_proveedor_sin_cuit_ni_datos_fiscales_y_varios_sin_cuit_conviven(base_datos_temporal):
    a = servicio_proveedores.crear_proveedor("A")
    b = servicio_proveedores.crear_proveedor("B", fiscales=DatosFiscales(cuit="  "))

    assert a.cuit is None and b.cuit is None and a.condicion_fiscal is None and not a.convenio_multilateral


def test_cuit_duplicado_se_rechaza_incluso_contra_un_proveedor_dado_de_baja(base_datos_temporal):
    owner = crear_owner()
    viejo = servicio_proveedores.crear_proveedor("Viejo", fiscales=DatosFiscales(cuit=CUIT_A))
    servicio_compras.registrar_compra(viejo.id, owner.id, [ItemCompra(crear_producto().id, 1, 10)])
    assert servicio_proveedores.eliminar_proveedor(viejo.id) is True

    with pytest.raises(CuitProveedorDuplicadoError):
        servicio_proveedores.crear_proveedor("Nuevo", fiscales=DatosFiscales(cuit="30712345671"))
    otro = servicio_proveedores.crear_proveedor("Otro", fiscales=DatosFiscales(cuit=CUIT_B))
    with pytest.raises(CuitProveedorDuplicadoError):
        servicio_proveedores.actualizar_proveedor(otro.id, "Otro", fiscales=DatosFiscales(cuit=CUIT_A))
    assert servicio_proveedores.reactivar_proveedor(viejo.id).cuit == "30712345671"


def test_cuit_invalido_o_condicion_inexistente_no_llegan_a_la_base(base_datos_temporal):
    with pytest.raises(DatosInvalidosError):
        servicio_proveedores.crear_proveedor("A", fiscales=DatosFiscales(cuit="30-71234567-9"))
    with pytest.raises(DatosInvalidosError):
        servicio_proveedores.crear_proveedor("A", fiscales=DatosFiscales(condicion_fiscal="INVENTADA"))
    assert servicio_proveedores.listar_todos() == []


def test_la_base_rechaza_un_cuit_mal_formado_o_una_condicion_invalida_escritos_por_fuera(cat):
    with pytest.raises(Exception, match="CHECK"):
        escribir(cat.ruta, "INSERT INTO proveedores (nombre, cuit) VALUES ('X', '30-7123456')")
    with pytest.raises(Exception, match="CHECK"):
        escribir(cat.ruta, "INSERT INTO proveedores (nombre, condicion_fiscal) VALUES ('X', 'LIBRE')")


def test_editar_conserva_el_cuit_historico_y_permite_borrarlo(base_datos_temporal):
    proveedor = servicio_proveedores.crear_proveedor("A", fiscales=DatosFiscales(cuit=CUIT_A))
    sin_cambios = servicio_proveedores.actualizar_proveedor(proveedor.id, "A", fiscales=DatosFiscales(cuit=CUIT_A))
    sin_cuit = servicio_proveedores.actualizar_proveedor(proveedor.id, "A")

    assert sin_cambios.cuit == "30712345671"
    assert sin_cuit.cuit is None


# --- categorías -----------------------------------------------------------------------------------


def test_editar_quita_un_vinculo_sin_borrar_la_categoria(cat):
    proveedor = servicio_proveedores.crear_proveedor("A", categoria_ids=[cat.bebidas.id, cat.limpieza.id])

    servicio_proveedores.actualizar_proveedor(proveedor.id, "A", categoria_ids=[cat.bebidas.id])

    assert _ids(proveedor.id) == {cat.bebidas.id}
    assert servicio_categorias.obtener_por_id(cat.limpieza.id).activa


def test_sin_categoria_ids_la_edicion_no_toca_la_clasificacion(cat):
    proveedor = servicio_proveedores.crear_proveedor("A", categoria_ids=[cat.bebidas.id])

    servicio_proveedores.actualizar_proveedor(proveedor.id, "A nuevo")

    assert _ids(proveedor.id) == {cat.bebidas.id}


def test_categoria_inexistente_o_inactiva_no_se_puede_agregar(cat):
    existente = servicio_proveedores.crear_proveedor("Existente", categoria_ids=[cat.limpieza.id])
    assert servicio_categorias.eliminar_categoria(cat.limpieza.id) is True
    with pytest.raises(DatosInvalidosError, match="no existe"):
        servicio_proveedores.crear_proveedor("A", categoria_ids=[9999])
    with pytest.raises(DatosInvalidosError, match="dada de baja"):
        servicio_proveedores.crear_proveedor("A", categoria_ids=[cat.limpieza.id])
    with pytest.raises(DatosInvalidosError, match="no existe"):
        servicio_proveedores.actualizar_proveedor(existente.id, "Renombrado", categoria_ids=[cat.golosinas.id, 0])
    assert [p.nombre for p in servicio_proveedores.listar_todos()] == ["Existente"]
    assert _ids(existente.id) == {cat.limpieza.id}


def test_categoria_dada_de_baja_despues_queda_vinculada_y_se_conserva_al_editar(cat):
    proveedor = servicio_proveedores.crear_proveedor("A", categoria_ids=[cat.limpieza.id, cat.golosinas.id])

    assert servicio_categorias.eliminar_categoria(cat.limpieza.id) is True  # en uso -> baja lógica
    servicio_proveedores.actualizar_proveedor(proveedor.id, "A", categoria_ids=[cat.limpieza.id])

    assert _ids(proveedor.id) == {cat.limpieza.id}
    [categoria] = servicio_proveedores.obtener_ficha(proveedor.id).categorias
    assert categoria.activa is False
    opciones = {o.categoria.id for o in servicio_proveedores.opciones_de_clasificacion(_ids(proveedor.id))}
    assert cat.limpieza.id in opciones
    assert cat.limpieza.id not in {o.categoria.id for o in servicio_proveedores.opciones_de_clasificacion()}


def test_categoria_sin_proveedores_ni_productos_se_sigue_borrando_fisicamente(cat):
    assert servicio_categorias.eliminar_categoria(cat.golosinas.id) is False
    assert servicio_categorias.obtener_por_id(cat.golosinas.id) is None


def test_padre_e_hija_juntas_y_el_filtro_por_padre_incluye_subcategorias(cat):
    solo_hija = servicio_proveedores.crear_proveedor("Gaseosera", categoria_ids=[cat.gaseosas.id])
    ambas = servicio_proveedores.crear_proveedor("Bebidas SA", categoria_ids=[cat.bebidas.id, cat.gaseosas.id])
    servicio_proveedores.crear_proveedor("Limpito", categoria_ids=[cat.limpieza.id])

    por_padre = [p.nombre for p in servicio_proveedores.buscar_proveedores("", categoria_id=cat.bebidas.id)]
    por_hija = [p.nombre for p in servicio_proveedores.buscar_proveedores("", categoria_id=cat.gaseosas.id)]

    assert por_padre == ["Bebidas SA", "Gaseosera"]
    assert por_hija == ["Bebidas SA", "Gaseosera"]
    assert [c.ruta for c in servicio_proveedores.obtener_ficha(ambas.id).categorias] == ["Bebidas", "Bebidas › Gaseosas"]
    assert _ids(solo_hija.id) == {cat.gaseosas.id}


def test_proveedor_con_muchas_categorias(base_datos_temporal):
    ids = [servicio_categorias.crear_categoria(f"Cat {i:02d}").id for i in range(40)]
    proveedor = servicio_proveedores.crear_proveedor("Mayorista total", categoria_ids=ids + ids[:5])

    [resumen] = servicio_proveedores.listar_resumen()
    assert _ids(proveedor.id) == set(ids)
    assert len(resumen.categorias) == 40


def test_busqueda_por_cuit_con_o_sin_guiones_por_razon_social_y_combinada_con_categoria(cat):
    servicio_proveedores.crear_proveedor(
        "Norte", fiscales=DatosFiscales(razon_social="Norteña SRL", cuit=CUIT_A), categoria_ids=[cat.bebidas.id]
    )
    servicio_proveedores.crear_proveedor("Sur", fiscales=DatosFiscales(cuit=CUIT_B))

    def nombres(texto, **filtros):
        return [p.nombre for p in servicio_proveedores.buscar_proveedores(texto, **filtros)]

    assert nombres("30-7123") == ["Norte"]
    assert nombres("712345671") == ["Norte"]
    assert nombres("norteña") == ["Norte"]
    assert nombres("20123", categoria_id=cat.bebidas.id) == []
    assert nombres("", categoria_id=cat.limpieza.id) == []
    assert nombres("%") == [] and nombres("-") == []


def test_inactivos_se_filtran_por_categoria_solo_si_se_piden(cat):
    owner = crear_owner()
    proveedor = servicio_proveedores.crear_proveedor("A", categoria_ids=[cat.bebidas.id])
    servicio_compras.registrar_compra(proveedor.id, owner.id, [ItemCompra(crear_producto().id, 1, 10)])
    servicio_proveedores.eliminar_proveedor(proveedor.id)

    assert servicio_proveedores.buscar_proveedores("", categoria_id=cat.bebidas.id) == []
    assert [p.id for p in servicio_proveedores.buscar_proveedores("", True, cat.bebidas.id)] == [proveedor.id]


def test_baja_fisica_de_un_proveedor_sin_historia_borra_su_clasificacion_y_no_las_categorias(cat):
    proveedor = servicio_proveedores.crear_proveedor("A", categoria_ids=[cat.bebidas.id])

    assert servicio_proveedores.eliminar_proveedor(proveedor.id) is False
    assert consultar(cat.ruta, "SELECT COUNT(*) FROM proveedor_categorias")[0][0] == 0
    assert servicio_categorias.obtener_por_id(cat.bebidas.id) is not None


def test_la_base_rechaza_un_vinculo_a_una_categoria_o_proveedor_inexistente(cat):
    proveedor = servicio_proveedores.crear_proveedor("A")
    with pytest.raises(Exception, match="FOREIGN KEY"):
        escribir(cat.ruta, "INSERT INTO proveedor_categorias VALUES (?, 9999)", (proveedor.id,))
    with pytest.raises(Exception, match="FOREIGN KEY"):
        escribir(cat.ruta, "INSERT INTO proveedor_categorias VALUES (9999, ?)", (cat.bebidas.id,))


def test_la_base_impide_borrar_fisicamente_una_categoria_que_clasifica_a_un_proveedor(cat):
    servicio_proveedores.crear_proveedor("A", categoria_ids=[cat.golosinas.id])
    with pytest.raises(Exception, match="FOREIGN KEY"):
        escribir(cat.ruta, "DELETE FROM categorias WHERE id = ?", (cat.golosinas.id,))


def test_cambios_de_categorias_se_auditan_en_la_misma_transaccion(cat):
    owner = crear_owner()
    proveedor = servicio_proveedores.crear_proveedor("A", usuario_id=owner.id, categoria_ids=[cat.gaseosas.id])
    servicio_proveedores.actualizar_proveedor(proveedor.id, "A", usuario_id=owner.id, categoria_ids=[cat.gaseosas.id])
    servicio_proveedores.actualizar_proveedor(proveedor.id, "A", usuario_id=owner.id, categoria_ids=[])

    filas = consultar(
        cat.ruta, "SELECT resumen FROM auditoria WHERE accion = 'PROVEEDOR_CATEGORIAS_CAMBIADAS' ORDER BY id"
    )
    assert [f["resumen"] for f in filas] == ["A: categorías Bebidas › Gaseosas", "A: categorías (ninguna)"]


def test_una_falla_al_clasificar_revierte_el_alta_completa(cat, monkeypatch):
    from db.repositorios import proveedores as repositorio_proveedores

    def fallar(*_args, **_kwargs):
        raise ErrorBaseDatos("falla simulada")

    monkeypatch.setattr(repositorio_proveedores, "reemplazar_categorias_en_conexion", fallar)
    with pytest.raises(ErrorBaseDatos):
        servicio_proveedores.crear_proveedor("A", categoria_ids=[cat.bebidas.id])
    assert servicio_proveedores.listar_todos() == []


def test_la_clasificacion_no_restringe_compras_de_otras_categorias(cat):
    owner = crear_owner()
    producto = crear_producto()
    proveedor = servicio_proveedores.crear_proveedor("Bebidas SA", categoria_ids=[cat.bebidas.id])
    escribir(cat.ruta, "UPDATE productos SET categoria_id = ? WHERE id = ?", (cat.limpieza.id, producto.id))

    compra = servicio_compras.registrar_compra(proveedor.id, owner.id, [ItemCompra(producto.id, 2, 10)])

    assert compra.id is not None
