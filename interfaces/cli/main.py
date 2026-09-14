"""Interfaz de consola (CLI) del sistema de stock del kiosco.

Menú interactivo para uso manual: alta y búsqueda de productos,
registro de ventas (carrito + tipo de pago), control de caja y
alertas de stock crítico. Solo orquesta llamadas a `services/`: no
contiene SQL ni reglas de negocio propias. Todas las excepciones de
negocio (`ErrorAplicacion` y derivadas) se capturan acá para mostrar
un mensaje claro en vez de un stack trace.
"""

import logging

from db.conexion import inicializar_base_datos
from domain.dinero import centavos_a_texto, texto_a_centavos
from domain.producto import Producto
from domain.venta import TIPOS_PAGO_VALIDOS, ItemVenta
from excepciones import DatosInvalidosError, ErrorAplicacion
from services import servicio_caja, servicio_stock, servicio_ventas

logger = logging.getLogger(__name__)


# --- Utilidades de entrada/salida por consola ---------------------------


def _pedir_texto(mensaje: str) -> str:
    """Pide un texto no vacío, repitiendo hasta obtener uno válido."""
    while True:
        valor = input(mensaje).strip()
        if valor:
            return valor
        print("  Este campo no puede estar vacío.")


def _pedir_texto_opcional(mensaje: str) -> str | None:
    """Pide un texto; una respuesta vacía se interpreta como 'nada'."""
    valor = input(mensaje).strip()
    return valor or None


def _pedir_centavos(mensaje: str) -> int:
    """Pide un monto en pesos (ej. "150.50") y lo devuelve en centavos."""
    while True:
        texto = input(mensaje).strip()
        try:
            return texto_a_centavos(texto)
        except DatosInvalidosError as error:
            print(f"  {error}")


def _pedir_entero_positivo(mensaje: str) -> int:
    """Pide un entero mayor a cero (ej. cantidad de un ítem de venta)."""
    while True:
        texto = input(mensaje).strip()
        try:
            valor = int(texto)
        except ValueError:
            print("  Ingresá un número entero.")
            continue
        if valor <= 0:
            print("  Debe ser mayor a cero.")
            continue
        return valor


def _pedir_entero_no_negativo(mensaje: str) -> int:
    """Pide un entero mayor o igual a cero (ej. stock inicial)."""
    while True:
        texto = input(mensaje).strip()
        try:
            valor = int(texto)
        except ValueError:
            print("  Ingresá un número entero.")
            continue
        if valor < 0:
            print("  No puede ser negativo.")
            continue
        return valor


def _mostrar_producto(producto: Producto) -> None:
    print(
        f"  [{producto.id}] {producto.nombre} | código: {producto.codigo_barras} | "
        f"precio: ${centavos_a_texto(producto.precio_venta_centavos)} | "
        f"stock: {producto.stock_actual} (mínimo: {producto.stock_minimo})"
    )


def _ejecutar_con_manejo_de_errores(accion) -> None:
    """Ejecuta `accion` capturando cualquier error de negocio.

    Es el único lugar donde se atrapan las excepciones de dominio
    (`ErrorAplicacion` y derivadas: `DatosInvalidosError`,
    `StockInsuficienteError`, `ProductoNoEncontradoError`,
    `CodigoBarrasDuplicadoError`, `CajaError`, `ErrorBaseDatos`), para
    mostrar un mensaje claro en vez de un stack trace.
    """
    try:
        accion()
    except ErrorAplicacion as error:
        print(f"\n  No se pudo completar la operación: {error}")
        logger.warning("Operación rechazada: %s", error)


# --- Productos ------------------------------------------------------------


def _alta_producto() -> None:
    print("\n-- Alta de producto --")
    codigo_barras = _pedir_texto("Código de barras: ")
    nombre = _pedir_texto("Nombre: ")
    precio_costo_centavos = _pedir_centavos("Precio de costo ($): ")
    precio_venta_centavos = _pedir_centavos("Precio de venta ($): ")
    stock_actual = _pedir_entero_no_negativo("Stock inicial: ")
    stock_minimo = _pedir_entero_no_negativo("Stock mínimo: ")

    producto = servicio_stock.registrar_producto(
        codigo_barras=codigo_barras,
        nombre=nombre,
        precio_costo_centavos=precio_costo_centavos,
        precio_venta_centavos=precio_venta_centavos,
        stock_actual=stock_actual,
        stock_minimo=stock_minimo,
    )
    print(f"  Producto creado con id {producto.id}.")


def _buscar_por_codigo() -> None:
    print("\n-- Buscar por código de barras --")
    codigo_barras = _pedir_texto("Código de barras: ")
    producto = servicio_stock.buscar_por_codigo_barras(codigo_barras)
    if producto is None:
        print("  No se encontró ningún producto con ese código.")
        return
    _mostrar_producto(producto)


def _buscar_por_nombre() -> None:
    print("\n-- Buscar por nombre --")
    texto = _pedir_texto("Nombre (o parte del nombre): ")
    resultados = servicio_stock.buscar_por_nombre(texto)
    if not resultados:
        print("  No se encontraron productos.")
        return
    for producto in resultados:
        _mostrar_producto(producto)


def _menu_productos() -> None:
    while True:
        print("\n=== Productos ===")
        print("1. Alta de producto")
        print("2. Buscar por código de barras")
        print("3. Buscar por nombre")
        print("4. Volver")
        opcion = input("Opción: ").strip()

        if opcion == "1":
            _ejecutar_con_manejo_de_errores(_alta_producto)
        elif opcion == "2":
            _ejecutar_con_manejo_de_errores(_buscar_por_codigo)
        elif opcion == "3":
            _ejecutar_con_manejo_de_errores(_buscar_por_nombre)
        elif opcion == "4":
            return
        else:
            print("Opción inválida.")


# --- Ventas -----------------------------------------------------------


def _registrar_venta() -> None:
    print("\n-- Nueva venta --")
    items: list[ItemVenta] = []

    while True:
        codigo_barras = _pedir_texto_opcional(
            "Código de barras (lector o manual, Enter vacío para cobrar): "
        )
        if codigo_barras is None:
            break

        producto = servicio_stock.buscar_por_codigo_barras(codigo_barras)
        if producto is None:
            print("  No se encontró ningún producto con ese código.")
            continue

        cantidad = _pedir_entero_positivo(f"  Cantidad de '{producto.nombre}': ")
        items.append(ItemVenta(producto_id=producto.id, cantidad=cantidad))
        subtotal_centavos = producto.precio_venta_centavos * cantidad
        print(f"  Agregado: {cantidad} x {producto.nombre} = ${centavos_a_texto(subtotal_centavos)}")

    if not items:
        print("  Venta cancelada: no se agregó ningún ítem.")
        return

    print(f"  Tipos de pago disponibles: {', '.join(sorted(TIPOS_PAGO_VALIDOS))}")
    tipo_pago = _pedir_texto("Tipo de pago: ").upper()

    venta = servicio_ventas.registrar_venta(items, tipo_pago)
    print(f"  Venta #{venta.id} registrada. Total: ${centavos_a_texto(venta.total_centavos)} ({venta.tipo_pago}).")


def _menu_ventas() -> None:
    while True:
        print("\n=== Ventas ===")
        print("1. Registrar venta")
        print("2. Volver")
        opcion = input("Opción: ").strip()

        if opcion == "1":
            _ejecutar_con_manejo_de_errores(_registrar_venta)
        elif opcion == "2":
            return
        else:
            print("Opción inválida.")


# --- Caja ---------------------------------------------------------------


def _abrir_caja() -> None:
    print("\n-- Abrir caja --")
    monto_inicial_centavos = _pedir_centavos("Monto inicial ($): ")
    descripcion = _pedir_texto_opcional("Descripción (opcional): ")
    movimiento = servicio_caja.abrir_caja(monto_inicial_centavos, descripcion)
    print(f"  Caja abierta. Movimiento #{movimiento.id} - ${centavos_a_texto(movimiento.monto_centavos)}")


def _cerrar_caja() -> None:
    print("\n-- Cerrar caja --")
    monto_final_centavos = _pedir_centavos("Monto final contado ($): ")
    descripcion = _pedir_texto_opcional("Descripción (opcional): ")
    movimiento = servicio_caja.cerrar_caja(monto_final_centavos, descripcion)
    print(f"  Caja cerrada. Movimiento #{movimiento.id} - ${centavos_a_texto(movimiento.monto_centavos)}")


def _registrar_ingreso() -> None:
    print("\n-- Registrar ingreso --")
    monto_centavos = _pedir_centavos("Monto ($): ")
    descripcion = _pedir_texto("Descripción (ej. 'cambio inicial'): ")
    movimiento = servicio_caja.registrar_ingreso(monto_centavos, descripcion)
    print(f"  Ingreso registrado. Movimiento #{movimiento.id} - ${centavos_a_texto(movimiento.monto_centavos)}")


def _registrar_egreso() -> None:
    print("\n-- Registrar egreso --")
    monto_centavos = _pedir_centavos("Monto ($): ")
    descripcion = _pedir_texto("Descripción (ej. 'pago a proveedor'): ")
    movimiento = servicio_caja.registrar_egreso(monto_centavos, descripcion)
    print(f"  Egreso registrado. Movimiento #{movimiento.id} - ${centavos_a_texto(movimiento.monto_centavos)}")


def _menu_caja() -> None:
    while True:
        print("\n=== Caja ===")
        print("1. Abrir caja")
        print("2. Registrar ingreso")
        print("3. Registrar egreso")
        print("4. Cerrar caja")
        print("5. Volver")
        opcion = input("Opción: ").strip()

        if opcion == "1":
            _ejecutar_con_manejo_de_errores(_abrir_caja)
        elif opcion == "2":
            _ejecutar_con_manejo_de_errores(_registrar_ingreso)
        elif opcion == "3":
            _ejecutar_con_manejo_de_errores(_registrar_egreso)
        elif opcion == "4":
            _ejecutar_con_manejo_de_errores(_cerrar_caja)
        elif opcion == "5":
            return
        else:
            print("Opción inválida.")


# --- Alertas de stock -----------------------------------------------------


def _ver_stock_critico() -> None:
    print("\n-- Alertas de stock crítico --")
    productos = servicio_stock.listar_stock_critico()
    if not productos:
        print("  No hay productos con stock crítico.")
        return
    for producto in productos:
        _mostrar_producto(producto)


# --- Menú principal ---------------------------------------------------


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    inicializar_base_datos()

    try:
        while True:
            print("\n=== Kiosco - Menú Principal ===")
            print("1. Productos")
            print("2. Ventas")
            print("3. Caja")
            print("4. Alertas de stock crítico")
            print("5. Salir")
            opcion = input("Opción: ").strip()

            if opcion == "1":
                _menu_productos()
            elif opcion == "2":
                _menu_ventas()
            elif opcion == "3":
                _menu_caja()
            elif opcion == "4":
                _ejecutar_con_manejo_de_errores(_ver_stock_critico)
            elif opcion == "5":
                print("¡Hasta luego!")
                return
            else:
                print("Opción inválida.")
    except (KeyboardInterrupt, EOFError):
        print("\n¡Hasta luego!")


if __name__ == "__main__":
    main()
