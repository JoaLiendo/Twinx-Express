"""Casos de uso de gestión de proveedores (Fase 4A), de su relación con los productos (V1.4) y de su
clasificación por categorías y datos fiscales (V1.13-A).

Orquesta `domain.proveedor`, `domain.producto_proveedor` y sus repositorios. Las reglas
son pocas: el nombre y el CUIT no pueden repetirse, un proveedor con historia (compras,
productos vinculados) no se borra físicamente (se desactiva), la clasificación usa el árbol
de categorías del catálogo y nunca restringe qué se le compra, y los vínculos
producto-proveedor solo los gestiona un OWNER activo. Cuando se informa `usuario_id`, cada
operación deja su registro de auditoría en la misma transacción que el cambio.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from db.conexion import ConexionBD, obtener_conexion
from db.repositorios import auditoria as repositorio_auditoria
from db.repositorios import categorias as repositorio_categorias
from db.repositorios import compras as repositorio_compras
from db.repositorios import movimientos_proveedor as repositorio_movimientos_proveedor
from db.repositorios import producto_proveedor as repositorio_producto_proveedor
from db.repositorios import productos as repositorio_productos
from db.repositorios import proveedores as repositorio_proveedores
from db.repositorios import usuarios as repositorio_usuarios
from domain.categoria import ArbolCategorias, CategoriaEnArbol
from domain.compra import ResumenCompra
from domain.deuda_proveedores import CuentaProveedor, FilaDeudaProveedor, cuenta_vacia
from domain.documento_proveedor import DocumentoProveedor
from domain.evaluacion_proveedor import EvaluacionProveedor, requiere_advertencia
from domain.producto import Producto
from domain.producto_proveedor import ProductoDeProveedor, VinculoProveedor, normalizar_codigo_proveedor
from domain.proveedor import (
    CategoriaDeProveedor,
    MovimientoProveedor,
    Proveedor,
    cambios_de_categorias,
    normalizar_cuit,
    texto_opcional,
)
from domain.reclamo_proveedor import ReclamoProveedor
from domain.usuario import exigir_rol
from excepciones import (
    DatosInvalidosError,
    ProductoNoEncontradoError,
    ProveedorNoEncontradoError,
    VinculoProveedorExistenteError,
    VinculoProveedorNoEncontradoError,
)
from services import (
    servicio_categorias,
    servicio_deuda_proveedores,
    servicio_documentos_proveedor,
    servicio_evaluaciones_proveedor,
    servicio_reclamos_proveedor,
)

logger = logging.getLogger(__name__)

_SOLO_OWNER = frozenset({"OWNER"})


# La ficha muestra solo las compras más recientes (el historial completo, filtrable y paginado, está en /compras).
COMPRAS_EN_FICHA = 20


@dataclass(frozen=True)
class FichaProveedor:
    """Un proveedor con sus productos vinculados, sus últimas compras (más recientes primero,
    activas y anuladas) y su cuenta a pagar completa.

    `saldo_centavos` (V1.9-B) es la deuda pendiente por compras a crédito, derivada del libro
    `movimientos_proveedor` (`SUM(CARGO_COMPRA) - SUM(PAGO) - SUM(REVERSA_COMPRA)`), nunca una
    columna persistida: `0` si el proveedor no tiene ningún movimiento. `movimientos` (V1.9-C) es
    ese mismo libro completo, del más reciente al más antiguo. `cuenta` (V1.10-E) es el resumen de cuentas a
    pagar (saldo real y estimaciones FIFO); `saldo_centavos` sale de esa misma lectura consistente."""

    proveedor: Proveedor
    productos: list[ProductoDeProveedor]
    compras: list[ResumenCompra]
    categorias: list[CategoriaDeProveedor] = field(default_factory=list)
    evaluaciones: list[EvaluacionProveedor] = field(default_factory=list)
    reclamos: list[ReclamoProveedor] = field(default_factory=list)
    documentos: list[DocumentoProveedor] = field(default_factory=list)
    total_compras: int = 0
    saldo_centavos: int = 0
    movimientos: list[MovimientoProveedor] = field(default_factory=list)
    cuenta: CuentaProveedor = field(default_factory=cuenta_vacia)

    @property
    def evaluacion_actual(self) -> EvaluacionProveedor | None:
        """La más reciente (V1.13-B): su estrella es la calificación actual del proveedor."""
        return self.evaluaciones[0] if self.evaluaciones else None

    @property
    def reclamos_abiertos(self) -> list[ReclamoProveedor]:
        """Pendientes o reconocidos (V1.13-C), del más reciente al más antiguo."""
        return [reclamo for reclamo in self.reclamos if reclamo.abierto]


def _auditar(
    conexion: ConexionBD, usuario_id: int | None, accion: str, entidad: str, entidad_id: int, resumen: str
) -> None:
    if usuario_id is not None:
        repositorio_auditoria.registrar_en_conexion(conexion, usuario_id, accion, entidad, entidad_id, resumen)


@dataclass(frozen=True)
class DatosFiscales:
    """Datos fiscales declarados del proveedor (V1.13-A), tal como llegan del formulario: el CUIT puede
    traer guiones y los textos espacios; `_construir_proveedor` los normaliza y valida."""

    razon_social: str | None = None
    cuit: str | None = None
    condicion_fiscal: str | None = None
    ingresos_brutos: str | None = None
    convenio_multilateral: bool = False


@dataclass(frozen=True)
class ResumenProveedor:
    """Un proveedor del listado con su clasificación (V1.13-A), su calificación actual (V1.13-B, `None` si
    nunca se lo evaluó), sus reclamos abiertos (V1.13-C) y su deuda (V1.13-D: la fila del reporte de deuda
    de V1.10, `None` si no debe nada). Son dimensiones independientes: nada las combina en un puntaje."""

    proveedor: Proveedor
    categorias: list[CategoriaDeProveedor]
    calificacion: int | None = None
    reclamos_abiertos: int = 0
    deuda: FilaDeudaProveedor | None = None

    @property
    def requiere_advertencia(self) -> bool:
        return requiere_advertencia(self.calificacion)


# Atajos del listado (V1.13-D): cada uno mira UNA dimensión; se combinan con búsqueda y categoría.
FILTROS_LISTADO = {
    "": "Todos",
    "baja_calificacion": "Con baja calificación",
    "reclamos_abiertos": "Con reclamos abiertos",
    "con_deuda": "Con deuda",
}


def _cumple_filtro(resumen: ResumenProveedor, filtro: str) -> bool:
    if filtro == "baja_calificacion":
        return resumen.requiere_advertencia
    if filtro == "reclamos_abiertos":
        return resumen.reclamos_abiertos > 0
    if filtro == "con_deuda":
        return resumen.deuda is not None
    return True


def _construir_proveedor(
    nombre: str,
    contacto_nombre: str | None,
    telefono: str | None,
    email: str | None,
    direccion: str | None,
    notas: str | None,
    fiscales: DatosFiscales,
    proveedor_id: int | None = None,
) -> Proveedor:
    return Proveedor(
        id=proveedor_id,
        nombre=nombre,
        contacto_nombre=contacto_nombre or None,
        telefono=telefono or None,
        email=email or None,
        direccion=direccion or None,
        notas=notas or None,
        razon_social=texto_opcional(fiscales.razon_social),
        cuit=normalizar_cuit(fiscales.cuit),
        condicion_fiscal=texto_opcional(fiscales.condicion_fiscal),
        ingresos_brutos=texto_opcional(fiscales.ingresos_brutos),
        convenio_multilateral=fiscales.convenio_multilateral,
    )


def _aplicar_categorias(
    conexion: ConexionBD, usuario_id: int | None, proveedor: Proveedor, categoria_ids: Iterable[int]
) -> None:
    """Deja al proveedor clasificado exactamente en `categoria_ids` y audita el cambio, si lo hubo."""
    arbol = ArbolCategorias(repositorio_categorias.listar_todas_en_conexion(conexion))
    actuales = repositorio_proveedores.ids_categorias_en_conexion(conexion, proveedor.id)
    agregar, quitar = cambios_de_categorias(categoria_ids, actuales, arbol)
    if not agregar and not quitar:
        return
    repositorio_proveedores.reemplazar_categorias_en_conexion(conexion, proveedor.id, agregar, quitar)
    finales = sorted(arbol.ruta(categoria_id) for categoria_id in (actuales | agregar) - quitar)
    _auditar(
        conexion, usuario_id, "PROVEEDOR_CATEGORIAS_CAMBIADAS", "PROVEEDOR", proveedor.id,
        f"{proveedor.nombre}: categorías {', '.join(finales) or '(ninguna)'}",
    )


def crear_proveedor(
    nombre: str,
    contacto_nombre: str | None = None,
    telefono: str | None = None,
    email: str | None = None,
    direccion: str | None = None,
    notas: str | None = None,
    usuario_id: int | None = None,
    fiscales: DatosFiscales = DatosFiscales(),
    categoria_ids: Iterable[int] = (),
) -> Proveedor:
    """Da de alta un nuevo proveedor, con sus datos fiscales y su clasificación por categorías.

    Raises:
        DatosInvalidosError: si el nombre está vacío, un dato fiscal es inválido (CUIT mal formado,
            condición inexistente) o una categoría elegida no existe o está dada de baja.
        NombreProveedorDuplicadoError: si ya existe un proveedor con ese nombre.
        CuitProveedorDuplicadoError: si ya existe un proveedor con ese CUIT.
    """
    proveedor = _construir_proveedor(nombre, contacto_nombre, telefono, email, direccion, notas, fiscales)
    with obtener_conexion(inmediata=True) as conexion:
        proveedor_creado = repositorio_proveedores.crear_proveedor_en_conexion(conexion, proveedor)
        _auditar(
            conexion, usuario_id, "PROVEEDOR_CREADO", "PROVEEDOR", proveedor_creado.id,
            f"Proveedor creado: {proveedor_creado.nombre}",
        )
        _aplicar_categorias(conexion, usuario_id, proveedor_creado, categoria_ids)
    logger.info("Proveedor creado: %s (id=%s)", proveedor_creado.nombre, proveedor_creado.id)
    return proveedor_creado


def obtener_por_id(proveedor_id: int) -> Proveedor | None:
    """Busca un proveedor activo por id (pantallas de edición)."""
    return repositorio_proveedores.obtener_por_id(proveedor_id)


def obtener_incluyendo_inactivos(proveedor_id: int) -> Proveedor:
    """Un proveedor activo o dado de baja (reclamos sobre compras históricas).

    Raises:
        ProveedorNoEncontradoError: si no existe.
    """
    proveedor = repositorio_proveedores.obtener_por_id_incluyendo_inactivos(proveedor_id)
    if proveedor is None:
        raise ProveedorNoEncontradoError(f"No existe un proveedor con id {proveedor_id}.")
    return proveedor


def listar_activos() -> list[Proveedor]:
    """Devuelve los proveedores activos (listado normal)."""
    return repositorio_proveedores.listar_activos()


def listar_todos() -> list[Proveedor]:
    """Devuelve todos los proveedores, activos e inactivos (vista "dados de baja")."""
    return repositorio_proveedores.listar_todos()


def buscar_proveedores(
    texto: str, incluir_inactivos: bool = False, categoria_id: int | None = None
) -> list[Proveedor]:
    """Proveedores cuyo nombre, razón social, contacto, teléfono, email o CUIT contienen `texto` (vacío =
    todos los activos, o todos con `incluir_inactivos`). Con `categoria_id`, solo los clasificados en esa
    categoría o en alguna de sus subcategorías (mismo criterio que el filtro de productos)."""
    categoria_ids = None
    if categoria_id is not None:
        categoria_ids = servicio_categorias.obtener_arbol().ids_con_descendientes(categoria_id)
    return repositorio_proveedores.buscar(texto, incluir_inactivos, categoria_ids)


def _categorias_de(ids: Iterable[int], arbol: ArbolCategorias) -> list[CategoriaDeProveedor]:
    categorias = [arbol.obtener(categoria_id) for categoria_id in ids]
    return sorted(
        (
            CategoriaDeProveedor(c.id, c.nombre, arbol.ruta(c.id), c.activa)
            for c in categorias
            if c is not None
        ),
        key=lambda c: c.ruta.casefold(),
    )


def opciones_de_clasificacion(elegidas: Iterable[int] = ()) -> list[CategoriaEnArbol]:
    """Categorías que ofrece el formulario del proveedor, en orden de árbol: todas las activas y, además,
    las dadas de baja que ya lo clasificaban (para que editarlo no las quite sin que nadie lo pida)."""
    arbol = servicio_categorias.obtener_arbol()
    elegidas_set = set(elegidas)
    return [
        opcion
        for opcion in arbol.en_orden()
        if opcion.categoria.id in elegidas_set or all(c.activa for c in arbol.ancestros(opcion.categoria.id))
    ]


def ids_categorias(proveedor_id: int) -> set[int]:
    return set(repositorio_proveedores.categorias_por_proveedor([proveedor_id]).get(proveedor_id, []))


def listar_resumen(
    texto: str = "", incluir_inactivos: bool = False, categoria_id: int | None = None, filtro: str = ""
) -> list[ResumenProveedor]:
    """Listado de proveedores (mismos filtros que `buscar_proveedores`) con su clasificación, calificación,
    reclamos abiertos y deuda; `filtro` (ver `FILTROS_LISTADO`) deja solo los que cumplen esa dimensión.

    Raises:
        DatosInvalidosError: si el filtro no es uno de los admitidos.
    """
    if filtro not in FILTROS_LISTADO:
        raise DatosInvalidosError(f"Filtro de proveedores inválido: {filtro!r}.")
    proveedores = buscar_proveedores(texto, incluir_inactivos, categoria_id)
    arbol = servicio_categorias.obtener_arbol()
    ids = [p.id for p in proveedores]
    por_proveedor = repositorio_proveedores.categorias_por_proveedor(ids)
    calificaciones = servicio_evaluaciones_proveedor.calificaciones_actuales(ids)
    abiertos = servicio_reclamos_proveedor.abiertos_por_proveedor()
    deudas = {fila.proveedor_id: fila for fila in servicio_deuda_proveedores.generar_reporte_deuda_proveedores().filas}
    resumenes = [
        ResumenProveedor(
            p,
            _categorias_de(por_proveedor.get(p.id, []), arbol),
            calificaciones.get(p.id),
            abiertos.get(p.id, 0),
            deudas.get(p.id),
        )
        for p in proveedores
    ]
    return [resumen for resumen in resumenes if _cumple_filtro(resumen, filtro)]


def obtener_ficha(proveedor_id: int) -> FichaProveedor:
    """Ficha de un proveedor (activo o dado de baja): datos, productos vinculados e historial de compras.

    Raises:
        ProveedorNoEncontradoError: si no existe un proveedor con ese id.
    """
    proveedor = repositorio_proveedores.obtener_por_id_incluyendo_inactivos(proveedor_id)
    if proveedor is None:
        raise ProveedorNoEncontradoError(f"No existe un proveedor con id {proveedor_id}.")
    cuenta = servicio_deuda_proveedores.generar_cuenta_proveedor(proveedor_id)
    categoria_ids = repositorio_proveedores.categorias_por_proveedor([proveedor_id]).get(proveedor_id, [])
    return FichaProveedor(
        proveedor=proveedor,
        categorias=_categorias_de(categoria_ids, servicio_categorias.obtener_arbol()),
        evaluaciones=servicio_evaluaciones_proveedor.historial(proveedor_id),
        reclamos=servicio_reclamos_proveedor.listar_de_proveedor(proveedor_id),
        documentos=servicio_documentos_proveedor.listar_de_proveedor(proveedor_id),
        productos=repositorio_producto_proveedor.listar_por_proveedor(proveedor_id),
        compras=repositorio_compras.listar_resumen(proveedor_id, limite=COMPRAS_EN_FICHA),
        total_compras=repositorio_compras.contar_resumen(proveedor_id),
        saldo_centavos=cuenta.saldo_centavos,
        movimientos=repositorio_movimientos_proveedor.listar_movimientos(proveedor_id),
        cuenta=cuenta,
    )


def actualizar_proveedor(
    proveedor_id: int,
    nombre: str,
    contacto_nombre: str | None = None,
    telefono: str | None = None,
    email: str | None = None,
    direccion: str | None = None,
    notas: str | None = None,
    usuario_id: int | None = None,
    fiscales: DatosFiscales = DatosFiscales(),
    categoria_ids: Iterable[int] | None = None,
) -> Proveedor:
    """Actualiza los datos editables de un proveedor existente y, si se indica `categoria_ids`, deja su
    clasificación exactamente en esas categorías (`None` = no la toca).

    Raises:
        ProveedorNoEncontradoError: si no existe un proveedor activo con ese id.
        DatosInvalidosError: si el nombre está vacío, un dato fiscal es inválido o una categoría nueva
            no existe o está dada de baja.
        NombreProveedorDuplicadoError: si el nombre ya pertenece a otro proveedor.
        CuitProveedorDuplicadoError: si el CUIT ya pertenece a otro proveedor.
    """
    proveedor_actualizado = _construir_proveedor(
        nombre, contacto_nombre, telefono, email, direccion, notas, fiscales, proveedor_id=proveedor_id
    )
    with obtener_conexion(inmediata=True) as conexion:
        resultado = repositorio_proveedores.actualizar_datos_en_conexion(conexion, proveedor_actualizado)
        _auditar(
            conexion, usuario_id, "PROVEEDOR_EDITADO", "PROVEEDOR", resultado.id,
            f"Proveedor editado: {resultado.nombre}",
        )
        if categoria_ids is not None:
            _aplicar_categorias(conexion, usuario_id, resultado, categoria_ids)
    logger.info("Proveedor actualizado: %s (id=%s)", resultado.nombre, resultado.id)
    return resultado


def eliminar_proveedor(proveedor_id: int, usuario_id: int | None = None) -> bool:
    """Da de baja un proveedor. Devuelve `True` si la baja fue lógica (tenía compras o productos vinculados).

    Raises:
        ProveedorNoEncontradoError: si no existe un proveedor activo con ese id.
    """
    with obtener_conexion(inmediata=True) as conexion:
        proveedor = repositorio_proveedores.obtener_por_id_en_conexion(conexion, proveedor_id)
        if proveedor is None:
            raise ProveedorNoEncontradoError(f"No existe un proveedor activo con id {proveedor_id}.")
        baja_logica = repositorio_proveedores.eliminar_proveedor_en_conexion(conexion, proveedor_id)
        detalle = "baja lógica (conserva su historial)" if baja_logica else "eliminado"
        _auditar(conexion, usuario_id, "PROVEEDOR_BAJA", "PROVEEDOR", proveedor_id, f"{proveedor.nombre}: {detalle}")
    logger.info("Proveedor id=%s dado de baja (baja_logica=%s)", proveedor_id, baja_logica)
    return baja_logica


def reactivar_proveedor(proveedor_id: int, usuario_id: int | None = None) -> Proveedor:
    """Reactiva un proveedor dado de baja lógica.

    Raises:
        ProveedorNoEncontradoError: si no existe un proveedor inactivo con ese id.
    """
    with obtener_conexion(inmediata=True) as conexion:
        proveedor = repositorio_proveedores.reactivar_proveedor_en_conexion(conexion, proveedor_id)
        _auditar(
            conexion, usuario_id, "PROVEEDOR_REACTIVADO", "PROVEEDOR", proveedor_id,
            f"Proveedor reactivado: {proveedor.nombre}",
        )
    logger.info("Proveedor reactivado: %s (id=%s)", proveedor.nombre, proveedor.id)
    return proveedor


# --- relación producto-proveedor (V1.4) ------------------------------------------------------------


def _resolver_para_vinculo(
    conexion: ConexionBD, usuario_id: int, proveedor_id: int, producto_id: int
) -> tuple[Proveedor, Producto]:
    """Exige un OWNER activo y resuelve proveedor y producto (ambos activos) dentro de la transacción."""
    exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
    proveedor = repositorio_proveedores.obtener_por_id_en_conexion(conexion, proveedor_id)
    if proveedor is None:
        raise ProveedorNoEncontradoError(f"No existe un proveedor activo con id {proveedor_id}.")
    producto = repositorio_productos.obtener_por_id_en_conexion(conexion, producto_id)
    if producto is None:
        raise ProductoNoEncontradoError(f"No existe un producto activo con id {producto_id}.")
    return proveedor, producto


def vincular_producto(
    proveedor_id: int, producto_id: int, usuario_id: int, codigo_proveedor: str | None = None
) -> VinculoProveedor:
    """Vincula un producto activo a un proveedor activo. Queda principal solo si el producto
    todavía no tenía un principal.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        ProveedorNoEncontradoError / ProductoNoEncontradoError: si no existen o están inactivos.
        VinculoProveedorExistenteError: si ya estaban vinculados.
        DatosInvalidosError: si el código del proveedor es demasiado largo.
    """
    codigo = normalizar_codigo_proveedor(codigo_proveedor)
    with obtener_conexion(inmediata=True) as conexion:
        proveedor, producto = _resolver_para_vinculo(conexion, usuario_id, proveedor_id, producto_id)
        if repositorio_producto_proveedor.obtener_vinculo_en_conexion(conexion, producto_id, proveedor_id):
            raise VinculoProveedorExistenteError(
                f"'{producto.nombre}' ya está vinculado al proveedor '{proveedor.nombre}'."
            )
        vinculo = repositorio_producto_proveedor.crear_vinculo_en_conexion(
            conexion, producto_id, proveedor_id, codigo
        )
        principal = " (principal)" if vinculo.es_principal else ""
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "PRODUCTO_PROVEEDOR_VINCULADO", "PRODUCTO_PROVEEDOR", vinculo.id,
            f"{producto.nombre} vinculado a {proveedor.nombre}{principal}",
        )
    logger.info("Producto %s vinculado al proveedor %s por usuario %s", producto_id, proveedor_id, usuario_id)
    return vinculo


def quitar_vinculo(proveedor_id: int, producto_id: int, usuario_id: int) -> None:
    """Quita el vínculo producto-proveedor. No promueve a otro proveedor como principal.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        VinculoProveedorNoEncontradoError: si no estaban vinculados.
    """
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
        vinculo = repositorio_producto_proveedor.obtener_vinculo_en_conexion(conexion, producto_id, proveedor_id)
        if vinculo is None:
            raise VinculoProveedorNoEncontradoError("El producto no está vinculado a ese proveedor.")
        repositorio_producto_proveedor.quitar_vinculo_en_conexion(conexion, producto_id, proveedor_id)
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "PRODUCTO_PROVEEDOR_QUITADO", "PRODUCTO_PROVEEDOR", vinculo.id,
            f"Producto {producto_id} desvinculado del proveedor {proveedor_id}",
        )
    logger.info("Producto %s desvinculado del proveedor %s por usuario %s", producto_id, proveedor_id, usuario_id)


def establecer_principal(proveedor_id: int, producto_id: int, usuario_id: int) -> VinculoProveedor:
    """Deja al proveedor como el único principal del producto (acción explícita del OWNER). Si ya
    lo era, no cambia nada ni audita.

    Raises:
        PermisoDenegadoError: si `usuario_id` no es un OWNER activo.
        VinculoProveedorNoEncontradoError: si no estaban vinculados.
    """
    with obtener_conexion(inmediata=True) as conexion:
        exigir_rol(repositorio_usuarios.obtener_por_id_en_conexion(conexion, usuario_id), _SOLO_OWNER)
        vinculo = repositorio_producto_proveedor.obtener_vinculo_en_conexion(conexion, producto_id, proveedor_id)
        if vinculo is None:
            raise VinculoProveedorNoEncontradoError("El producto no está vinculado a ese proveedor.")
        if vinculo.es_principal:
            return vinculo
        repositorio_producto_proveedor.establecer_principal_en_conexion(conexion, producto_id, proveedor_id)
        repositorio_auditoria.registrar_en_conexion(
            conexion, usuario_id, "PRODUCTO_PROVEEDOR_PRINCIPAL", "PRODUCTO_PROVEEDOR", vinculo.id,
            f"Proveedor {proveedor_id} es ahora el principal del producto {producto_id}",
        )
        actualizado = repositorio_producto_proveedor.obtener_vinculo_en_conexion(conexion, producto_id, proveedor_id)
    logger.info("Proveedor %s principal del producto %s (usuario %s)", proveedor_id, producto_id, usuario_id)
    return actualizado
