"""Repositorio de la contingencia CAEA (migración 044). Solo SQL parametrizado."""

import json
import sqlite3

from db.conexion import obtener_conexion
from domain.fiscal.caea import CAEA

_COLUMNAS = "id, ambiente, cuit, codigo, periodo, orden, vigente_desde, vigente_hasta, tope_informe, fecha_proceso, fecha_obtencion"


def _fila(fila: sqlite3.Row) -> CAEA:
    return CAEA(**{clave: fila[clave] for clave in fila.keys()})


def guardar_en_conexion(
    conexion: sqlite3.Connection,
    ambiente: str,
    cuit: str,
    codigo: str,
    periodo: int,
    orden: int,
    vigente_desde: str,
    vigente_hasta: str,
    tope_informe: str,
    fecha_proceso: str | None,
    observaciones: list[dict],
) -> CAEA:
    """Inserta el CAEA si no estaba (un reintento devuelve el ya registrado)."""
    conexion.execute(
        """
        INSERT INTO caea_autorizaciones (ambiente, cuit, codigo, periodo, orden, vigente_desde, vigente_hasta,
                                         tope_informe, fecha_proceso, observaciones_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ambiente, cuit, periodo, orden) DO NOTHING
        """,
        (ambiente, cuit, codigo, periodo, orden, vigente_desde, vigente_hasta, tope_informe, fecha_proceso,
         json.dumps(observaciones, ensure_ascii=False) if observaciones else None),
    )
    return _fila(conexion.execute(
        f"SELECT {_COLUMNAS} FROM caea_autorizaciones WHERE ambiente = ? AND cuit = ? AND periodo = ? AND orden = ?",
        (ambiente, cuit, periodo, orden),
    ).fetchone())


def del_periodo(ambiente: str, cuit: str, periodo: int, orden: int) -> CAEA | None:
    with obtener_conexion() as conexion:
        fila = conexion.execute(
            f"SELECT {_COLUMNAS} FROM caea_autorizaciones WHERE ambiente = ? AND cuit = ? AND periodo = ? AND orden = ?",
            (ambiente, cuit, periodo, orden),
        ).fetchone()
    return _fila(fila) if fila is not None else None


def vigente_en_conexion(conexion: sqlite3.Connection, ambiente: str, cuit: str, fecha_iso: str) -> CAEA | None:
    fila = conexion.execute(
        f"SELECT {_COLUMNAS} FROM caea_autorizaciones WHERE ambiente = ? AND cuit = ?"
        " AND ? BETWEEN vigente_desde AND vigente_hasta ORDER BY id DESC LIMIT 1",
        (ambiente, cuit, fecha_iso),
    ).fetchone()
    return _fila(fila) if fila is not None else None


def listar(ambiente: str, cuit: str, limite: int = 6) -> list[CAEA]:
    with obtener_conexion() as conexion:
        return [
            _fila(fila)
            for fila in conexion.execute(
                f"SELECT {_COLUMNAS} FROM caea_autorizaciones WHERE ambiente = ? AND cuit = ? ORDER BY periodo DESC, orden DESC LIMIT ?",
                (ambiente, cuit, limite),
            )
        ]


def vencidos_sin_cerrar(ambiente: str, cuit: str, hoy_iso: str) -> list[CAEA]:
    """CAEA cuyo período terminó (candidatos a informe sin movimiento)."""
    with obtener_conexion() as conexion:
        return [
            _fila(fila)
            for fila in conexion.execute(
                f"SELECT {_COLUMNAS} FROM caea_autorizaciones WHERE ambiente = ? AND cuit = ? AND vigente_hasta < ? ORDER BY id",
                (ambiente, cuit, hoy_iso),
            )
        ]


def sincronizar_numeracion_en_conexion(
    conexion: sqlite3.Connection, ambiente: str, cuit: str, punto_venta: int, tipo: int, ultimo_arca: int
) -> None:
    conexion.execute(
        """
        INSERT INTO caea_numeracion (ambiente, cuit, punto_venta, tipo_comprobante, ultimo_arca) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(ambiente, cuit, punto_venta, tipo_comprobante) DO UPDATE
            SET ultimo_arca = MAX(caea_numeracion.ultimo_arca, excluded.ultimo_arca), fecha = datetime('now', 'localtime')
        """,
        (ambiente, cuit, punto_venta, tipo, ultimo_arca),
    )


def proximo_numero_en_conexion(conexion: sqlite3.Connection, ambiente: str, cuit: str, punto_venta: int, tipo: int) -> int | None:
    """Mayor entre la base sincronizada con ARCA y lo ya emitido en el punto de venta CAEA, + 1. `None` si
    la secuencia nunca se sincronizó con ARCA (no se adivina un número)."""
    base = conexion.execute(
        "SELECT ultimo_arca FROM caea_numeracion WHERE ambiente = ? AND cuit = ? AND punto_venta = ? AND tipo_comprobante = ?",
        (ambiente, cuit, punto_venta, tipo),
    ).fetchone()
    if base is None:
        return None
    emitido = conexion.execute(
        "SELECT COALESCE(MAX(numero), 0) FROM comprobantes_fiscales WHERE ambiente = ? AND cuit_emisor = ?"
        " AND punto_venta = ? AND tipo_comprobante = ? AND numero IS NOT NULL AND estado NOT IN ('RECHAZADO', 'DESCARTADO')",
        (ambiente, cuit, punto_venta, tipo),
    ).fetchone()[0]
    return max(base["ultimo_arca"], emitido) + 1


def registrar_sin_movimiento_en_conexion(
    conexion: sqlite3.Connection, caea_id: int, punto_venta: int, resultado: str, fecha_proceso: str | None, detalle: dict | None
) -> None:
    conexion.execute(
        "INSERT INTO caea_sin_movimiento (caea_id, punto_venta, resultado, fecha_proceso, detalle_json) VALUES (?, ?, ?, ?, ?)",
        (caea_id, punto_venta, resultado, fecha_proceso, json.dumps(detalle, ensure_ascii=False) if detalle else None),
    )


def sin_movimiento_informado(caea_id: int, punto_venta: int) -> bool:
    with obtener_conexion() as conexion:
        return conexion.execute(
            "SELECT 1 FROM caea_sin_movimiento WHERE caea_id = ? AND punto_venta = ? AND resultado = 'A'", (caea_id, punto_venta)
        ).fetchone() is not None


def tiene_comprobantes(caea_id: int, punto_venta: int) -> bool:
    with obtener_conexion() as conexion:
        return conexion.execute(
            "SELECT 1 FROM comprobantes_fiscales WHERE caea_id = ? AND punto_venta = ?", (caea_id, punto_venta)
        ).fetchone() is not None
