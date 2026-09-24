"""Migración 019 (sesiones de caja, V1.3): reconstrucción histórica, sesión
LEGADO, backfill, verificaciones transaccionales y triggers.

Cada test arma una base "V1.2" (las migraciones 001-018 con datos históricos
puestos a mano, con fechas explícitas), aplica la 019 con el runner real
(`inicializar_base_datos`) y inspecciona el resultado. Nunca toca `data/kiosco.db`.
"""

import re
import shutil
import sqlite3

import pytest

import db.conexion as modulo_conexion
from db.conexion import obtener_conexion
from excepciones import CajaCerradaError, ErrorBaseDatos, VentaDeCajaCerradaError
from services import servicio_caja, servicio_ventas
from domain.venta import ItemVenta

NOMBRE_019 = "019_sesiones_caja.sql"
RUTA_019 = modulo_conexion.DIRECTORIO_MIGRACIONES / NOMBRE_019

T = "2026-03-01"  # día base de los escenarios


class BaseV12:
    """Base con el esquema de la V1.2 (migraciones anteriores a la 019) y
    métodos para cargar movimientos y ventas históricos con fecha explícita."""

    def __init__(self, ruta, monkeypatch):
        self.ruta = ruta
        monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", ruta)
        self.con = sqlite3.connect(ruta)
        self.con.execute(modulo_conexion._TABLA_MIGRACIONES)
        for migracion in sorted(modulo_conexion.DIRECTORIO_MIGRACIONES.glob("*.sql")):
            if migracion.name < NOMBRE_019:
                self.con.executescript(migracion.read_text(encoding="utf-8"))
                self.con.execute("INSERT INTO schema_migraciones (nombre_archivo) VALUES (?)", (migracion.name,))
        self.con.execute(
            "INSERT INTO productos (codigo_barras, nombre, precio_costo_centavos, precio_venta_centavos,"
            " stock_actual) VALUES ('1', 'Producto', 1, 100, 1000)"
        )
        self.con.commit()

    def usuario(self, nombre="cajera") -> int:
        cursor = self.con.execute(
            "INSERT INTO usuarios (nombre_usuario, nombre_completo, password_hash, rol)"
            " VALUES (?, 'Test', 'hash', 'CASHIER')",
            (nombre,),
        )
        self.con.commit()
        return cursor.lastrowid

    def movimiento(self, fecha, tipo, monto=0, diferencia=None, usuario_id=None, descripcion=None) -> int:
        if tipo in ("INGRESO", "EGRESO") and descripcion is None:
            descripcion = "x"
        cursor = self.con.execute(
            "INSERT INTO caja_movimientos (fecha, tipo, monto_centavos, descripcion, usuario_id, diferencia_centavos)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (fecha, tipo, monto, descripcion, usuario_id, diferencia),
        )
        self.con.commit()
        return cursor.lastrowid

    def venta(
        self, fecha, total=100, estado="ACTIVA", fecha_anulacion=None, usuario_id=None, lineas=1, tipo_pago="EFECTIVO"
    ) -> int:
        cursor = self.con.execute(
            "INSERT INTO ventas (fecha, total_centavos, tipo_pago, estado, fecha_anulacion, usuario_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (fecha, total, tipo_pago, estado, fecha_anulacion, usuario_id),
        )
        venta_id = cursor.lastrowid
        for _ in range(lineas):
            self.con.execute(
                "INSERT INTO detalle_venta (venta_id, producto_id, cantidad, precio_unitario_centavos,"
                " subtotal_centavos) VALUES (?, 1, 1, ?, ?)",
                (venta_id, total // lineas, total // lineas),
            )
        self.con.commit()
        return venta_id

    def migrar(self) -> None:
        self.con.commit()
        self.con.close()
        modulo_conexion.inicializar_base_datos()


@pytest.fixture
def v12(tmp_path, monkeypatch):
    return BaseV12(tmp_path / "v12.db", monkeypatch)


def _consultar(ruta, sql, parametros=()):
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, parametros).fetchall()
    finally:
        con.close()


def _sesiones(ruta):
    return _consultar(ruta, "SELECT * FROM sesiones_caja ORDER BY id")


def _sesion_de_venta(ruta, venta_id):
    return _consultar(
        ruta,
        "SELECT s.* FROM ventas v JOIN sesiones_caja s ON s.id = v.sesion_caja_id WHERE v.id = ?",
        (venta_id,),
    )[0]


def _sesion_de_movimiento(ruta, movimiento_id):
    return _consultar(
        ruta,
        "SELECT s.* FROM caja_movimientos m JOIN sesiones_caja s ON s.id = m.sesion_caja_id WHERE m.id = ?",
        (movimiento_id,),
    )[0]


def _legado(ruta):
    return _consultar(ruta, "SELECT * FROM sesiones_caja WHERE origen = 'LEGADO'")


def _instantanea(ruta):
    """Todo lo observable de la base: esquema, filas comerciales y migraciones registradas."""
    con = sqlite3.connect(ruta)
    try:
        return {
            "esquema": sorted(
                (fila[0], fila[1], fila[2]) for fila in con.execute("SELECT type, name, sql FROM sqlite_master")
            ),
            "ventas": con.execute("SELECT * FROM ventas ORDER BY id").fetchall(),
            "detalle": con.execute("SELECT * FROM detalle_venta ORDER BY id").fetchall(),
            "movimientos": con.execute("SELECT * FROM caja_movimientos ORDER BY id").fetchall(),
            "migraciones": [f[0] for f in con.execute("SELECT nombre_archivo FROM schema_migraciones ORDER BY 1")],
            "secuencias": sorted(con.execute("SELECT name, seq FROM sqlite_sequence").fetchall()),
        }
    finally:
        con.close()


# --- Escenarios básicos ------------------------------------------------------------


def test_v12_sin_ventas_reconstruye_la_sesion_y_no_crea_legado(v12):
    apertura = v12.movimiento(f"{T} 08:00:00", "APERTURA", 5000)
    cierre = v12.movimiento(f"{T} 18:00:00", "CIERRE", 5000, diferencia=0)
    v12.migrar()

    (sesion,) = _sesiones(v12.ruta)
    assert (sesion["estado"], sesion["origen"]) == ("CERRADA", "RECONSTRUIDA")
    assert sesion["fondo_centavos"] == 5000
    assert _sesion_de_movimiento(v12.ruta, apertura)["id"] == sesion["id"]
    assert _sesion_de_movimiento(v12.ruta, cierre)["id"] == sesion["id"]
    assert _legado(v12.ruta) == []
    assert _consultar(v12.ruta, "SELECT COUNT(*) AS n FROM sesiones_caja WHERE estado = 'ABIERTA'")[0]["n"] == 0


def test_ventas_normales_van_a_su_sesion_y_se_conserva_el_cierre(v12):
    usuario = v12.usuario()
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 1000, usuario_id=usuario)
    v1 = v12.venta(f"{T} 09:00:00", 300)
    v2 = v12.venta(f"{T} 10:30:00", 700, tipo_pago="TARJETA")
    v12.movimiento(f"{T} 18:00:00", "CIERRE", 1300, diferencia=-0, usuario_id=usuario)
    v12.migrar()

    (sesion,) = _sesiones(v12.ruta)
    assert _sesion_de_venta(v12.ruta, v1)["id"] == sesion["id"]
    assert _sesion_de_venta(v12.ruta, v2)["id"] == sesion["id"]
    assert sesion["fecha_apertura"] == f"{T} 08:00:00"
    assert sesion["fecha_cierre"] == f"{T} 18:00:00"
    assert sesion["contado_centavos"] == 1300
    assert sesion["diferencia_centavos"] == 0
    assert sesion["usuario_apertura_id"] == usuario and sesion["usuario_cierre_id"] == usuario
    assert _legado(v12.ruta) == []


def test_cierre_anterior_a_la_011_conserva_diferencia_null(v12):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 1000)
    v12.movimiento(f"{T} 18:00:00", "CIERRE", 900, diferencia=None)
    v12.migrar()

    (sesion,) = _sesiones(v12.ruta)
    assert sesion["contado_centavos"] == 900
    assert sesion["diferencia_centavos"] is None


def test_venta_antes_de_la_primera_apertura_va_a_legado(v12):
    venta = v12.venta(f"{T} 07:00:00")
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    v12.movimiento(f"{T} 18:00:00", "CIERRE", 100, diferencia=0)
    v12.migrar()

    sesion = _sesion_de_venta(v12.ruta, venta)
    assert sesion["origen"] == "LEGADO" and sesion["estado"] == "CERRADA"
    assert len(_legado(v12.ruta)) == 1


def test_venta_entre_cierre_y_apertura_va_a_legado(v12):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    v12.movimiento(f"{T} 12:00:00", "CIERRE", 100, diferencia=0)
    venta = v12.venta(f"{T} 13:00:00")
    v12.movimiento(f"{T} 14:00:00", "APERTURA", 100)
    v12.migrar()

    assert _sesion_de_venta(v12.ruta, venta)["origen"] == "LEGADO"


def test_venta_despues_de_una_apertura_sin_cierre_pertenece_a_esa_sesion(v12):
    apertura = v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    venta = v12.venta(f"{T} 09:00:00")
    v12.migrar()

    sesion = _sesion_de_venta(v12.ruta, venta)
    assert sesion["id"] == _sesion_de_movimiento(v12.ruta, apertura)["id"]
    assert sesion["origen"] == "RECONSTRUIDA"


def test_apertura_sin_cierre_queda_cerrada_sin_datos_de_cierre_y_no_hay_caja_abierta(v12):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 2500)
    v12.venta(f"{T} 09:00:00")
    v12.migrar()

    (sesion,) = _sesiones(v12.ruta)
    assert (sesion["estado"], sesion["origen"]) == ("CERRADA", "RECONSTRUIDA")
    assert sesion["fecha_cierre"] is None
    assert sesion["contado_centavos"] is None and sesion["diferencia_centavos"] is None
    assert servicio_caja.consultar_estado() is False
    with pytest.raises(CajaCerradaError):
        servicio_ventas.registrar_venta([ItemVenta(1, 1)], "EFECTIVO")


def test_dos_aperturas_consecutivas_la_primera_queda_sin_cierre(v12):
    a1 = v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    ingreso = v12.movimiento(f"{T} 08:30:00", "INGRESO", 50)
    venta1 = v12.venta(f"{T} 09:00:00")
    a2 = v12.movimiento(f"{T} 10:00:00", "APERTURA", 200)
    venta2 = v12.venta(f"{T} 11:00:00")
    v12.migrar()

    s1, s2 = _sesion_de_movimiento(v12.ruta, a1), _sesion_de_movimiento(v12.ruta, a2)
    assert s1["id"] != s2["id"]
    assert s1["fecha_cierre"] is None and s1["contado_centavos"] is None  # cierre implícito, no registrado
    assert _sesion_de_movimiento(v12.ruta, ingreso)["id"] == s1["id"]
    assert _sesion_de_venta(v12.ruta, venta1)["id"] == s1["id"]
    assert _sesion_de_venta(v12.ruta, venta2)["id"] == s2["id"]
    assert _legado(v12.ruta) == []


def test_cierre_sin_apertura_y_movimientos_fuera_de_sesion_van_a_legado(v12):
    huerfano_antes = v12.movimiento(f"{T} 07:00:00", "INGRESO", 10)
    cierre_huerfano = v12.movimiento(f"{T} 07:30:00", "CIERRE", 10, diferencia=0)
    apertura = v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    cierre = v12.movimiento(f"{T} 12:00:00", "CIERRE", 100, diferencia=0)
    entre = v12.movimiento(f"{T} 13:00:00", "EGRESO", 5)
    cierre_doble = v12.movimiento(f"{T} 13:30:00", "CIERRE", 0, diferencia=0)
    v12.migrar()

    for movimiento_id in (huerfano_antes, cierre_huerfano, entre, cierre_doble):
        assert _sesion_de_movimiento(v12.ruta, movimiento_id)["origen"] == "LEGADO"
    assert _sesion_de_movimiento(v12.ruta, apertura)["origen"] == "RECONSTRUIDA"
    assert _sesion_de_movimiento(v12.ruta, cierre)["origen"] == "RECONSTRUIDA"
    assert len([s for s in _sesiones(v12.ruta) if s["origen"] == "RECONSTRUIDA"]) == 1


def test_sesion_legado_es_un_contenedor_cerrado_con_campos_en_null(v12):
    v12.venta(f"{T} 07:00:00")
    v12.movimiento(f"{T} 07:30:00", "CIERRE", 10, diferencia=0)
    v12.migrar()

    (legado,) = _legado(v12.ruta)
    assert legado["estado"] == "CERRADA"
    for campo in (
        "fondo_centavos", "contado_centavos", "diferencia_centavos", "fecha_cierre",
        "usuario_apertura_id", "usuario_cierre_id",
    ):
        assert legado[campo] is None, campo
    assert legado["fecha_apertura"] == f"{T} 07:00:00"


def test_sin_huerfanos_no_se_crea_legado(v12):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    v12.venta(f"{T} 09:00:00")
    v12.movimiento(f"{T} 12:00:00", "CIERRE", 100, diferencia=0)
    v12.migrar()

    assert _legado(v12.ruta) == []


# --- Frontera y timestamps ---------------------------------------------------------


def test_venta_en_el_segundo_del_cierre_de_a_y_la_apertura_de_b_pertenece_a_a(v12):
    a = v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    v12.movimiento(f"{T} 12:00:00", "CIERRE", 100, diferencia=0)
    b = v12.movimiento(f"{T} 12:00:00", "APERTURA", 100)
    frontera = v12.venta(f"{T} 12:00:00")
    v12.migrar()

    assert _sesion_de_venta(v12.ruta, frontera)["id"] == _sesion_de_movimiento(v12.ruta, a)["id"]
    assert _sesion_de_movimiento(v12.ruta, a)["id"] != _sesion_de_movimiento(v12.ruta, b)["id"]


def test_venta_en_el_segundo_de_la_apertura_de_b_sin_otra_candidata_pertenece_a_b(v12):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    v12.movimiento(f"{T} 09:00:00", "CIERRE", 100, diferencia=0)
    b = v12.movimiento(f"{T} 12:00:00", "APERTURA", 100)
    en_apertura = v12.venta(f"{T} 12:00:00")
    en_cierre = v12.venta(f"{T} 09:00:00")
    v12.migrar()

    assert _sesion_de_venta(v12.ruta, en_apertura)["id"] == _sesion_de_movimiento(v12.ruta, b)["id"]
    assert _sesion_de_venta(v12.ruta, en_cierre)["origen"] == "RECONSTRUIDA"  # el límite de cierre es inclusivo
    assert _sesion_de_venta(v12.ruta, en_cierre)["id"] != _sesion_de_movimiento(v12.ruta, b)["id"]


def test_venta_anulada_se_asigna_por_la_fecha_de_la_venta_no_por_la_de_anulacion(v12):
    a = v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    v12.movimiento(f"{T} 12:00:00", "CIERRE", 100, diferencia=0)
    b = v12.movimiento(f"{T} 13:00:00", "APERTURA", 100)
    anulada = v12.venta(f"{T} 09:00:00", estado="ANULADA", fecha_anulacion=f"{T} 13:30:00")
    v12.migrar()

    assert _sesion_de_venta(v12.ruta, anulada)["id"] == _sesion_de_movimiento(v12.ruta, a)["id"]
    assert _sesion_de_venta(v12.ruta, anulada)["id"] != _sesion_de_movimiento(v12.ruta, b)["id"]
    assert _consultar(v12.ruta, "SELECT estado FROM ventas WHERE id = ?", (anulada,))[0]["estado"] == "ANULADA"


def test_reloj_retrocedido_no_pierde_ventas_ni_importes_y_es_determinista(v12, tmp_path, monkeypatch):
    """La asignación de ventas depende solo del timestamp de la venta: con el
    reloj retrocedido (ventas fuera de orden respecto de su id) sigue siendo
    determinista y no pierde nada."""
    v12.movimiento(f"{T} 10:00:00", "APERTURA", 100)
    v_tarde = v12.venta(f"{T} 10:05:00", 500)
    v_temprano = v12.venta(f"{T} 10:03:00", 300)
    v12.movimiento(f"{T} 10:06:00", "CIERRE", 100, diferencia=0)
    v12.con.commit()
    copia = tmp_path / "copia.db"
    shutil.copy(v12.ruta, copia)

    v12.migrar()
    (sesion,) = _sesiones(v12.ruta)
    assert _sesion_de_venta(v12.ruta, v_tarde)["id"] == sesion["id"]
    assert _sesion_de_venta(v12.ruta, v_temprano)["id"] == sesion["id"]
    assert _consultar(v12.ruta, "SELECT SUM(total_centavos) AS s, COUNT(*) AS n FROM ventas")[0]["s"] == 800
    primera = _instantanea(v12.ruta)

    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", copia)
    modulo_conexion.inicializar_base_datos()
    assert _instantanea(copia) == primera


def test_reloj_retrocedido_que_invierte_una_sesion_manda_sus_ventas_a_legado_sin_perder_nada(v12):
    apertura = v12.movimiento(f"{T} 10:10:00", "APERTURA", 100)
    cierre = v12.movimiento(f"{T} 10:06:00", "CIERRE", 100, diferencia=0)  # fecha anterior a su apertura
    ventas = [v12.venta(f"{T} 10:08:00"), v12.venta(f"{T} 10:10:00"), v12.venta(f"{T} 10:12:00")]
    v12.migrar()

    assert _sesion_de_movimiento(v12.ruta, apertura)["id"] == _sesion_de_movimiento(v12.ruta, cierre)["id"]
    for venta in ventas:
        assert _sesion_de_venta(v12.ruta, venta)["origen"] == "LEGADO"
    assert _consultar(v12.ruta, "SELECT COUNT(*) AS n FROM ventas")[0]["n"] == 3


def test_venta_con_fecha_de_formato_invalido_va_a_legado(v12):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    invalida = v12.venta("no-es-una-fecha")
    normal = v12.venta(f"{T} 09:00:00")
    v12.migrar()

    assert _sesion_de_venta(v12.ruta, invalida)["origen"] == "LEGADO"
    assert _sesion_de_venta(v12.ruta, normal)["origen"] == "RECONSTRUIDA"


# --- Preservación de datos ---------------------------------------------------------


def test_usuario_null_se_conserva_y_no_se_inventa_ningun_usuario(v12):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    v12.venta(f"{T} 09:00:00")
    v12.movimiento(f"{T} 12:00:00", "CIERRE", 100, diferencia=0)
    usuarios_antes = _consultar(v12.ruta, "SELECT COUNT(*) AS n FROM usuarios")[0]["n"]
    v12.migrar()

    (sesion,) = _sesiones(v12.ruta)
    assert sesion["usuario_apertura_id"] is None and sesion["usuario_cierre_id"] is None
    assert _consultar(v12.ruta, "SELECT COUNT(*) AS n FROM usuarios")[0]["n"] == usuarios_antes
    assert _consultar(v12.ruta, "SELECT usuario_id FROM ventas")[0]["usuario_id"] is None


def test_detalles_de_venta_se_conservan_intactos(v12):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    v12.venta(f"{T} 09:00:00", 400, lineas=2)
    v12.venta(f"{T} 10:00:00", 600, lineas=3)
    antes = _instantanea(v12.ruta)
    v12.migrar()

    despues = _instantanea(v12.ruta)
    assert despues["detalle"] == antes["detalle"]
    assert len(despues["detalle"]) == 5
    huerfanos = _consultar(
        v12.ruta,
        "SELECT COUNT(*) AS n FROM detalle_venta d LEFT JOIN ventas v ON v.id = d.venta_id WHERE v.id IS NULL",
    )[0]["n"]
    assert huerfanos == 0


def test_ventas_y_movimientos_se_conservan_salvo_la_columna_nueva(v12):
    usuario = v12.usuario()
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100, usuario_id=usuario)
    v12.venta(f"{T} 09:00:00", 350, usuario_id=usuario)
    v12.venta(f"{T} 09:10:00", 150, estado="ANULADA", fecha_anulacion=f"{T} 09:20:00")
    v12.movimiento(f"{T} 12:00:00", "CIERRE", 550, diferencia=100, usuario_id=usuario)
    antes = _instantanea(v12.ruta)
    v12.migrar()
    despues = _instantanea(v12.ruta)

    # El runner aplica también la 020, que agrega `ventas.cliente_id` y
    # `caja_movimientos.origen` al final de cada fila: la columna de la 019
    # (`sesion_caja_id`) queda anteúltima y el resto es idéntico a la V1.2.
    assert [fila[:-2] for fila in despues["ventas"]] == antes["ventas"]
    assert [fila[:-2] for fila in despues["movimientos"]] == antes["movimientos"]
    assert all(fila[-2] is not None for fila in despues["ventas"] + despues["movimientos"])
    assert sum(fila[2] for fila in despues["ventas"]) == 500  # SUM(total) antes = después


def test_sqlite_sequence_se_preserva(v12):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    v12.venta(f"{T} 09:00:00")
    v12.venta(f"{T} 09:10:00")
    ultima = v12.venta(f"{T} 09:20:00")
    v12.con.execute("DELETE FROM detalle_venta WHERE venta_id = ?", (ultima,))
    v12.con.execute("DELETE FROM ventas WHERE id = ?", (ultima,))  # seq queda por encima del máximo id
    borrado = v12.movimiento(f"{T} 09:30:00", "INGRESO", 5)
    v12.con.execute("DELETE FROM caja_movimientos WHERE id = ?", (borrado,))
    v12.con.commit()
    antes = _instantanea(v12.ruta)["secuencias"]
    v12.migrar()

    despues = dict(_instantanea(v12.ruta)["secuencias"])
    for tabla, seq in antes:
        assert despues[tabla] == seq, tabla
    assert dict(antes)["ventas"] == 3


def test_multiples_sesiones_historicas_asignan_cada_fila_a_su_sesion(v12):
    esperado = []
    for indice, hora in enumerate(("08", "10", "12")):
        apertura = v12.movimiento(f"{T} {hora}:00:00", "APERTURA", 100 * (indice + 1))
        v1 = v12.venta(f"{T} {hora}:10:00", 100)
        v2 = v12.venta(f"{T} {hora}:20:00", 200)
        cierre = v12.movimiento(f"{T} {hora}:50:00", "CIERRE", 500, diferencia=indice)
        esperado.append((apertura, cierre, v1, v2))
    v12.migrar()

    ids_de_sesion = set()
    for apertura, cierre, v1, v2 in esperado:
        sesion = _sesion_de_movimiento(v12.ruta, apertura)
        ids_de_sesion.add(sesion["id"])
        assert _sesion_de_movimiento(v12.ruta, cierre)["id"] == sesion["id"]
        assert _sesion_de_venta(v12.ruta, v1)["id"] == sesion["id"]
        assert _sesion_de_venta(v12.ruta, v2)["id"] == sesion["id"]
    assert len(ids_de_sesion) == 3
    assert _legado(v12.ruta) == []


def test_base_completamente_vacia_no_crea_sesiones_y_permite_la_primera_apertura(base_datos_temporal):
    assert _sesiones(base_datos_temporal) == []
    assert servicio_caja.consultar_estado() is False

    servicio_caja.abrir_caja(1000)

    (sesion,) = _sesiones(base_datos_temporal)
    assert (sesion["estado"], sesion["origen"], sesion["fondo_centavos"]) == ("ABIERTA", "NORMAL", 1000)


def test_backup_v12_restaurado_y_migrado_da_el_mismo_resultado(v12, tmp_path, monkeypatch):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    v12.venta(f"{T} 09:00:00")
    v12.movimiento(f"{T} 12:00:00", "CIERRE", 100, diferencia=0)
    v12.venta(f"{T} 13:00:00")  # huérfana -> LEGADO
    v12.con.commit()
    respaldo = tmp_path / "backup_v12.db"
    destino = sqlite3.connect(respaldo)
    v12.con.backup(destino)
    destino.close()

    v12.migrar()
    esperado = _instantanea(v12.ruta)

    restaurada = tmp_path / "restaurada.db"
    shutil.copy(respaldo, restaurada)
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", restaurada)
    modulo_conexion.inicializar_base_datos()
    assert _instantanea(restaurada) == esperado


# --- Atomicidad, rollback e idempotencia ------------------------------------------


def _con_019_modificada(tmp_path, monkeypatch, sufijo):
    directorio = tmp_path / "migraciones_rotas"
    shutil.copytree(modulo_conexion.DIRECTORIO_MIGRACIONES, directorio)
    (directorio / NOMBRE_019).write_text(RUTA_019.read_text(encoding="utf-8") + sufijo, encoding="utf-8")
    monkeypatch.setattr(modulo_conexion, "DIRECTORIO_MIGRACIONES", directorio)


def test_rollback_intencional_deja_la_base_identica_y_la_migracion_sin_registrar(v12, tmp_path, monkeypatch):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    v12.venta(f"{T} 09:00:00")
    v12.venta(f"{T} 07:00:00")
    v12.con.commit()
    antes = _instantanea(v12.ruta)
    _con_019_modificada(tmp_path, monkeypatch, "\nSELECT * FROM tabla_que_no_existe;\n")
    v12.con.close()

    with pytest.raises(ErrorBaseDatos):
        modulo_conexion.inicializar_base_datos()

    assert _instantanea(v12.ruta) == antes  # ni tablas, ni columnas, ni triggers, ni filas
    assert NOMBRE_019 not in _instantanea(v12.ruta)["migraciones"]


def test_violacion_de_fk_preexistente_aborta_la_migracion_sin_dejar_nada(v12):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    venta = v12.venta(f"{T} 09:00:00")
    v12.con.execute("PRAGMA foreign_keys = OFF")
    v12.con.execute("UPDATE detalle_venta SET producto_id = 999 WHERE venta_id = ?", (venta,))
    v12.con.commit()
    antes = _instantanea(v12.ruta)
    v12.con.close()

    with pytest.raises(ErrorBaseDatos, match="foreign_key_check"):
        modulo_conexion.inicializar_base_datos()

    assert _instantanea(v12.ruta) == antes


def test_violacion_de_fk_provocada_al_final_de_la_migracion_revierte_todo(v12, tmp_path, monkeypatch):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    v12.venta(f"{T} 09:00:00")
    v12.con.commit()
    antes = _instantanea(v12.ruta)
    v12.con.close()
    _con_019_modificada(tmp_path, monkeypatch, "\nUPDATE ventas SET usuario_id = 9999;\n")

    with pytest.raises(ErrorBaseDatos):
        modulo_conexion.inicializar_base_datos()

    assert _instantanea(v12.ruta) == antes


def test_la_migracion_es_idempotente_por_registro(v12):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    v12.venta(f"{T} 09:00:00")
    v12.migrar()
    primera = _instantanea(v12.ruta)

    modulo_conexion.inicializar_base_datos()
    modulo_conexion.inicializar_base_datos()

    assert _instantanea(v12.ruta) == primera
    assert primera["migraciones"].count(NOMBRE_019) == 1


def test_integridad_y_claves_foraneas_correctas_tras_migrar(v12):
    v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    v12.venta(f"{T} 09:00:00", lineas=2)
    v12.venta(f"{T} 07:00:00")
    v12.migrar()

    con = sqlite3.connect(v12.ruta)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT name FROM sqlite_temp_master").fetchall() == []  # sin restos temporales
    finally:
        con.close()


# --- Estructura del script ---------------------------------------------------------


def _script_sin_comentarios():
    texto = RUTA_019.read_text(encoding="utf-8")
    return "\n".join(re.sub(r"--.*$", "", linea) for linea in texto.splitlines())


def test_el_script_es_aditivo_sobre_las_tablas_comerciales():
    sql = _script_sin_comentarios().upper()
    tablas = r"(VENTAS|DETALLE_VENTA|CAJA_MOVIMIENTOS)"
    assert not re.search(rf"DROP\s+TABLE\s+(IF\s+EXISTS\s+)?{tablas}\b", sql)
    assert not re.search(rf"DELETE\s+FROM\s+{tablas}\b", sql)
    assert not re.search(rf"INSERT\s+(OR\s+\w+\s+)?INTO\s+{tablas}\b", sql)
    assert not re.search(rf"ALTER\s+TABLE\s+{tablas}\s+(RENAME|DROP)", sql)
    assert "PRAGMA FOREIGN_KEYS" not in sql
    assert "COMMIT" not in sql and "BEGIN" not in re.sub(r"CREATE\s+(TEMP\s+)?TRIGGER.*?END;", "", sql, flags=re.S)


def test_los_triggers_permanentes_se_crean_despues_de_todo_el_backfill():
    """Durante el backfill no existe ningún trigger sobre `sesion_caja_id`: la
    columna solo pasa de NULL a una sesión histórica antes de que existan."""
    sql = _script_sin_comentarios()
    ultimo_backfill = max(
        sql.rfind("UPDATE ventas"), sql.rfind("UPDATE caja_movimientos"), sql.rfind("INSERT INTO sesiones_caja")
    )
    primer_trigger = re.search(r"CREATE\s+TRIGGER\s+trg_", sql).start()
    assert 0 < ultimo_backfill < primer_trigger


# --- Triggers tras la migración ----------------------------------------------------


@pytest.fixture
def migrada(v12):
    """Base migrada con: sesión histórica 1 (venta + ingreso), sesión LEGADO
    (venta previa a toda apertura) y una sesión NORMAL abierta."""
    legado_venta = v12.venta(f"{T} 07:00:00", 10)
    apertura = v12.movimiento(f"{T} 08:00:00", "APERTURA", 100)
    venta = v12.venta(f"{T} 09:00:00", 200)
    ingreso = v12.movimiento(f"{T} 09:30:00", "INGRESO", 50)
    v12.movimiento(f"{T} 12:00:00", "CIERRE", 350, diferencia=0)
    v12.migrar()
    servicio_caja.abrir_caja(1000)
    (abierta,) = _consultar(v12.ruta, "SELECT id FROM sesiones_caja WHERE estado = 'ABIERTA'")
    reconstruida = _sesion_de_venta(v12.ruta, venta)
    legado = _sesion_de_venta(v12.ruta, legado_venta)
    return {
        "ruta": v12.ruta, "venta": venta, "ingreso": ingreso, "apertura": apertura,
        "abierta": abierta["id"], "reconstruida": reconstruida["id"], "legado": legado["id"],
        "venta_legado": legado_venta,
    }


def test_backfill_deja_venta_y_movimiento_en_sesion_cerrada_reconstruida(migrada):
    ruta = migrada["ruta"]
    sesion_venta = _sesion_de_venta(ruta, migrada["venta"])
    sesion_mov = _sesion_de_movimiento(ruta, migrada["ingreso"])
    assert (sesion_venta["estado"], sesion_venta["origen"]) == ("CERRADA", "RECONSTRUIDA")
    assert sesion_mov["id"] == sesion_venta["id"]


def test_backfill_sobre_legado_deja_la_venta_en_sesion_cerrada_legado(migrada):
    sesion = _sesion_de_venta(migrada["ruta"], migrada["venta_legado"])
    assert (sesion["estado"], sesion["origen"]) == ("CERRADA", "LEGADO")


@pytest.mark.parametrize("tabla, clave", [("ventas", "venta"), ("caja_movimientos", "ingreso")])
@pytest.mark.parametrize("destino", ["abierta", "legado", "otra_reconstruida", None])
def test_sesion_asignada_es_inmutable(migrada, tabla, clave, destino):
    """No se permite A -> B (abierta, LEGADO u otra reconstruida) ni A -> NULL."""
    if destino == "otra_reconstruida":
        destino_id = migrada["abierta"] + 100  # no hace falta que exista: el trigger corta antes que la FK
    else:
        destino_id = migrada[destino] if destino else None

    with pytest.raises(ErrorBaseDatos, match="no puede modificarse"):
        with obtener_conexion() as conexion:
            conexion.execute(f"UPDATE {tabla} SET sesion_caja_id = ? WHERE id = ?", (destino_id, migrada[clave]))

    assert _consultar(migrada["ruta"], f"SELECT sesion_caja_id FROM {tabla} WHERE id = ?", (migrada[clave],))[0][0] == (
        migrada["reconstruida"]
    )


@pytest.mark.parametrize("tabla, clave", [("ventas", "venta"), ("caja_movimientos", "ingreso")])
def test_reasignar_a_la_misma_sesion_no_cambia_nada_y_no_se_rechaza(migrada, tabla, clave):
    with obtener_conexion() as conexion:
        conexion.execute(
            f"UPDATE {tabla} SET sesion_caja_id = ? WHERE id = ?", (migrada["reconstruida"], migrada[clave])
        )


def _insertar_venta(sesion_caja_id):
    with obtener_conexion() as conexion:
        conexion.execute(
            "INSERT INTO ventas (total_centavos, tipo_pago, sesion_caja_id) VALUES (100, 'EFECTIVO', ?)",
            (sesion_caja_id,),
        )


def _insertar_movimiento(sesion_caja_id):
    with obtener_conexion() as conexion:
        conexion.execute(
            "INSERT INTO caja_movimientos (tipo, monto_centavos, descripcion, sesion_caja_id)"
            " VALUES ('INGRESO', 10, 'x', ?)",
            (sesion_caja_id,),
        )


@pytest.mark.parametrize("insertar", [_insertar_venta, _insertar_movimiento])
@pytest.mark.parametrize("destino", ["reconstruida", "legado", None])
def test_no_se_puede_operar_sobre_sesion_cerrada_legado_ni_sin_sesion(migrada, insertar, destino):
    with pytest.raises(ErrorBaseDatos, match="sesión de caja ABIERTA"):
        insertar(migrada[destino] if destino else None)


@pytest.mark.parametrize("insertar", [_insertar_venta, _insertar_movimiento])
def test_si_se_puede_operar_sobre_la_sesion_abierta(migrada, insertar):
    insertar(migrada["abierta"])


def test_no_puede_haber_dos_sesiones_abiertas(migrada):
    with pytest.raises(ErrorBaseDatos):
        with obtener_conexion() as conexion:
            conexion.execute(
                "INSERT INTO sesiones_caja (estado, origen, fecha_apertura, fondo_centavos)"
                " VALUES ('ABIERTA', 'NORMAL', '2026-04-01 00:00:00', 0)"
            )


@pytest.mark.parametrize("origen", ["RECONSTRUIDA", "LEGADO"])
def test_no_se_pueden_crear_sesiones_reconstruida_ni_legado_despues_de_migrar(migrada, origen):
    fondo = "NULL" if origen == "LEGADO" else "0"
    with pytest.raises(ErrorBaseDatos, match="NORMAL"):
        with obtener_conexion() as conexion:
            conexion.execute(
                "INSERT INTO sesiones_caja (estado, origen, fecha_apertura, fondo_centavos)"
                f" VALUES ('CERRADA', '{origen}', '2026-04-01 00:00:00', {fondo})"
            )


@pytest.mark.parametrize(
    "asignacion",
    ["estado = 'ABIERTA'", "origen = 'NORMAL'", "fondo_centavos = 1", "contado_centavos = 1"],
)
@pytest.mark.parametrize("sesion", ["reconstruida", "legado"])
def test_una_sesion_cerrada_no_se_reabre_ni_se_modifica(migrada, sesion, asignacion):
    with pytest.raises(ErrorBaseDatos):
        with obtener_conexion() as conexion:
            conexion.execute(f"UPDATE sesiones_caja SET {asignacion} WHERE id = ?", (migrada[sesion],))


def test_el_origen_y_los_datos_de_apertura_de_una_sesion_abierta_no_cambian(migrada):
    for asignacion in ("origen = 'RECONSTRUIDA'", "fondo_centavos = 5", "fecha_apertura = '2000-01-01 00:00:00'"):
        with pytest.raises(ErrorBaseDatos):
            with obtener_conexion() as conexion:
                conexion.execute(f"UPDATE sesiones_caja SET {asignacion} WHERE id = ?", (migrada["abierta"],))


def test_la_sesion_legado_nunca_admite_operaciones_nuevas(migrada):
    for insertar in (_insertar_venta, _insertar_movimiento):
        with pytest.raises(ErrorBaseDatos):
            insertar(migrada["legado"])


# --- Comportamiento de V1.3 sobre una base migrada ---------------------------------


def test_anular_una_venta_de_una_sesion_cerrada_se_rechaza(migrada):
    usuario_id = _consultar(migrada["ruta"], "SELECT COUNT(*) AS n FROM usuarios")[0]["n"]
    assert usuario_id == 0  # el escenario no tiene usuarios: se crea uno con FK válida
    with obtener_conexion() as conexion:
        usuario_id = conexion.execute(
            "INSERT INTO usuarios (nombre_usuario, nombre_completo, password_hash, rol)"
            " VALUES ('duenio', 'D', 'h', 'OWNER')"
        ).lastrowid

    with pytest.raises(VentaDeCajaCerradaError):
        servicio_ventas.anular_venta(migrada["venta"], "ERROR_CARGA", None, usuario_id)
    with pytest.raises(VentaDeCajaCerradaError):
        servicio_ventas.anular_venta(migrada["venta_legado"], "ERROR_CARGA", None, usuario_id)

    estados = _consultar(migrada["ruta"], "SELECT estado FROM ventas WHERE id IN (?, ?)", (migrada["venta"], migrada["venta_legado"]))
    assert {fila["estado"] for fila in estados} == {"ACTIVA"}


def test_primera_apertura_v13_crea_sesion_normal_y_las_ventas_nuevas_van_a_ella(migrada):
    venta = servicio_ventas.registrar_venta([ItemVenta(1, 1)], "EFECTIVO")

    sesion = _sesion_de_venta(migrada["ruta"], venta.id)
    assert sesion["id"] == migrada["abierta"]
    assert (sesion["estado"], sesion["origen"]) == ("ABIERTA", "NORMAL")
