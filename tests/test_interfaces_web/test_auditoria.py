"""V1.2 Fase 3: auditoría general -- qué se registra, con qué usuario, que sea
atómica con la operación, que no incluya datos sensibles y la pantalla de
consulta (solo OWNER)."""

import pytest

import services.servicio_stock as modulo_servicio_stock
from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.auditoria import LONGITUD_MAXIMA_RESUMEN, normalizar
from domain.compra import ItemCompra
from domain.precios_masivos import CriterioActualizacion
from domain.usuario import Usuario
from domain.venta import ItemVenta
from excepciones import DatosInvalidosError, ErrorBaseDatos, ProductoNoEncontradoError, StockInsuficienteError
from interfaces.web.auth import NOMBRE_COOKIE_SESION
from services import (
    servicio_auditoria,
    servicio_auth,
    servicio_caja,
    servicio_compras,
    servicio_precios,
    servicio_proveedores,
    servicio_stock,
    servicio_usuarios,
    servicio_ventas,
)

from ._asgi_cliente import solicitud


def _usuario(nombre="duenio", rol="OWNER"):
    return repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre,
            nombre_completo=f"Nombre {nombre}",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )


def _cookies(nombre="web", rol="OWNER"):
    _usuario(nombre, rol)
    return {NOMBRE_COOKIE_SESION: servicio_auth.iniciar_sesion(nombre, "clave-correcta-123").token}


def _entradas():
    return [dict(f) for f in _consultar("SELECT * FROM auditoria ORDER BY id")]


def _consultar(sql):
    with obtener_conexion() as conexion:
        return conexion.execute(sql).fetchall()


def _acciones():
    return [e["accion"] for e in _entradas()]


def _producto(usuario_id=None, codigo="7790000000001", nombre="Alfajor", stock=10):
    return servicio_stock.registrar_producto(codigo, nombre, 100, 200, stock_actual=stock, usuario_id=usuario_id)


class TestDominio:
    def test_normaliza_espacios_y_saltos_de_linea(self):
        assert normalizar("AJUSTE_STOCK", "  a\n  b\t c  ") == "a b c"

    def test_acota_el_largo(self):
        resumen = normalizar("AJUSTE_STOCK", "x" * 1000)

        assert len(resumen) == LONGITUD_MAXIMA_RESUMEN and resumen.endswith("…")

    @pytest.mark.parametrize("accion, resumen", [("INVENTADA", "algo"), ("AJUSTE_STOCK", "   "), ("AJUSTE_STOCK", "")])
    def test_rechaza_accion_invalida_o_resumen_vacio(self, accion, resumen):
        with pytest.raises(DatosInvalidosError):
            normalizar(accion, resumen)


class TestProductos:
    def test_alta_con_usuario_se_audita(self, base_datos_temporal):
        usuario = _usuario()

        producto = _producto(usuario.id)

        (e,) = _entradas()
        assert (e["accion"], e["entidad"], e["entidad_id"], e["usuario_id"]) == (
            "PRODUCTO_CREADO", "PRODUCTO", producto.id, usuario.id,
        )
        assert "Alfajor" in e["resumen"] and "7790000000001" in e["resumen"]

    def test_sin_usuario_no_se_audita_ni_hace_ruido(self, base_datos_temporal):
        _producto()

        assert _entradas() == []

    def test_edicion_lista_los_campos_modificados(self, base_datos_temporal):
        usuario = _usuario()
        producto = _producto()

        servicio_stock.actualizar_producto(
            producto.id, producto.codigo_barras, "Alfajor triple", 100, 250, 3, usuario_id=usuario.id
        )

        (e,) = _entradas()
        assert e["accion"] == "PRODUCTO_EDITADO" and e["entidad_id"] == producto.id
        assert "nombre" in e["resumen"] and "precio de venta" in e["resumen"] and "stock mínimo" in e["resumen"]
        assert "costo" not in e["resumen"]

    def test_edicion_sin_cambios_no_se_audita(self, base_datos_temporal):
        usuario = _usuario()
        producto = _producto()

        servicio_stock.actualizar_producto(
            producto.id, producto.codigo_barras, producto.nombre, 100, 200, 0, usuario_id=usuario.id
        )

        assert _entradas() == []

    def test_baja_fisica_baja_logica_y_reactivacion(self, base_datos_temporal):
        servicio_caja.abrir_caja(0)
        usuario = _usuario()
        eliminable = _producto(codigo="1", nombre="Se borra")
        con_ventas = _producto(codigo="2", nombre="Se desactiva")
        servicio_ventas.registrar_venta([ItemVenta(con_ventas.id, 1)], "EFECTIVO")

        servicio_stock.eliminar_producto(eliminable.id, usuario_id=usuario.id)
        servicio_stock.eliminar_producto(con_ventas.id, usuario_id=usuario.id)
        servicio_stock.reactivar_producto(con_ventas.id, usuario_id=usuario.id)

        assert _acciones() == ["PRODUCTO_BAJA", "PRODUCTO_BAJA", "PRODUCTO_REACTIVADO"]
        assert "eliminado" in _entradas()[0]["resumen"] and "baja lógica" in _entradas()[1]["resumen"]


class TestAjusteCompraAnulacion:
    def test_ajuste_se_audita_con_motivo_delta_y_stock(self, base_datos_temporal):
        usuario = _usuario()
        producto = _producto()

        servicio_stock.ajustar_stock(producto.id, -3, "MERMA", usuario.id)

        (e,) = _entradas()
        assert e["accion"] == "AJUSTE_STOCK" and e["usuario_id"] == usuario.id
        assert "MERMA" in e["resumen"] and "-3" in e["resumen"] and "10 → 7" in e["resumen"]

    def test_el_reintento_de_un_ajuste_no_duplica_la_auditoria(self, base_datos_temporal):
        usuario = _usuario()
        producto = _producto()

        servicio_stock.ajustar_stock(producto.id, -3, "MERMA", usuario.id, clave_idempotencia="k")
        servicio_stock.ajustar_stock(producto.id, -3, "MERMA", usuario.id, clave_idempotencia="k")

        assert _acciones() == ["AJUSTE_STOCK"]

    def test_un_ajuste_rechazado_no_deja_auditoria(self, base_datos_temporal):
        usuario = _usuario()
        producto = _producto(stock=1)

        with pytest.raises(StockInsuficienteError):
            servicio_stock.ajustar_stock(producto.id, -5, "MERMA", usuario.id)

        assert _entradas() == []

    def test_si_falla_la_auditoria_el_ajuste_se_revierte_atomicidad(self, base_datos_temporal, monkeypatch):
        usuario = _usuario()
        producto = _producto()

        def auditoria_que_falla(*_a, **_k):
            raise ErrorBaseDatos("falla simulada")

        monkeypatch.setattr(modulo_servicio_stock.repositorio_auditoria, "registrar_en_conexion", auditoria_que_falla)

        with pytest.raises(ErrorBaseDatos):
            servicio_stock.ajustar_stock(producto.id, -3, "MERMA", usuario.id)

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == 10
        assert _consultar("SELECT COUNT(*) FROM ajustes_stock")[0][0] == 0

    def test_compra_se_audita_con_proveedor_y_total(self, base_datos_temporal):
        usuario = _usuario()
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        producto = _producto()

        compra = servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(producto.id, 5, 120)])

        (e,) = _entradas()
        assert (e["accion"], e["entidad"], e["entidad_id"]) == ("COMPRA_REGISTRADA", "COMPRA", compra.id)
        assert "Distribuidora SA" in e["resumen"] and "$6.00" in e["resumen"]

    def test_compra_rechazada_o_repetida_no_duplica_ni_deja_auditoria(self, base_datos_temporal):
        usuario = _usuario()
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        producto = _producto()

        with pytest.raises(ProductoNoEncontradoError):
            servicio_compras.registrar_compra(proveedor.id, usuario.id, [ItemCompra(99999, 1, 10)], clave_idempotencia="k")
        assert _entradas() == []

        items = [ItemCompra(producto.id, 5, 120)]
        servicio_compras.registrar_compra(proveedor.id, usuario.id, items, clave_idempotencia="k")
        servicio_compras.registrar_compra(proveedor.id, usuario.id, items, clave_idempotencia="k")
        assert _acciones() == ["COMPRA_REGISTRADA"]

    def test_anulacion_se_audita_con_el_motivo(self, base_datos_temporal):
        owner = _usuario()
        servicio_caja.abrir_caja(0)
        producto = _producto()
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")

        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)

        (e,) = _entradas()
        assert (e["accion"], e["entidad"], e["entidad_id"], e["usuario_id"]) == (
            "VENTA_ANULADA", "VENTA", venta.id, owner.id,
        )
        assert "ERROR_CARGA" in e["resumen"]


class TestCaja:
    def test_apertura_ingreso_egreso_y_cierre(self, base_datos_temporal):
        usuario = _usuario()

        servicio_caja.abrir_caja(100_000, usuario_id=usuario.id)
        servicio_caja.registrar_ingreso(5_000, "cambio", usuario_id=usuario.id)
        servicio_caja.registrar_egreso(2_000, "pago proveedor", usuario_id=usuario.id)
        servicio_caja.cerrar_caja(102_500, usuario_id=usuario.id)

        entradas = _entradas()
        assert [e["accion"] for e in entradas] == ["CAJA_APERTURA", "CAJA_INGRESO", "CAJA_EGRESO", "CAJA_CIERRE"]
        assert "1000,00" not in entradas[0]["resumen"] and "1000.00" in entradas[0]["resumen"]
        assert "pago proveedor" in entradas[2]["resumen"]
        assert "diferencia +$-" not in entradas[3]["resumen"] and "contado" in entradas[3]["resumen"]
        assert all(e["entidad"] == "CAJA" and e["usuario_id"] == usuario.id for e in entradas)

    def test_el_reintento_de_un_movimiento_no_duplica_la_auditoria(self, base_datos_temporal):
        usuario = _usuario()
        servicio_caja.abrir_caja(100_000, usuario_id=usuario.id)

        servicio_caja.registrar_egreso(2_000, "pago", usuario_id=usuario.id, clave_idempotencia="k")
        servicio_caja.registrar_egreso(2_000, "pago", usuario_id=usuario.id, clave_idempotencia="k")

        assert _acciones().count("CAJA_EGRESO") == 1

    def test_sin_usuario_no_se_audita(self, base_datos_temporal):
        servicio_caja.abrir_caja(100_000)

        assert _entradas() == []


class TestPreciosMasivos:
    def test_la_actualizacion_masiva_deja_una_sola_entrada_resumida(self, base_datos_temporal):
        usuario = _usuario()
        a, b = _producto(codigo="1", nombre="A"), _producto(codigo="2", nombre="B")
        criterio = CriterioActualizacion("PORCENTAJE", "AUMENTAR", 1000)

        lote = servicio_precios.aplicar_actualizacion(
            usuario.id, criterio, "TODOS", {a.id: 200, b.id: 200}, clave_idempotencia="k"
        )

        (e,) = _entradas()
        assert (e["accion"], e["entidad"], e["entidad_id"]) == ("PRECIOS_ACTUALIZACION_MASIVA", "LOTE_PRECIOS", lote.id)
        assert "Aumentar" in e["resumen"] and "10.00 %" in e["resumen"] and "2 producto(s)" in e["resumen"]

    def test_un_lote_rechazado_no_deja_auditoria(self, base_datos_temporal):
        usuario = _usuario()
        with pytest.raises(ProductoNoEncontradoError):
            servicio_precios.aplicar_actualizacion(
                usuario.id, CriterioActualizacion("MONTO", "AUMENTAR", 100), "TODOS", {9999: 1}
            )

        assert _entradas() == []


class TestUsuarios:
    def test_cada_operacion_de_administracion_se_audita(self, base_datos_temporal):
        owner = _usuario()

        nuevo = servicio_usuarios.crear_usuario("cajera", "Cajera", "clave-larga-123", "CASHIER", actor_id=owner.id)
        servicio_usuarios.cambiar_rol(nuevo.id, "OWNER", actor_id=owner.id)
        servicio_usuarios.desactivar(nuevo.id, actor_id=owner.id)
        servicio_usuarios.activar(nuevo.id, actor_id=owner.id)
        servicio_usuarios.resetear_password(nuevo.id, "otra-clave-larga-1", actor_id=owner.id)

        assert _acciones() == [
            "USUARIO_CREADO", "USUARIO_ROL_CAMBIADO", "USUARIO_DESACTIVADO", "USUARIO_ACTIVADO",
            "USUARIO_PASSWORD_RESETEADA",
        ]

    def test_nunca_registra_contrasenas_ni_hashes_ni_claves(self, base_datos_temporal):
        owner = _usuario()
        nuevo = servicio_usuarios.crear_usuario("cajera", "Cajera", "clave-secreta-777", "CASHIER", actor_id=owner.id)
        servicio_usuarios.resetear_password(nuevo.id, "otra-clave-secreta-888", actor_id=owner.id)
        hash_guardado = _consultar("SELECT password_hash FROM usuarios WHERE id = %d" % nuevo.id)[0][0]
        producto = _producto(owner.id)
        servicio_stock.ajustar_stock(producto.id, -1, "MERMA", owner.id, clave_idempotencia="CLAVE-IDEMPOTENCIA-XYZ")
        servicio_caja.abrir_caja(0, usuario_id=owner.id)
        servicio_caja.registrar_egreso(100, "x", usuario_id=owner.id, clave_idempotencia="OTRA-CLAVE-ABC")

        todo = " ".join(f"{e['accion']} {e['entidad']} {e['resumen']}" for e in _entradas())

        for secreto in ("clave-secreta-777", "otra-clave-secreta-888", hash_guardado, "CLAVE-IDEMPOTENCIA-XYZ", "OTRA-CLAVE-ABC"):
            assert secreto not in todo

    def test_sin_actor_no_se_audita(self, base_datos_temporal):
        servicio_usuarios.crear_usuario("cajera", "Cajera", "clave-larga-123", "CASHIER")

        assert _entradas() == []


class TestPantalla:
    def test_solo_el_owner_accede(self, base_datos_temporal):
        cajera = _cookies("cajera", "CASHIER")

        assert solicitud("GET", "/auditoria", cookies=cajera).status == 403
        assert solicitud("GET", "/auditoria").status == 303

    def test_lista_filtra_por_accion_usuario_y_fecha(self, base_datos_temporal):
        cookies = _cookies("web", "OWNER")
        otro = _usuario("otro")
        servicio_stock.registrar_producto("1", "Producto uno", 1, 2, usuario_id=otro.id)
        producto = _producto(otro.id, codigo="2", nombre="Producto dos")
        servicio_stock.ajustar_stock(producto.id, -1, "MERMA", otro.id)

        todas = solicitud("GET", "/auditoria", cookies=cookies)
        por_accion = solicitud("GET", "/auditoria?accion=AJUSTE_STOCK", cookies=cookies)
        por_usuario = solicitud("GET", f"/auditoria?usuario_id={otro.id}", cookies=cookies)
        futuro = solicitud("GET", "/auditoria?fecha_desde=2999-01-01", cookies=cookies)
        vacios = solicitud("GET", "/auditoria?accion=&usuario_id=&fecha_desde=&fecha_hasta=", cookies=cookies)

        assert "Producto uno" in todas.texto and "AJUSTE_STOCK" in todas.texto
        assert "Producto uno" not in por_accion.texto and "MERMA" in por_accion.texto
        assert "Nombre otro" in por_usuario.texto
        assert "Sin registros" in futuro.texto
        assert "Producto uno" in vacios.texto

    def test_el_menu_del_owner_incluye_auditoria_y_el_del_cashier_no(self, base_datos_temporal):
        owner = _cookies("web", "OWNER")
        cajera = _cookies("cajera", "CASHIER")

        assert 'href="/auditoria"' in solicitud("GET", "/", cookies=owner).texto
        assert 'href="/auditoria"' not in solicitud("GET", "/", cookies=cajera).texto

    def test_crear_un_producto_por_http_audita_al_usuario_autenticado(self, base_datos_temporal):
        cookies = _cookies("web", "OWNER")

        solicitud(
            "POST",
            "/productos/nuevo",
            cookies=cookies,
            formulario={
                "codigo_barras": "7790000000123",
                "nombre": "Chicle",
                "precio_costo": "1.00",
                "precio_venta": "2.00",
                "stock_actual": "5",
                "stock_minimo": "1",
                "categoria_id": "",
                "unidad_medida": "UNIDAD",
            },
        )

        (e,) = _entradas()
        assert e["accion"] == "PRODUCTO_CREADO" and "Chicle" in e["resumen"]
        assert e["usuario_id"] is not None


class TestMigracion017:
    def test_la_tabla_existe_con_sus_indices(self, base_datos_temporal):
        indices = {f["name"] for f in _consultar("PRAGMA index_list(auditoria)")}

        assert {"idx_auditoria_fecha", "idx_auditoria_accion", "idx_auditoria_usuario_id"} <= indices
