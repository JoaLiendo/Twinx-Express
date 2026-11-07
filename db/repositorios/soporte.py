"""Contadores de solo lectura para la pantalla de soporte (V1.18): cuánto hay cargado, sin contenido."""

from db.conexion import obtener_conexion

_CONTADORES = {
    "productos_activos": "SELECT COUNT(*) FROM productos WHERE activo = 1",
    "ventas": "SELECT COUNT(*) FROM ventas",
    "compras": "SELECT COUNT(*) FROM compras",
    "clientes": "SELECT COUNT(*) FROM clientes",
    "proveedores": "SELECT COUNT(*) FROM proveedores",
    "usuarios": "SELECT COUNT(*) FROM usuarios",
    "auditoria": "SELECT COUNT(*) FROM auditoria",
}


def contadores() -> dict[str, int]:
    """Cantidad de filas de las entidades principales (nunca su contenido)."""
    with obtener_conexion() as conexion:
        return {clave: conexion.execute(consulta).fetchone()[0] for clave, consulta in _CONTADORES.items()}
