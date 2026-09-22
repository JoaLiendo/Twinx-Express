"""Pruebas de integración de db.repositorios.caja: alta de movimientos y
trazabilidad de usuario (migración 010)."""

import db.conexion as modulo_conexion
from db.conexion import obtener_conexion
from db.repositorios import caja as repositorio_caja
from db.repositorios import usuarios as repositorio_usuarios
from domain.caja import MovimientoCaja
from domain.usuario import Usuario


def _crear_usuario(nombre_usuario="cajera1", rol="CASHIER"):
    return repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario=nombre_usuario, nombre_completo="Test", password_hash="hash", rol=rol)
    )


def test_migracion_010_agrega_la_columna_usuario_id(base_datos_temporal):
    with obtener_conexion() as conexion:
        columnas = {fila["name"] for fila in conexion.execute("PRAGMA table_info(caja_movimientos)").fetchall()}
    assert "usuario_id" in columnas


def test_migracion_010_no_inventa_datos_para_movimientos_anteriores(tmp_path, monkeypatch):
    """Simula una DB que ya tenía movimientos de caja antes de que
    existiera esta migración: aplica solo 001-009 a mano, inserta un
    movimiento con el esquema viejo, y recién después corre
    `inicializar_base_datos()` completo (aplica 010 en adelante). La
    fila vieja debe quedar con `usuario_id` en NULL -- nunca inventado.
    """
    ruta_bd = tmp_path / "test_kiosco_pre_010.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta_bd)

    rutas_previas = [
        ruta
        for ruta in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql"))
        if ruta.name < "010_usuario_en_caja.sql"
    ]
    assert rutas_previas, "no se encontraron migraciones anteriores a la 010"

    with modulo_conexion.obtener_conexion() as conexion:
        conexion.execute(modulo_conexion._TABLA_MIGRACIONES)
        for ruta in rutas_previas:
            conexion.executescript(ruta.read_text(encoding="utf-8"))
            conexion.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (ruta.name,))
        conexion.execute(
            "INSERT INTO caja_movimientos (tipo, monto_centavos, descripcion) VALUES ('APERTURA', 100000, NULL)"
        )

    modulo_conexion.inicializar_base_datos()

    with modulo_conexion.obtener_conexion() as conexion:
        fila = conexion.execute("SELECT usuario_id FROM caja_movimientos WHERE id = 1").fetchone()
    assert fila["usuario_id"] is None


def test_registrar_movimiento_persiste_usuario_id(base_datos_temporal):
    usuario = _crear_usuario()
    movimiento = MovimientoCaja(tipo="APERTURA", monto_centavos=100000)

    repositorio_caja.registrar_movimiento(movimiento, usuario_id=usuario.id)

    with obtener_conexion() as conexion:
        fila = conexion.execute("SELECT usuario_id FROM caja_movimientos").fetchone()
    assert fila["usuario_id"] == usuario.id


def test_registrar_movimiento_sin_usuario_id_queda_null(base_datos_temporal):
    """Compatibilidad con el CLI y con los tests existentes de
    `services.servicio_caja`: `usuario_id` es opcional, por defecto
    `None` -- no se inventa ningún usuario."""
    movimiento = MovimientoCaja(tipo="APERTURA", monto_centavos=100000)

    repositorio_caja.registrar_movimiento(movimiento)

    with obtener_conexion() as conexion:
        fila = conexion.execute("SELECT usuario_id FROM caja_movimientos").fetchone()
    assert fila["usuario_id"] is None


def test_owner_y_cashier_quedan_diferenciados(base_datos_temporal):
    owner = _crear_usuario(nombre_usuario="duenio", rol="OWNER")
    cajera = _crear_usuario(nombre_usuario="cajera1", rol="CASHIER")

    repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="APERTURA", monto_centavos=100000), usuario_id=owner.id)
    repositorio_caja.registrar_movimiento(
        MovimientoCaja(tipo="INGRESO", monto_centavos=5000, descripcion="cambio"), usuario_id=cajera.id
    )

    with obtener_conexion() as conexion:
        filas = conexion.execute("SELECT tipo, usuario_id FROM caja_movimientos ORDER BY id").fetchall()
    assert filas[0]["usuario_id"] == owner.id
    assert filas[1]["usuario_id"] == cajera.id


def test_usuario_desactivado_conserva_fk_valida_en_el_historico(base_datos_temporal):
    usuario = _crear_usuario()
    repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="APERTURA", monto_centavos=100000), usuario_id=usuario.id)

    repositorio_usuarios.actualizar_activo(usuario.id, False)

    with obtener_conexion() as conexion:
        fila = conexion.execute("SELECT usuario_id FROM caja_movimientos").fetchone()
    assert fila["usuario_id"] == usuario.id


def test_registrar_movimiento_sigue_devolviendo_el_movimiento_de_dominio(base_datos_temporal):
    """El contrato de retorno no cambia: sigue siendo un `MovimientoCaja`
    sin `usuario_id` expuesto (ver diseño aprobado: no se toca el
    dominio en este bloque)."""
    usuario = _crear_usuario()

    resultado = repositorio_caja.registrar_movimiento(
        MovimientoCaja(tipo="APERTURA", monto_centavos=100000), usuario_id=usuario.id
    )

    assert isinstance(resultado, MovimientoCaja)
    assert resultado.tipo == "APERTURA"
    assert resultado.monto_centavos == 100000
    assert not hasattr(resultado, "usuario_id")
