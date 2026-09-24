"""V1.2: la auditoría es ATÓMICA con la operación. Toda operación auditada escribe
operación + rastro en la misma transacción: si la auditoría falla, la operación
hace rollback real (no queda nada a medias) y una operación con usuario no puede
completarse sin dejar su entrada."""

from collections import Counter

import pytest

import db.repositorios.auditoria as modulo_auditoria
from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.compra import ItemCompra
from domain.comercio import DatosComercio
from domain.precios_masivos import CriterioActualizacion
from domain.usuario import Usuario
from domain.venta import ItemVenta
from excepciones import CodigoBarrasDuplicadoError, ErrorBaseDatos, StockInsuficienteError
from services import (
    servicio_auth,
    servicio_caja,
    servicio_compras,
    servicio_configuracion,
    servicio_importacion,
    servicio_precios,
    servicio_proveedores,
    servicio_stock,
    servicio_usuarios,
    servicio_ventas,
)


def _usuario(nombre="duenio", rol="OWNER", activo=True):
    return repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre,
            nombre_completo=f"Nombre {nombre}",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
            activo=activo,
        )
    )


def _consultar(sql, *parametros):
    with obtener_conexion() as conexion:
        return conexion.execute(sql, parametros).fetchall()


def _contar(tabla, donde="1=1"):
    return _consultar(f"SELECT COUNT(*) FROM {tabla} WHERE {donde}")[0][0]


@pytest.fixture
def auditoria_que_falla(monkeypatch):
    """Hace fallar la escritura de auditoría (toda operación auditada la invoca
    a través de este módulo)."""

    def falla(*_args, **_kwargs):
        raise ErrorBaseDatos("falla simulada de la auditoría")

    monkeypatch.setattr(modulo_auditoria, "registrar_en_conexion", falla)


def _producto(nombre="Alfajor", codigo="7790000000001", stock=10, usuario_id=None):
    return servicio_stock.registrar_producto(codigo, nombre, 100, 200, stock_actual=stock, usuario_id=usuario_id)


class TestProducto:
    def test_alta_con_falla_de_auditoria_no_crea_el_producto(self, base_datos_temporal, auditoria_que_falla):
        usuario = _usuario()

        with pytest.raises(ErrorBaseDatos):
            _producto(usuario_id=usuario.id)

        assert _contar("productos") == 0

    def test_baja_fisica_con_falla_de_auditoria_no_elimina_el_producto(self, base_datos_temporal, monkeypatch):
        usuario = _usuario()
        producto = _producto()
        monkeypatch.setattr(modulo_auditoria, "registrar_en_conexion", lambda *a, **k: (_ for _ in ()).throw(ErrorBaseDatos("x")))

        with pytest.raises(ErrorBaseDatos):
            servicio_stock.eliminar_producto(producto.id, usuario_id=usuario.id)

        assert servicio_stock.obtener_por_id(producto.id) is not None

    def test_baja_logica_con_falla_de_auditoria_no_desactiva_el_producto(self, base_datos_temporal, monkeypatch):
        usuario = _usuario()
        servicio_caja.abrir_caja(0)
        producto = _producto()
        servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        monkeypatch.setattr(modulo_auditoria, "registrar_en_conexion", lambda *a, **k: (_ for _ in ()).throw(ErrorBaseDatos("x")))

        with pytest.raises(ErrorBaseDatos):
            servicio_stock.eliminar_producto(producto.id, usuario_id=usuario.id)

        assert _consultar("SELECT activo FROM productos WHERE id = ?", producto.id)[0][0] == 1

    def test_reactivacion_con_falla_de_auditoria_lo_deja_inactivo(self, base_datos_temporal, monkeypatch):
        usuario = _usuario()
        producto = _producto()
        with obtener_conexion() as conexion:
            conexion.execute("UPDATE productos SET activo = 0 WHERE id = ?", (producto.id,))
        monkeypatch.setattr(modulo_auditoria, "registrar_en_conexion", lambda *a, **k: (_ for _ in ()).throw(ErrorBaseDatos("x")))

        with pytest.raises(ErrorBaseDatos):
            servicio_stock.reactivar_producto(producto.id, usuario_id=usuario.id)

        assert _consultar("SELECT activo FROM productos WHERE id = ?", producto.id)[0][0] == 0

    def test_edicion_con_falla_de_auditoria_no_cambia_datos_ni_historial(self, base_datos_temporal, monkeypatch):
        usuario = _usuario()
        producto = _producto()
        monkeypatch.setattr(modulo_auditoria, "registrar_en_conexion", lambda *a, **k: (_ for _ in ()).throw(ErrorBaseDatos("x")))

        with pytest.raises(ErrorBaseDatos):
            servicio_stock.actualizar_producto(
                producto.id, producto.codigo_barras, "Otro nombre", 100, 999, 0, usuario_id=usuario.id
            )

        actual = servicio_stock.obtener_por_id(producto.id)
        assert (actual.nombre, actual.precio_venta_centavos) == ("Alfajor", 200)
        assert _contar("historial_precios") == 0


class TestUsuarios:
    def test_crear_usuario_con_falla_de_auditoria_no_lo_crea(self, base_datos_temporal, auditoria_que_falla):
        owner = _usuario()

        with pytest.raises(ErrorBaseDatos):
            servicio_usuarios.crear_usuario("cajera", "Cajera", "clave-larga-123", "CASHIER", actor_id=owner.id)

        assert _contar("usuarios", "nombre_usuario = 'cajera'") == 0

    def test_cambio_de_rol_con_falla_de_auditoria_no_cambia_el_rol(self, base_datos_temporal, monkeypatch):
        owner = _usuario()
        otro = _usuario("otro", "OWNER")
        monkeypatch.setattr(modulo_auditoria, "registrar_en_conexion", lambda *a, **k: (_ for _ in ()).throw(ErrorBaseDatos("x")))

        with pytest.raises(ErrorBaseDatos):
            servicio_usuarios.cambiar_rol(otro.id, "CASHIER", actor_id=owner.id)

        assert _consultar("SELECT rol FROM usuarios WHERE id = ?", otro.id)[0][0] == "OWNER"

    def test_activar_con_falla_de_auditoria_lo_deja_inactivo(self, base_datos_temporal, monkeypatch):
        owner = _usuario()
        inactivo = _usuario("inactivo", "CASHIER", activo=False)
        monkeypatch.setattr(modulo_auditoria, "registrar_en_conexion", lambda *a, **k: (_ for _ in ()).throw(ErrorBaseDatos("x")))

        with pytest.raises(ErrorBaseDatos):
            servicio_usuarios.activar(inactivo.id, actor_id=owner.id)

        assert _consultar("SELECT activo FROM usuarios WHERE id = ?", inactivo.id)[0][0] == 0

    def test_desactivar_con_falla_de_auditoria_lo_deja_activo(self, base_datos_temporal, monkeypatch):
        owner = _usuario()
        cajera = _usuario("cajera", "CASHIER")
        monkeypatch.setattr(modulo_auditoria, "registrar_en_conexion", lambda *a, **k: (_ for _ in ()).throw(ErrorBaseDatos("x")))

        with pytest.raises(ErrorBaseDatos):
            servicio_usuarios.desactivar(cajera.id, actor_id=owner.id)

        assert _consultar("SELECT activo FROM usuarios WHERE id = ?", cajera.id)[0][0] == 1

    def test_reset_de_password_con_falla_de_auditoria_no_cambia_el_hash(self, base_datos_temporal, monkeypatch):
        owner = _usuario()
        cajera = _usuario("cajera", "CASHIER")
        hash_original = _consultar("SELECT password_hash FROM usuarios WHERE id = ?", cajera.id)[0][0]
        monkeypatch.setattr(modulo_auditoria, "registrar_en_conexion", lambda *a, **k: (_ for _ in ()).throw(ErrorBaseDatos("x")))

        with pytest.raises(ErrorBaseDatos):
            servicio_usuarios.resetear_password(cajera.id, "otra-clave-larga-1", actor_id=owner.id)

        assert _consultar("SELECT password_hash FROM usuarios WHERE id = ?", cajera.id)[0][0] == hash_original
        assert servicio_auth.iniciar_sesion("cajera", "clave-correcta-123") is not None  # la clave vieja sigue valiendo


class TestOtrasOperacionesAuditadas:
    def test_ajuste_compra_anulacion_caja_precios_importacion_y_configuracion_hacen_rollback(
        self, base_datos_temporal, monkeypatch
    ):
        owner = _usuario()
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        producto = _producto()
        servicio_caja.abrir_caja(100_000)
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 2)], "EFECTIVO")
        stock_antes = servicio_stock.obtener_por_id(producto.id).stock_actual
        monkeypatch.setattr(modulo_auditoria, "registrar_en_conexion", lambda *a, **k: (_ for _ in ()).throw(ErrorBaseDatos("x")))
        csv = b"codigo_barras,nombre,precio_costo,precio_venta,stock_actual,stock_minimo\n999,Nuevo,1,2,0,0\n"

        operaciones = {
            "ajuste": lambda: servicio_stock.ajustar_stock(producto.id, -1, "MERMA", owner.id),
            "compra": lambda: servicio_compras.registrar_compra(proveedor.id, owner.id, [ItemCompra(producto.id, 5, 120)]),
            "anulacion": lambda: servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id),
            "caja": lambda: servicio_caja.registrar_egreso(1_000, "pago", usuario_id=owner.id),
            "precios": lambda: servicio_precios.aplicar_actualizacion(
                owner.id, CriterioActualizacion("PORCENTAJE", "AUMENTAR", 1000), "TODOS", {producto.id: 200}
            ),
            "importacion": lambda: servicio_importacion.procesar_archivo("a.csv", csv, usuario_id=owner.id),
            "configuracion": lambda: servicio_configuracion.guardar_datos_comercio(DatosComercio("Nuevo"), usuario_id=owner.id),
        }
        for nombre, operacion in operaciones.items():
            with pytest.raises(ErrorBaseDatos):
                operacion()

        assert servicio_stock.obtener_por_id(producto.id).stock_actual == stock_antes
        assert servicio_stock.obtener_por_id(producto.id).precio_venta_centavos == 200
        assert servicio_stock.obtener_por_id(producto.id).precio_costo_centavos == 100
        assert _contar("ajustes_stock") == 0 and _contar("compras") == 0
        assert _consultar("SELECT estado FROM ventas WHERE id = ?", venta.id)[0][0] == "ACTIVA"
        assert _contar("caja_movimientos", "tipo = 'EGRESO'") == 0
        assert _contar("lotes_precios") == 0 and _contar("historial_precios") == 0
        assert _contar("productos", "codigo_barras = '999'") == 0
        assert _contar("configuracion") == 0


class TestNingunaOperacionAuditadaQuedaSinRastro:
    def test_cada_operacion_auditada_con_usuario_deja_exactamente_su_entrada(self, base_datos_temporal):
        owner = _usuario()
        proveedor = servicio_proveedores.crear_proveedor("Distribuidora SA")
        servicio_caja.abrir_caja(100_000, usuario_id=owner.id)
        servicio_caja.registrar_ingreso(500, "cambio", usuario_id=owner.id)
        servicio_caja.registrar_egreso(200, "pago", usuario_id=owner.id)
        producto = _producto(usuario_id=owner.id)
        servicio_stock.actualizar_producto(producto.id, producto.codigo_barras, "Nuevo nombre", 100, 300, 0, usuario_id=owner.id)
        servicio_stock.ajustar_stock(producto.id, -1, "MERMA", owner.id)
        servicio_compras.registrar_compra(proveedor.id, owner.id, [ItemCompra(producto.id, 5, 120)])
        venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO")
        servicio_ventas.anular_venta(venta.id, motivo="ERROR_CARGA", observaciones=None, usuario_id=owner.id)
        servicio_precios.aplicar_actualizacion(
            owner.id, CriterioActualizacion("PORCENTAJE", "AUMENTAR", 1000), "TODOS", {producto.id: 300}
        )
        servicio_importacion.procesar_archivo(
            "a.csv", b"codigo_barras,nombre,precio_costo,precio_venta,stock_actual,stock_minimo\n888,Otro,1,2,0,0\n",
            usuario_id=owner.id,
        )
        servicio_configuracion.guardar_datos_comercio(DatosComercio("Mi Kiosco"), usuario_id=owner.id)
        nuevo = servicio_usuarios.crear_usuario("cajera", "Cajera", "clave-larga-123", "CASHIER", actor_id=owner.id)
        servicio_usuarios.cambiar_rol(nuevo.id, "OWNER", actor_id=owner.id)
        servicio_usuarios.desactivar(nuevo.id, actor_id=owner.id)
        servicio_usuarios.activar(nuevo.id, actor_id=owner.id)
        servicio_usuarios.resetear_password(nuevo.id, "otra-clave-larga-1", actor_id=owner.id)
        servicio_stock.eliminar_producto(producto.id, usuario_id=owner.id)
        servicio_stock.reactivar_producto(producto.id, usuario_id=owner.id)

        acciones = Counter(fila[0] for fila in _consultar("SELECT accion FROM auditoria"))

        assert acciones == Counter(
            {
                "CAJA_APERTURA": 1, "CAJA_INGRESO": 1, "CAJA_EGRESO": 1,
                "PRODUCTO_CREADO": 1, "PRODUCTO_EDITADO": 1, "AJUSTE_STOCK": 1, "COMPRA_REGISTRADA": 1,
                "VENTA_ANULADA": 1, "PRECIOS_ACTUALIZACION_MASIVA": 1, "PRODUCTOS_IMPORTADOS": 1,
                "CONFIGURACION_COMERCIO_CAMBIADA": 1, "USUARIO_CREADO": 1, "USUARIO_ROL_CAMBIADO": 1,
                "USUARIO_DESACTIVADO": 1, "USUARIO_ACTIVADO": 1, "USUARIO_PASSWORD_RESETEADA": 1,
                "PRODUCTO_BAJA": 1, "PRODUCTO_REACTIVADO": 1,
            }
        )
        assert all(fila[0] == owner.id for fila in _consultar("SELECT usuario_id FROM auditoria"))

    def test_una_operacion_fallida_no_deja_una_auditoria_falsa(self, base_datos_temporal):
        owner = _usuario()
        producto = _producto(stock=1)

        with pytest.raises(StockInsuficienteError):
            servicio_stock.ajustar_stock(producto.id, -5, "MERMA", owner.id)
        with pytest.raises(CodigoBarrasDuplicadoError):
            _producto(usuario_id=owner.id)

        assert _contar("auditoria") == 0

    def test_sin_usuario_no_se_audita(self, base_datos_temporal):
        _producto()
        servicio_usuarios.crear_usuario("cajera", "Cajera", "clave-larga-123", "CASHIER")

        assert _contar("auditoria") == 0
