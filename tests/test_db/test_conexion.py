"""Pruebas de configuración de db.conexion.obtener_conexion (Fase 5A).

`busy_timeout` no garantiza la idempotencia (eso lo hace el UNIQUE de
`clave_idempotencia`): sirve para que, ante una carrera real de
escritura entre dos transacciones, la que pierde el lock espere en vez
de fallar de inmediato con "database is locked" (ver diseño de 5A).
"""

import sqlite3
import threading
import time

import pytest

from db.conexion import obtener_conexion
from excepciones import ErrorBaseDatos


def test_busy_timeout_esta_configurado(base_datos_temporal):
    with obtener_conexion() as conexion:
        valor = conexion.execute("PRAGMA busy_timeout").fetchone()[0]

    assert valor == 5000


def test_obtener_conexion_inmediata_abre_la_transaccion_de_entrada(base_datos_temporal):
    """`inmediata=True` debe tomar el lock de escritura (`BEGIN
    IMMEDIATE`) apenas se entra al bloque `with`, no recién antes de la
    primera escritura (comportamiento por defecto de sqlite3) -- ver
    auditoría de concurrencia, Stage D."""
    with obtener_conexion(inmediata=True) as conexion:
        assert conexion.in_transaction is True


def test_obtener_conexion_sin_inmediata_no_abre_transaccion_de_entrada(base_datos_temporal):
    """Comportamiento por defecto sin cambios: no debe tomarse ningún
    lock de escritura solo por leer."""
    with obtener_conexion() as conexion:
        assert conexion.in_transaction is False


def test_otros_errores_de_sqlite_conservan_el_detalle_tecnico(base_datos_temporal):
    """El mensaje amigable de contención (ver test de abajo) es
    específico de "database is locked": cualquier otro error de
    sqlite3 debe seguir mostrando su detalle técnico tal cual, nunca
    ocultarse indiscriminadamente."""
    with pytest.raises(ErrorBaseDatos) as info:
        with obtener_conexion() as conexion:
            conexion.execute("SELECT * FROM tabla_que_no_existe")

    mensaje = str(info.value)
    assert "Intentá nuevamente" not in mensaje
    assert "no such table" in mensaje


def test_conflicto_real_de_locks_agota_el_timeout_y_muestra_mensaje_claro(base_datos_temporal):
    """Reproduce una contención real de escritura con dos conexiones y
    dos hilos reales (nunca con monkeypatch): la conexión A mantiene
    una transacción `BEGIN IMMEDIATE` abierta claramente más tiempo que
    `busy_timeout` (5s); la conexión B, que también pide
    `inmediata=True`, agota ese margen esperando el mismo lock y debe
    recibir un error de dominio con un mensaje claro para el usuario
    final -- nunca el texto técnico crudo de sqlite3. Después de que
    ambos hilos terminan, la base tiene que seguir siendo operable: no
    debe quedar ningún lock permanente.

    A retiene el lock 8s (no apenas 5s): el reintento interno de
    sqlite3 ante SQLITE_BUSY no corta exactamente al milisegundo del
    `busy_timeout` configurado, sino con cierto margen de más
    (comprobado empíricamente: unos cientos de ms sobre los 5000ms
    nominales) -- con un margen chico entre la duración del lock y el
    timeout, esa demora podía superponerse con la liberación real del
    lock y B terminaba teniendo éxito por una carrera de milisegundos
    en vez de fallar. Un margen amplio (8s contra 5s) hace el
    resultado determinista.
    """
    bloqueo_tomado = threading.Event()
    resultado_b = {}

    def mantener_bloqueo():
        with obtener_conexion(inmediata=True) as conexion:
            conexion.execute(
                "INSERT INTO productos "
                "(codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos) "
                "VALUES ('A', 'A', 0, 0)"
            )
            bloqueo_tomado.set()
            time.sleep(8)  # supera con margen amplio busy_timeout=5000ms

    def intentar_escribir_mientras_esta_bloqueado():
        bloqueo_tomado.wait(timeout=5)
        try:
            with obtener_conexion(inmediata=True) as conexion:
                conexion.execute(
                    "INSERT INTO productos "
                    "(codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos) "
                    "VALUES ('B', 'B', 0, 0)"
                )
        except ErrorBaseDatos as error:
            resultado_b["error"] = error

    hilo_a = threading.Thread(target=mantener_bloqueo)
    hilo_b = threading.Thread(target=intentar_escribir_mientras_esta_bloqueado)
    hilo_a.start()
    hilo_b.start()
    hilo_a.join(timeout=20)
    hilo_b.join(timeout=20)

    assert not hilo_a.is_alive()
    assert not hilo_b.is_alive()
    assert "error" in resultado_b
    mensaje = str(resultado_b["error"])
    assert "database is locked" not in mensaje
    assert "Intentá nuevamente" in mensaje

    # No queda ningún lock permanente: una operación normal después de
    # la contención tiene que funcionar sin problemas.
    with obtener_conexion() as conexion:
        total = conexion.execute("SELECT COUNT(*) FROM productos").fetchone()[0]
    assert total == 1  # solo el INSERT de A, que sí llegó a comprometer


# ---------------------------------------------------------------------------
# hay_migraciones_pendientes_en_base_existente
# ---------------------------------------------------------------------------


@pytest.fixture
def base_con_migraciones_propias(tmp_path, monkeypatch):
    """DB y directorio de migraciones temporales, con una sola migración
    (001) escrita y la base todavía inexistente."""
    import db.conexion as modulo

    dir_migraciones = tmp_path / "migraciones"
    dir_migraciones.mkdir()
    (dir_migraciones / "001_a.sql").write_text("CREATE TABLE a (id INTEGER PRIMARY KEY);", encoding="utf-8")
    monkeypatch.setattr(modulo, "RUTA_BASE_DATOS", tmp_path / "kiosco.db")
    monkeypatch.setattr(modulo, "DIRECTORIO_MIGRACIONES", dir_migraciones)
    return modulo, dir_migraciones


def test_pendientes_es_false_si_la_base_no_existe_y_no_la_crea(base_con_migraciones_propias):
    modulo, _ = base_con_migraciones_propias

    assert modulo.hay_migraciones_pendientes_en_base_existente() is False
    assert not modulo.RUTA_BASE_DATOS.exists()


def test_pendientes_es_false_si_el_archivo_existe_pero_no_tiene_tablas(base_con_migraciones_propias):
    modulo, _ = base_con_migraciones_propias
    modulo.RUTA_BASE_DATOS.write_bytes(b"")

    assert modulo.hay_migraciones_pendientes_en_base_existente() is False


def test_pendientes_es_false_si_todas_las_migraciones_estan_aplicadas(base_con_migraciones_propias):
    modulo, _ = base_con_migraciones_propias
    modulo.inicializar_base_datos()

    assert modulo.hay_migraciones_pendientes_en_base_existente() is False


def test_pendientes_es_true_si_hay_una_migracion_sin_aplicar(base_con_migraciones_propias):
    modulo, dir_migraciones = base_con_migraciones_propias
    modulo.inicializar_base_datos()
    (dir_migraciones / "002_b.sql").write_text("CREATE TABLE b (id INTEGER PRIMARY KEY);", encoding="utf-8")

    assert modulo.hay_migraciones_pendientes_en_base_existente() is True


def test_pendientes_es_true_para_base_con_tablas_pero_sin_registro_de_migraciones(base_con_migraciones_propias):
    modulo, _ = base_con_migraciones_propias
    conexion = sqlite3.connect(modulo.RUTA_BASE_DATOS)
    conexion.execute("CREATE TABLE a (id INTEGER PRIMARY KEY)")
    conexion.commit()
    conexion.close()

    assert modulo.hay_migraciones_pendientes_en_base_existente() is True
