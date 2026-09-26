"""Excepciones de dominio del proyecto.

Todas las excepciones propias de la aplicación heredan de
`ErrorAplicacion`, para poder capturarlas de forma genérica en los
bordes de la aplicación (interfaces/) sin capturar `Exception` a secas.
"""


class ErrorAplicacion(Exception):
    """Excepción base de la aplicación. No se usa directamente."""


class ErrorBaseDatos(ErrorAplicacion):
    """Error al acceder o modificar la base de datos.

    Traduce errores de bajo nivel de sqlite3 en un error de dominio,
    para que las capas superiores no dependan de sqlite3.
    """


class ProductoNoEncontradoError(ErrorAplicacion):
    """No existe un producto con el código o id solicitado."""


class CodigoBarrasDuplicadoError(ErrorAplicacion):
    """Ya existe un producto registrado con ese código de barras."""


class DatosInvalidosError(ErrorAplicacion):
    """Los datos provistos violan una regla de negocio del dominio.

    Ej.: precios o stock negativos, campos obligatorios vacíos.
    """


class StockInsuficienteError(ErrorAplicacion):
    """La operación requiere más stock del disponible."""


class PrecioVentaNoConfiguradoError(DatosInvalidosError):
    """El producto tiene precio de venta 0: primero hay que configurarlo.

    Es el estado en el que nace todo producto del catálogo inicial de
    distribución (ver `services.servicio_catalogo_inicial`).
    """


class ErrorCatalogoInicial(ErrorAplicacion):
    """El catálogo inicial de distribución es inválido o no pudo cargarse."""


class PuertoOcupadoError(ErrorAplicacion):
    """El puerto local de la aplicación ya está en uso: otra instancia de
    Twinx Express, u otro programa."""


class ErrorInicioServidor(ErrorAplicacion):
    """El servidor no pudo iniciar por un fallo real de arranque (base de
    datos inaccesible o corrupta, migración, seed o backup fallidos), no
    por un puerto ocupado."""


class CajaError(ErrorAplicacion):
    """Error en una operación de caja (apertura, cierre o movimiento)."""


MENSAJE_FORMULARIO_REENVIADO_CON_OTROS_DATOS = (
    "Este formulario ya se había enviado con otros datos y no se registró de nuevo. "
    "Recargá la página y cargá la operación otra vez."
)


class CajaCerradaError(CajaError):
    """Se intentó registrar una venta sin ninguna caja abierta: toda venta
    debe pertenecer a una sesión de caja (decisión de negocio de V1.1)."""


class ArchivoImportacionInvalidoError(DatosInvalidosError):
    """El archivo subido para importación masiva no tiene un formato,
    extensión o columnas válidas."""


class NombreUsuarioDuplicadoError(ErrorAplicacion):
    """Ya existe un usuario registrado con ese nombre de usuario."""


class CredencialesInvalidasError(ErrorAplicacion):
    """Nombre de usuario o contraseña incorrectos.

    Se usa tanto si el usuario no existe como si la contraseña es
    incorrecta: `services.servicio_auth.iniciar_sesion` levanta esta
    misma excepción, con el mismo mensaje, en ambos casos para no
    revelar cuál de los dos datos falló (ver docstring del módulo).
    """


class NoAutenticadoError(ErrorAplicacion):
    """La acción requiere una sesión válida y no hay ninguna (fase 2D+).

    Distinta de `PermisoDenegadoError`: acá no sabemos quién es el
    usuario. Traducir esto a una respuesta HTTP (ej. redirigir a
    `/login`) es responsabilidad de una fase posterior; ver
    `interfaces.web.auth.requiere_rol`.
    """


class PermisoDenegadoError(ErrorAplicacion):
    """Hay un usuario autenticado, pero su rol no alcanza para la acción
    (fase 2D+). Distinta de `NoAutenticadoError`: acá sí sabemos quién
    es, simplemente no tiene el permiso necesario."""


class UsuarioInactivoError(ErrorAplicacion):
    """El usuario existe y la contraseña sería válida, pero está desactivado.

    Se distingue de `CredencialesInvalidasError` solo para uso interno
    (por ejemplo, un log de auditoría). Cualquier capa que la exponga
    hacia afuera (la interfaz web, en una fase posterior) debe
    mostrarla con el mismo mensaje genérico que una credencial
    inválida: el mensaje de esta excepción puede ser más específico
    porque está pensado para consumo interno, no para mostrarse tal
    cual al usuario final.
    """


class NombreCategoriaDuplicadoError(ErrorAplicacion):
    """Ya existe una categoría registrada con ese nombre."""


class CategoriaNoEncontradaError(ErrorAplicacion):
    """No existe una categoría con el id o nombre solicitado."""


class ArchivoImagenInvalidoError(DatosInvalidosError):
    """La imagen subida para un producto no tiene un formato, tamaño o
    contenido válido (ver `services.servicio_imagenes`)."""


class NombreProveedorDuplicadoError(ErrorAplicacion):
    """Ya existe un proveedor registrado con ese nombre."""


class ProveedorNoEncontradoError(ErrorAplicacion):
    """No existe un proveedor con el id solicitado."""


class VinculoProveedorExistenteError(ErrorAplicacion):
    """El producto ya está vinculado a ese proveedor."""


class VinculoProveedorNoEncontradoError(ErrorAplicacion):
    """El producto no está vinculado a ese proveedor."""


class ProductoDuplicadoEnCompraError(DatosInvalidosError):
    """Un mismo producto aparece en más de una línea de la misma compra
    (ver `services.servicio_compras.registrar_compra`: cada producto
    puede aparecer como máximo una vez por compra)."""


class ClaveIdempotenciaReutilizadaError(ErrorAplicacion):
    """Una `clave_idempotencia` de venta (Fase 5A) ya se usó antes con
    un contenido distinto (otra cantidad, otro producto u otro tipo de
    pago). Distinta de un reintento legítimo: ahí el contenido
    coincide y `services.servicio_ventas.registrar_venta` devuelve la
    venta existente en vez de levantar esta excepción."""


class UsuarioNoEncontradoError(ErrorAplicacion):
    """No existe un usuario con el id solicitado."""


class UltimoOwnerActivoError(ErrorAplicacion):
    """La operación dejaría al sistema sin ningún usuario OWNER activo
    (desactivar al último OWNER activo, o cambiarle el rol a CASHIER).
    Ver `services.servicio_usuarios`."""


class ErrorBackup(ErrorAplicacion):
    """No se pudo generar el backup (ver `services.servicio_backup`)."""


class ErrorRestore(ErrorAplicacion):
    """El backup no es válido o no se pudo restaurar (ver
    `services.servicio_restore`). El mensaje está pensado para
    mostrarse tal cual al usuario final: nunca se ejecuta ninguna
    modificación sobre la instalación real antes de que todas las
    validaciones representadas por esta excepción hayan pasado."""


class VentaNoEncontradaError(ErrorAplicacion):
    """No existe una venta con el id solicitado (ver
    `services.servicio_ventas.anular_venta`)."""


class VentaYaAnuladaError(ErrorAplicacion):
    """La venta ya fue anulada anteriormente: no se puede anular dos veces."""


class VentaDeCajaCerradaError(ErrorAplicacion):
    """La venta no pertenece a la sesión de caja actualmente abierta (o
    no hay ninguna caja abierta ahora mismo).

    Anularla implicaría modificar un cierre de caja ya congelado
    (`caja_movimientos` de tipo CIERRE) o inventar una reversión sobre
    una caja que ni siquiera está abierta -- ninguna de las dos está
    en el alcance de este MVP (ver auditoría de diseño de anulación de
    ventas)."""


class CompraNoEncontradaError(ErrorAplicacion):
    """No existe una compra con el id solicitado (ver `services.servicio_compras.anular_compra`)."""


class CompraYaAnuladaError(ErrorAplicacion):
    """La compra ya fue anulada anteriormente: no se puede anular dos veces."""


class ClienteNoEncontradoError(ErrorAplicacion):
    """No existe un cliente con el id solicitado."""


class ClienteInactivoError(ErrorAplicacion):
    """El cliente está desactivado: no puede asociarse a ninguna venta nueva
    ni recibir cobros."""


class ClienteConSaldoError(ErrorAplicacion):
    """No se puede desactivar un cliente con saldo pendiente en su cuenta corriente."""


class CobroInvalidoError(DatosInvalidosError):
    """El cobro de cuenta corriente no cumple las reglas comerciales: medio
    distinto de efectivo o monto fuera de `0 < monto <= saldo`."""


class VentaACuentaNoAnulableError(ErrorAplicacion):
    """Una venta a cuenta corriente no puede anularse en la V1.3: hacerlo
    obligaría a reversar el CARGO de la cuenta del cliente."""


class InventarioNoEncontradoError(ErrorAplicacion):
    """No existe un inventario con el id solicitado."""


class InventarioNoAbiertoError(ErrorAplicacion):
    """El inventario ya fue confirmado o cancelado: no admite conteos ni cierre."""


class InventarioAbiertoExistenteError(ErrorAplicacion):
    """Ya hay un inventario abierto: hay que confirmarlo o cancelarlo antes de iniciar otro."""


class InventarioSinConteosError(DatosInvalidosError):
    """No se puede confirmar un inventario sin ninguna línea contada."""


class ProductoFueraDeInventarioError(ErrorAplicacion):
    """El producto no forma parte de las líneas del inventario abierto."""


class InventarioDesactualizadoError(ErrorAplicacion):
    """El stock de uno o más productos cambió después de contarlos (una venta, compra, ajuste o
    anulación): el inventario no se confirma y hay que volver a contarlos.

    `productos` lista los nombres de los productos desactualizados.
    """

    def __init__(self, mensaje: str, productos: list[str]) -> None:
        super().__init__(mensaje)
        self.productos = productos
