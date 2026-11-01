"""Caché de tablas de parámetros de ARCA (migración 042). Solo SQL parametrizado."""

import json
from dataclasses import asdict, dataclass

from db.conexion import obtener_conexion
from domain.fiscal.protocolo import ParametroARCA


@dataclass(frozen=True)
class TablaARCA:
    ambiente: str
    tabla: str
    filas: tuple[ParametroARCA, ...]
    fuente: str
    fecha_actualizacion: str


def guardar(ambiente: str, tabla: str, filas: list[ParametroARCA], fuente: str) -> None:
    with obtener_conexion() as conexion:
        conexion.execute(
            """
            INSERT INTO parametros_arca (ambiente, tabla, contenido_json, fuente) VALUES (?, ?, ?, ?)
            ON CONFLICT(ambiente, tabla) DO UPDATE SET contenido_json = excluded.contenido_json,
                fuente = excluded.fuente, fecha_actualizacion = datetime('now', 'localtime')
            """,
            (ambiente, tabla, json.dumps([asdict(fila) for fila in filas], ensure_ascii=False), fuente),
        )


def obtener(ambiente: str, tabla: str) -> TablaARCA | None:
    with obtener_conexion() as conexion:
        fila = conexion.execute(
            "SELECT * FROM parametros_arca WHERE ambiente = ? AND tabla = ?", (ambiente, tabla)
        ).fetchone()
    if fila is None:
        return None
    return TablaARCA(
        ambiente=fila["ambiente"],
        tabla=fila["tabla"],
        filas=tuple(ParametroARCA(**dato) for dato in json.loads(fila["contenido_json"])),
        fuente=fila["fuente"],
        fecha_actualizacion=fila["fecha_actualizacion"],
    )


def listar(ambiente: str) -> list[TablaARCA]:
    with obtener_conexion() as conexion:
        nombres = [f["tabla"] for f in conexion.execute("SELECT tabla FROM parametros_arca WHERE ambiente = ? ORDER BY tabla", (ambiente,))]
    return [tabla for nombre in nombres if (tabla := obtener(ambiente, nombre)) is not None]
