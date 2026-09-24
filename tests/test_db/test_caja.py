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


def test_migracion_011_agrega_la_columna_diferencia_centavos(base_datos_temporal):
    with obtener_conexion() as conexion:
        columnas = {fila["name"] for fila in conexion.execute("PRAGMA table_info(caja_movimientos)").fetchall()}
    assert "diferencia_centavos" in columnas


def test_migracion_011_no_inventa_datos_para_cierres_anteriores(tmp_path, monkeypatch):
    """Simula una DB que ya tenía cierres de caja antes de que existiera
    esta migración: aplica solo 001-010 a mano, inserta un CIERRE con el
    esquema viejo, y recién después corre `inicializar_base_datos()`
    completo (aplica 011 en adelante). El cierre viejo debe quedar con
    `diferencia_centavos` en NULL -- nunca una diferencia inventada.
    """
    ruta_bd = tmp_path / "test_kiosco_pre_011.db"
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta_bd)

    rutas_previas = [
        ruta
        for ruta in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql"))
        if ruta.name < "011_diferencia_cierre_caja.sql"
    ]
    assert rutas_previas, "no se encontraron migraciones anteriores a la 011"

    with modulo_conexion.obtener_conexion() as conexion:
        conexion.execute(modulo_conexion._TABLA_MIGRACIONES)
        for ruta in rutas_previas:
            conexion.executescript(ruta.read_text(encoding="utf-8"))
            conexion.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (ruta.name,))
        conexion.execute(
            "INSERT INTO caja_movimientos (tipo, monto_centavos, descripcion) VALUES ('CIERRE', 100000, NULL)"
        )

    modulo_conexion.inicializar_base_datos()

    with modulo_conexion.obtener_conexion() as conexion:
        fila = conexion.execute("SELECT diferencia_centavos FROM caja_movimientos WHERE id = 1").fetchone()
    assert fila["diferencia_centavos"] is None


class TestDiferenciaDeCierre:
    """Migración 011: `registrar_movimiento` persiste `diferencia_centavos`
    tal cual viene en el `MovimientoCaja` -- no la calcula (eso es
    responsabilidad de `services.servicio_caja.cerrar_caja`)."""

    def test_persiste_diferencia_positiva(self, base_datos_temporal):
        repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="CIERRE", monto_centavos=100000, diferencia_centavos=500))

        with obtener_conexion() as conexion:
            fila = conexion.execute("SELECT diferencia_centavos FROM caja_movimientos").fetchone()
        assert fila["diferencia_centavos"] == 500

    def test_persiste_diferencia_negativa(self, base_datos_temporal):
        repositorio_caja.registrar_movimiento(
            MovimientoCaja(tipo="CIERRE", monto_centavos=100000, diferencia_centavos=-500)
        )

        with obtener_conexion() as conexion:
            fila = conexion.execute("SELECT diferencia_centavos FROM caja_movimientos").fetchone()
        assert fila["diferencia_centavos"] == -500

    def test_persiste_diferencia_cero(self, base_datos_temporal):
        """Cero es un resultado real (caja cuadrada) -- se persiste como
        `0`, nunca como `NULL` (que significaría "no calculado")."""
        repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="CIERRE", monto_centavos=100000, diferencia_centavos=0))

        with obtener_conexion() as conexion:
            fila = conexion.execute("SELECT diferencia_centavos FROM caja_movimientos").fetchone()
        assert fila["diferencia_centavos"] == 0

    def test_otros_movimientos_quedan_null(self, base_datos_temporal):
        repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="APERTURA", monto_centavos=100000))
        repositorio_caja.registrar_movimiento(
            MovimientoCaja(tipo="INGRESO", monto_centavos=5000, descripcion="cambio")
        )
        repositorio_caja.registrar_movimiento(
            MovimientoCaja(tipo="EGRESO", monto_centavos=2000, descripcion="pago")
        )

        with obtener_conexion() as conexion:
            filas = conexion.execute("SELECT diferencia_centavos FROM caja_movimientos ORDER BY id").fetchall()
        assert [fila["diferencia_centavos"] for fila in filas] == [None, None, None]

    def test_lectura_devuelve_la_diferencia_correcta(self, base_datos_temporal):
        repositorio_caja.registrar_movimiento(
            MovimientoCaja(tipo="CIERRE", monto_centavos=100000, diferencia_centavos=-300)
        )

        movimientos = repositorio_caja.listar_movimientos()

        assert len(movimientos) == 1
        assert movimientos[0].diferencia_centavos == -300


class TestObtenerFechaUltimaAperturaEnConexion:
    """Migración 013 (anulación de ventas): reconstruye la sesión de caja
    vigente sin ninguna relación `caja_id` en `ventas` -- solo con
    `caja_movimientos.tipo`/`fecha`/`id`, tal como ya existen."""

    def test_sin_ningun_movimiento_devuelve_none(self, base_datos_temporal):
        with obtener_conexion() as conexion:
            assert repositorio_caja.obtener_sesion_abierta_en_conexion(conexion) is None

    def test_con_caja_abierta_devuelve_la_fecha_de_la_apertura(self, base_datos_temporal):
        apertura = repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="APERTURA", monto_centavos=100000))

        with obtener_conexion() as conexion:
            fecha = repositorio_caja.obtener_sesion_abierta_en_conexion(conexion).fecha_apertura

        assert fecha == apertura.fecha

    def test_con_caja_cerrada_devuelve_none(self, base_datos_temporal):
        repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="APERTURA", monto_centavos=100000))
        repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="CIERRE", monto_centavos=100000, diferencia_centavos=0))

        with obtener_conexion() as conexion:
            assert repositorio_caja.obtener_sesion_abierta_en_conexion(conexion) is None

    def test_ingresos_y_egresos_no_ocultan_la_apertura_vigente(self, base_datos_temporal):
        """La APERTURA sigue siendo el inicio de la sesión aunque haya
        movimientos manuales después -- solo un CIERRE la cierra."""
        apertura = repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="APERTURA", monto_centavos=100000))
        repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="INGRESO", monto_centavos=5000, descripcion="x"))
        repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="EGRESO", monto_centavos=2000, descripcion="y"))

        with obtener_conexion() as conexion:
            fecha = repositorio_caja.obtener_sesion_abierta_en_conexion(conexion).fecha_apertura

        assert fecha == apertura.fecha

    def test_reapertura_despues_de_un_cierre_devuelve_la_nueva_apertura(self, base_datos_temporal):
        """Caso obligatorio de la auditoría de diseño: 10:00 venta / 18:00
        cierre / 20:00 apertura -- la sesión vigente es la de las 20:00,
        no la de las 10:00."""
        repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="APERTURA", monto_centavos=100000))
        repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="CIERRE", monto_centavos=100000, diferencia_centavos=0))
        segunda_apertura = repositorio_caja.registrar_movimiento(MovimientoCaja(tipo="APERTURA", monto_centavos=50000))

        with obtener_conexion() as conexion:
            fecha = repositorio_caja.obtener_sesion_abierta_en_conexion(conexion).fecha_apertura

        assert fecha == segunda_apertura.fecha
