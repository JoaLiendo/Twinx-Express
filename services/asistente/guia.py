"""Guía de uso del asistente: respuestas fijas a "¿cómo hago...?", extraídas del manual (`docs/MANUAL.md`).

Son texto redactado por personas, no generado por un modelo, y así se presentan. Cada tema enlaza a la
sección de la Ayuda integrada que lo desarrolla. Un tema reservado al dueño se lo dice al cajero en vez de
explicarle una operación que no puede hacer.
"""

from dataclasses import dataclass

from domain.asistente import ROL_OWNER, Accion


@dataclass(frozen=True)
class TemaGuia:
    clave: str
    titulo: str
    pasos: tuple[str, ...]
    ancla: str
    solo_owner: bool = False
    pantalla: Accion | None = None


TEMAS: dict[str, TemaGuia] = {
    tema.clave: tema
    for tema in (
        TemaGuia(
            "compras",
            "Registrar una compra (ingreso de mercadería)",
            (
                "Entrá en Compras › Nueva y elegí el proveedor.",
                "Cargá cada línea con cantidad y costo unitario; si la mercadería vence, agregá la fecha "
                "(una fecha o varias con el formato cantidad@fecha).",
                "Elegí la condición: contado (indicando si el pago sale de la caja o de afuera) o crédito "
                "(con vencimiento opcional).",
                "Confirmá: el stock sube, el costo vigente del producto pasa a ser el de la compra y queda en el "
                "historial de precios. El precio de venta no cambia solo: se ajusta en Precios.",
            ),
            "compras",
            solo_owner=True,
            pantalla=Accion("Nueva compra", "/compras/nueva"),
        ),
        TemaGuia(
            "restauracion",
            "Restaurar un backup",
            (
                "Cerrá Twinx Express (la ventana negra).",
                "Hacé doble clic en Restaurar_backup.bat, dentro de la carpeta del programa, y arrastrá el ZIP "
                "del backup.",
                "Antes de reemplazar nada se guarda una copia de seguridad de los datos actuales. Un backup "
                "dañado o de una versión más nueva no se restaura.",
                "Si hay comprobantes fiscales que el backup no conoce, el restore se bloquea: lo concilia el "
                "soporte técnico.",
            ),
            "restauracion",
            solo_owner=True,
            pantalla=Accion("Backup y restauración", "/backup"),
        ),
        TemaGuia(
            "backup",
            "Hacer un backup",
            (
                "El backup se hace solo al cerrar la caja, al abrir la aplicación si el último tiene más de 24 "
                "horas y antes de cada actualización.",
                "Para hacer uno ahora, entrá en Configuración › Backup y tocá Crear backup.",
                "Configurá un segundo destino (otro disco, pendrive o carpeta sincronizada): cada backup se copia "
                "y se verifica ahí.",
                "Los backups quedan en %LOCALAPPDATA%\\KioscoApp\\backups; el material fiscal no viaja en ellos.",
            ),
            "backup",
            solo_owner=True,
            pantalla=Accion("Backup", "/backup"),
        ),
        TemaGuia(
            "ventas",
            "Vender y cobrar",
            (
                "Con la caja abierta, en Ventas escaneá el código de barras o buscá por nombre (↑ ↓ y Enter "
                "agregan el producto resaltado).",
                "Ajustá cantidades en el carrito y elegí el medio de pago: efectivo (con monto recibido y "
                "vuelto), tarjeta, transferencia, otro o a cuenta para clientes con crédito.",
                "Tocá Cobrar. Después podés imprimir el ticket y, si corresponde, la factura; Nueva venta deja "
                "todo listo para el siguiente cliente.",
            ),
            "ventas",
            pantalla=Accion("Punto de venta", "/ventas"),
        ),
        TemaGuia(
            "caja",
            "Abrir y cerrar la caja",
            (
                "Abrí la caja en Caja con el fondo inicial. Sin caja abierta no se puede vender.",
                "Mientras está abierta podés registrar ingresos y egresos manuales con descripción; el arqueo "
                "muestra cuánto efectivo debería haber.",
                "Para cerrar, contá el efectivo real y confirmá: el sistema calcula la diferencia, la guarda y "
                "hace un backup automático.",
            ),
            "caja",
            pantalla=Accion("Caja", "/caja"),
        ),
        TemaGuia(
            "vencimientos",
            "Retirar mercadería vencida",
            (
                "El dashboard y Stock avisan qué productos están próximos a vencer o vencidos (los días de "
                "aviso se configuran en Configuración).",
                "Desde Vencimientos, en la fila del producto, se da de baja la mercadería vencida: queda "
                "registrada como pérdida con el costo de su partida.",
                "Vender una unidad vencida requiere autorización del dueño y queda en la auditoría.",
            ),
            "inventario",
            pantalla=Accion("Stock", "/productos"),
        ),
        TemaGuia(
            "reposicion",
            "Reponer stock",
            (
                "Reposición agrupa por proveedor principal lo que está por debajo del mínimo y explica cada "
                "cantidad sugerida (faltante, rotación, cobertura, vencimientos).",
                "Podés cambiar cantidades, pasar la lista al formulario de compra o imprimirla. Nunca registra "
                "una compra sola.",
            ),
            "inventario",
            solo_owner=True,
            pantalla=Accion("Reposición", "/reposicion"),
        ),
        TemaGuia(
            "inventario",
            "Hacer un inventario físico",
            (
                "En Inventario › Nuevo se cuenta a ciegas: no se ve el stock del sistema mientras se cuenta.",
                "El dueño revisa las diferencias y confirma: se generan ajustes con motivo «Ajuste de "
                "inventario». Un producto que se movió después de contarlo hay que volver a contarlo.",
            ),
            "inventario",
            pantalla=Accion("Inventario", "/inventario"),
        ),
        TemaGuia(
            "clientes",
            "Clientes, cuenta corriente y crédito",
            (
                "Ambos roles pueden crear clientes y cobrar; editar y desactivar es del dueño.",
                "Una venta a cuenta carga el saldo; los cobros en efectivo entran a la caja y se imputan a las "
                "ventas más viejas.",
                "Nadie tiene crédito hasta que el dueño define una política en Configuración. El estado se "
                "calcula por antigüedad de la deuda: normal, regular, demorado (requiere autorización) y "
                "crítica (crédito cerrado).",
            ),
            "clientes",
            pantalla=Accion("Clientes", "/clientes"),
        ),
        TemaGuia(
            "soporte",
            "Pedir ayuda al soporte",
            (
                "La Ayuda del menú describe cada pantalla y tiene una sección de solución de problemas.",
                "El dueño puede entrar en Configuración › Soporte para ver la versión, dónde están los datos y "
                "descargar un diagnóstico sin datos de negocio ni contraseñas para enviar al soporte técnico.",
            ),
            "problemas",
            pantalla=Accion("Ayuda", "/ayuda"),
        ),
    )
}


def responder_tema(tema: str, rol: str) -> tuple[str, tuple[Accion, ...], dict]:
    """Texto de la guía para `tema` según el rol, con los accesos relacionados y los datos para el modelo."""
    guia = TEMAS.get(tema)
    if guia is None:
        temas = ", ".join(sorted(TEMAS))
        return (f"No tengo una guía para eso. Temas disponibles: {temas}.", (Accion("Ayuda", "/ayuda"),), {"tema": None})
    ayuda = Accion(f"Ayuda · {guia.titulo}", f"/ayuda#{guia.ancla}")
    if guia.solo_owner and rol != ROL_OWNER:
        texto = f"{guia.titulo}: es una operación del dueño. Pedile que la haga o consultá la Ayuda."
        return texto, (ayuda,), {"tema": guia.clave, "solo_owner": True}
    pasos = "\n".join(f"{i}. {paso}" for i, paso in enumerate(guia.pasos, start=1))
    acciones = (ayuda,) + ((guia.pantalla,) if guia.pantalla else ())
    return f"{guia.titulo}:\n{pasos}", acciones, {"tema": guia.clave, "titulo": guia.titulo, "pasos": list(guia.pasos)}
