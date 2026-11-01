"""Fábricas compartidas por los tests fiscales (V1.16). No es un módulo de tests."""

from db.conexion import obtener_conexion
from db.repositorios import fiscal as repositorio_fiscal
from db.repositorios import usuarios as repositorio_usuarios
from domain.fiscal import tablas_arca
from domain.fiscal.comprobante import SnapshotReceptor, calcular_contenido_hash, resolver_receptor
from domain.fiscal.emisor import PerfilFiscal
from domain.fiscal.iva import TratamientoIva, calcular_importes
from domain.usuario import Usuario
from domain.venta import ItemVenta
from services import servicio_auth, servicio_stock, servicio_ventas

CUIT_EMISOR = "20111111112"
CUIT_RECEPTOR_RI = "30712345671"


def crear_owner(nombre_usuario: str = "duenio") -> int:
    return repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Dueño",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol="OWNER",
        )
    ).id


def crear_cashier(nombre_usuario: str = "cajero") -> int:
    return repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo="Cajero",
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol="CASHIER",
        )
    ).id


def perfil(condicion: str = "RESPONSABLE_INSCRIPTO", punto_venta: int = 3, **extra) -> PerfilFiscal:
    return PerfilFiscal(
        cuit=CUIT_EMISOR,
        razon_social="Kiosco Twinx SRL",
        condicion_iva=condicion,
        domicilio="Av. Siempreviva 742, CABA",
        ingresos_brutos="901-123456-7",
        inicio_actividades="2020-03-01",
        punto_venta_cae=punto_venta,
        **extra,
    )


def guardar_perfil_validado(condicion: str = "RESPONSABLE_INSCRIPTO", punto_venta: int = 3, **extra) -> PerfilFiscal:
    """Perfil persistido con el punto de venta ya validado en homologación (atajo de test)."""
    with obtener_conexion() as conexion:
        repositorio_fiscal.guardar_perfil_en_conexion(conexion, perfil(condicion, punto_venta, **extra))
        repositorio_fiscal.marcar_punto_venta_validado_en_conexion(conexion, False, "HOMOLOGACION")
        if extra.get("punto_venta_caea"):
            repositorio_fiscal.marcar_punto_venta_validado_en_conexion(conexion, True, "HOMOLOGACION")
    return repositorio_fiscal.obtener_perfil()


_codigos = iter(range(7790000000001, 7790000099999))


def producto(precio_centavos: int = 12100, tratamiento: TratamientoIva | None = None, stock: int = 1000, nombre: str | None = None):
    codigo = str(next(_codigos))
    creado = servicio_stock.registrar_producto(codigo, nombre or f"Producto {codigo[-4:]}", 100, precio_centavos, stock_actual=stock)
    if tratamiento is not None:
        with obtener_conexion() as conexion:
            repositorio_fiscal.guardar_tratamiento_en_conexion(conexion, creado.id, tratamiento, None)
    return creado


def vender(items: list[tuple], tipo_pago: str = "EFECTIVO", cliente_id: int | None = None, usuario_id: int | None = None):
    """`items`: (producto, cantidad). Venta real por el servicio (requiere caja abierta)."""
    return servicio_ventas.registrar_venta(
        [ItemVenta(p.id, cantidad) for p, cantidad in items], tipo_pago, cliente_id=cliente_id, usuario_id=usuario_id
    )


IVA_21 = TratamientoIva("GRAVADO", 5)
IVA_105 = TratamientoIva("GRAVADO", 4)
EXENTO = TratamientoIva("EXENTO")
NO_GRAVADO = TratamientoIva("NO_GRAVADO")


def nuevo_comprobante_para_venta(venta_id: int, clase: str = "B", receptor: SnapshotReceptor | None = None, **extra):
    """Arma un `NuevoComprobante` PENDIENTE (CAE) a partir de una venta real ya registrada."""
    from datetime import date

    with obtener_conexion() as conexion:
        venta = repositorio_fiscal.venta_para_fiscalizar_en_conexion(conexion, venta_id)
        emisor = (repositorio_fiscal.obtener_perfil_en_conexion(conexion) or perfil()).snapshot(3)
    receptor = receptor or resolver_receptor(None, venta.total_centavos, date.today())
    importes = calcular_importes(list(venta.lineas), clase)
    rol = extra.pop("rol", tablas_arca.ROL_PRINCIPAL)
    tipo = tablas_arca.TIPO_POR_CLASE_Y_ROL[(clase, rol)]
    datos = dict(
        venta_id=venta_id,
        rol=rol,
        comprobante_asociado_id=extra.pop("comprobante_asociado_id", None),
        ambiente="HOMOLOGACION",
        modalidad="CAE",
        caea_id=None,
        tipo_comprobante=tipo,
        clase=clase,
        concepto=tablas_arca.CONCEPTO_PRODUCTOS,
        moneda=tablas_arca.MONEDA_PESOS,
        cotizacion="1",
        emisor=emisor,
        receptor=receptor,
        importes=importes,
        lineas=venta.lineas,
        contenido_hash=calcular_contenido_hash(rol, None, "HOMOLOGACION", emisor, tipo, receptor, importes, list(venta.lineas)),
        usuario_id=None,
        estado="PENDIENTE",
    )
    datos.update(extra)
    return repositorio_fiscal.NuevoComprobante(**datos)


def insertar(nuevo) -> int:
    with obtener_conexion() as conexion:
        return repositorio_fiscal.insertar_comprobante_en_conexion(conexion, nuevo)


def transicionar(comprobante_id: int, estado: str, **campos) -> None:
    with obtener_conexion() as conexion:
        repositorio_fiscal.actualizar_estado_en_conexion(conexion, comprobante_id, estado, **campos)


def autorizar(comprobante_id: int, numero: int, cae: str = "71234567890123") -> None:
    """Lleva un PENDIENTE hasta AUTORIZADO por el camino válido (atajo de test, sin ARCA)."""
    transicionar(comprobante_id, "ENVIANDO", numero=numero, fecha_comprobante="2026-11-01")
    transicionar(
        comprobante_id, "AUTORIZADO", resultado="A", codigo_autorizacion=cae, vencimiento_autorizacion="2026-11-11"
    )
