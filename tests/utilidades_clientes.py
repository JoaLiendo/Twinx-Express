"""Ayudas compartidas por las pruebas de clientes y cuenta corriente (migración 020)."""

import sqlite3

from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from services import servicio_stock


def crear_usuario(nombre_usuario: str = "cajera", rol: str = "CASHIER", activo: bool = True) -> Usuario:
    usuario = repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario=nombre_usuario, nombre_completo=nombre_usuario.title(), password_hash="hash", rol=rol)
    )
    if not activo:
        usuario = repositorio_usuarios.actualizar_activo(usuario.id, False)
    return usuario


def usuario_logueado(rol: str, nombre_usuario: str) -> tuple[Usuario, dict[str, str]]:
    """Usuario nuevo con sesión iniciada: `(usuario, cookies)` listo para `solicitud(..., cookies=cookies)`."""
    from interfaces.web.auth import NOMBRE_COOKIE_SESION
    from services import servicio_auth

    usuario = repositorio_usuarios.crear_usuario(
        Usuario(
            nombre_usuario=nombre_usuario,
            nombre_completo=nombre_usuario.title(),
            password_hash=servicio_auth.hashear_password("clave-correcta-123", iteraciones=1000),
            rol=rol,
        )
    )
    token = servicio_auth.iniciar_sesion(nombre_usuario, "clave-correcta-123").token
    return usuario, {NOMBRE_COOKIE_SESION: token}


def crear_owner(nombre_usuario: str = "duenio") -> Usuario:
    return crear_usuario(nombre_usuario, "OWNER")


CREDITO_AMPLIO_CENTAVOS = 100_000_000_000


def habilitar_credito_general(owner_id: int, limite_centavos: int = CREDITO_AMPLIO_CENTAVOS) -> None:
    """Configura la política GENERAL de crédito (V1.14-B). Desde V1.14 nadie tiene crédito sin una política
    explícita: los escenarios previos a V1.14 que venden a cuenta la declaran con un límite amplio."""
    from services import servicio_credito

    actual = next(p for p in servicio_credito.listar_politicas_globales() if p.ambito == "GENERAL")
    servicio_credito.configurar_politica("GENERAL", "LIMITE", limite_centavos, owner_id, version_esperada=actual.version)


def crear_producto(codigo: str = "7790000000001", precio_centavos: int = 100, stock: int = 100):
    return servicio_stock.registrar_producto(codigo, f"Producto {codigo}", 10, precio_centavos, stock_actual=stock)


def heredar_existencia_en_conexion(conexion, producto_id: int, cantidad: int) -> None:
    """Partida HEREDADA de `cantidad` unidades, exactamente como la crea la migración 032 para el stock
    anterior a V1.12. Para tests que fabrican historia previa (un producto o una compra creados por el
    repositorio, sin pasar por las partidas): así el escenario es el mismo que deja un upgrade real."""
    conexion.execute(
        "INSERT INTO partidas_stock (producto_id, origen, cantidad_original, cantidad_disponible)"
        " VALUES (?, 'HEREDADA', ?, ?)",
        (producto_id, cantidad, cantidad),
    )


def conectar(ruta) -> sqlite3.Connection:
    """Conexión directa con FK activas, para inspeccionar o forzar escrituras que el
    servicio no permitiría (y comprobar que el esquema igual las rechaza)."""
    conexion = sqlite3.connect(str(ruta))
    conexion.execute("PRAGMA foreign_keys = ON")
    conexion.row_factory = sqlite3.Row
    return conexion


def consultar(ruta, sql: str, parametros=()) -> list[sqlite3.Row]:
    conexion = conectar(ruta)
    try:
        return conexion.execute(sql, parametros).fetchall()
    finally:
        conexion.close()


def contar(ruta, tabla: str, donde: str = "1 = 1") -> int:
    return consultar(ruta, f"SELECT COUNT(*) AS n FROM {tabla} WHERE {donde}")[0]["n"]


def escribir(ruta, sql: str, parametros=()) -> None:
    """Ejecuta una escritura directa (con FK activas) y confirma; deja que la excepción suba."""
    conexion = conectar(ruta)
    try:
        conexion.execute(sql, parametros)
        conexion.commit()
    finally:
        conexion.close()


def forzar_cliente_inactivo(ruta, cliente_id: int) -> None:
    """Deja inactivo a un cliente aunque tenga saldo, saltando el trigger de baja.

    La aplicación nunca produce este estado (la baja con saldo > 0 está bloqueada); se arma a mano
    solo para probar que un cliente inactivo con deuda puede pagarla.
    """
    conexion = conectar(ruta)
    try:
        conexion.execute("DROP TRIGGER trg_clientes_no_desactivar_con_saldo")
        conexion.execute("UPDATE clientes SET activo = 0 WHERE id = ?", (cliente_id,))
        conexion.commit()
    finally:
        conexion.close()


def violaciones_de_invariantes(ruta) -> list[str]:
    """Invariantes globales de la cuenta corriente (migración 020; obligaciones e imputaciones desde V1.14,
    migración 039). Devuelve una descripción por cada violación encontrada; una base consistente devuelve `[]`.

    Son las que el esquema no puede exigir por sí solo al insertar (cada fila se valida contra lo que ya
    existe, no contra lo que todavía no se insertó) y que hoy garantiza que los servicios escriban ambas
    patas en una única transacción.
    """
    violaciones: list[str] = []

    def buscar(descripcion: str, sql: str) -> None:
        for fila in consultar(ruta, sql):
            violaciones.append(f"{descripcion}: {tuple(fila)}")

    buscar(
        "venta a cuenta sin exactamente un CARGO",
        """
        SELECT v.id, COUNT(m.id) AS cargos FROM ventas v
        LEFT JOIN movimientos_cuenta m ON m.venta_id = v.id AND m.tipo = 'CARGO'
        WHERE v.tipo_pago = 'CUENTA_CORRIENTE' GROUP BY v.id HAVING COUNT(m.id) <> 1
        """,
    )
    buscar(
        "CARGO que no corresponde a una venta a cuenta",
        """
        SELECT m.id FROM movimientos_cuenta m LEFT JOIN ventas v ON v.id = m.venta_id
        WHERE m.tipo = 'CARGO' AND (v.id IS NULL OR v.tipo_pago <> 'CUENTA_CORRIENTE')
        """,
    )
    buscar(
        "monto del CARGO distinto de la porción financiada de la venta o cliente distinto",
        """
        SELECT m.id, m.monto_centavos, v.total_centavos, v.pago_contado_centavos
        FROM movimientos_cuenta m JOIN ventas v ON v.id = m.venta_id
        WHERE m.tipo = 'CARGO'
          AND (m.monto_centavos <> v.total_centavos - v.pago_contado_centavos OR m.cliente_id IS NOT v.cliente_id)
        """,
    )
    # --- V1.14: obligaciones e imputaciones (invariantes financieras 1-8) ---
    buscar(
        "saldo del cliente distinto de la suma de saldos pendientes de sus obligaciones",
        """
        SELECT l.cliente_id, l.saldo, COALESCE(o.pendiente, 0) FROM
            (SELECT cliente_id, SUM(CASE tipo WHEN 'CARGO' THEN monto_centavos ELSE -monto_centavos END) AS saldo
             FROM movimientos_cuenta GROUP BY cliente_id) l
        LEFT JOIN (SELECT cliente_id, SUM(saldo_pendiente_centavos) AS pendiente FROM obligaciones_cliente
                   GROUP BY cliente_id) o ON o.cliente_id = l.cliente_id
        WHERE l.saldo <> COALESCE(o.pendiente, 0)
        """,
    )
    buscar(
        "obligación con monto distinto de imputado + saldo pendiente, o saldo fuera de rango",
        """
        SELECT o.id FROM obligaciones_cliente o
        WHERE o.saldo_pendiente_centavos < 0 OR o.saldo_pendiente_centavos > o.monto_original_centavos
           OR o.monto_original_centavos <> o.saldo_pendiente_centavos
              + (SELECT COALESCE(SUM(i.monto_centavos), 0) FROM imputaciones_cobro i WHERE i.obligacion_id = o.id)
        """,
    )
    buscar(
        "cobro imputado por un monto distinto del cobro (de más o de menos: no hay saldo a favor)",
        """
        SELECT m.id, m.monto_centavos FROM movimientos_cuenta m
        WHERE m.tipo = 'COBRO' AND m.monto_centavos <>
              (SELECT COALESCE(SUM(i.monto_centavos), 0) FROM imputaciones_cobro i WHERE i.movimiento_cobro_id = m.id)
        """,
    )
    buscar(
        "venta con financiación sin exactamente su obligación causal (monto, cliente y CARGO)",
        """
        SELECT v.id FROM ventas v
        LEFT JOIN obligaciones_cliente o ON o.venta_id = v.id
        LEFT JOIN movimientos_cuenta m ON m.id = o.movimiento_cargo_id
        WHERE v.tipo_pago = 'CUENTA_CORRIENTE'
          AND (o.id IS NULL OR o.monto_original_centavos <> v.total_centavos - v.pago_contado_centavos
               OR o.cliente_id IS NOT v.cliente_id OR m.venta_id IS NOT v.id)
        """,
    )
    buscar(
        "venta sin financiación con obligación o con pago al contado",
        """
        SELECT v.id FROM ventas v
        WHERE v.tipo_pago <> 'CUENTA_CORRIENTE'
          AND (v.pago_contado_centavos <> 0 OR EXISTS (SELECT 1 FROM obligaciones_cliente o WHERE o.venta_id = v.id))
        """,
    )
    buscar(
        "venta mixta cuyo contado + crédito no es el total",
        """
        SELECT v.id FROM ventas v JOIN obligaciones_cliente o ON o.venta_id = v.id
        WHERE v.pago_contado_centavos + o.monto_original_centavos <> v.total_centavos
           OR v.pago_contado_centavos >= v.total_centavos
        """,
    )
    buscar(
        "COBRO sin exactamente un INGRESO COBRO_CUENTA",
        """
        SELECT m.id, COUNT(c.id) AS ingresos FROM movimientos_cuenta m
        LEFT JOIN caja_movimientos c
               ON c.id = m.caja_movimiento_id AND c.tipo = 'INGRESO' AND c.origen = 'COBRO_CUENTA'
        WHERE m.tipo = 'COBRO' GROUP BY m.id HAVING COUNT(c.id) <> 1
        """,
    )
    buscar(
        "INGRESO COBRO_CUENTA sin exactamente un COBRO",
        """
        SELECT c.id, COUNT(m.id) AS cobros FROM caja_movimientos c
        LEFT JOIN movimientos_cuenta m ON m.caja_movimiento_id = c.id AND m.tipo = 'COBRO'
        WHERE c.origen = 'COBRO_CUENTA' GROUP BY c.id HAVING COUNT(m.id) <> 1
        """,
    )
    buscar(
        "monto del COBRO distinto del monto de su INGRESO",
        """
        SELECT m.id, m.monto_centavos, c.monto_centavos FROM movimientos_cuenta m
        JOIN caja_movimientos c ON c.id = m.caja_movimiento_id
        WHERE m.tipo = 'COBRO' AND m.monto_centavos <> c.monto_centavos
        """,
    )
    buscar(
        "origen COBRO_CUENTA en un movimiento que no es INGRESO",
        "SELECT id FROM caja_movimientos WHERE origen = 'COBRO_CUENTA' AND tipo <> 'INGRESO'",
    )
    buscar(
        "saldo negativo (a cualquier altura del libro)",
        """
        SELECT m.id, m.cliente_id FROM movimientos_cuenta m
        WHERE (SELECT SUM(CASE x.tipo WHEN 'CARGO' THEN x.monto_centavos ELSE -x.monto_centavos END)
               FROM movimientos_cuenta x WHERE x.cliente_id = m.cliente_id AND x.id <= m.id) < 0
        """,
    )
    return violaciones


def saldos_esperados_del_libro(ruta) -> dict[int, int]:
    """`SUM(CARGO) - SUM(COBRO)` por cliente, calculado aparte del repositorio (en Python, fila por
    fila), para compararlo con el saldo que informa `db.repositorios.clientes.obtener_saldo`."""
    saldos: dict[int, int] = {}
    for fila in consultar(ruta, "SELECT cliente_id, tipo, monto_centavos FROM movimientos_cuenta"):
        signo = 1 if fila["tipo"] == "CARGO" else -1
        saldos[fila["cliente_id"]] = saldos.get(fila["cliente_id"], 0) + signo * fila["monto_centavos"]
    return saldos
