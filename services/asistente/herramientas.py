"""Herramientas de consulta del asistente (V1.18): solo lectura, argumentos validados, filas acotadas y
permisos explícitos por rol.

Cada herramienta reutiliza un servicio existente (reportes, analítica, stock, partidas, clientes,
proveedores): el asistente no tiene un segundo motor analítico ni ejecuta SQL. Devuelve el texto ya
redactado (para la ayuda integrada) y un diccionario de datos pequeño (para el modelo de IA, cuando hay).
Los nombres de productos, clientes y proveedores se sanean antes de salir: son contenido, no instrucciones.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from domain import asistente as reglas
from domain.analitica import ORDEN_ESTADOS_DEUDA
from domain.asistente import LIMITE_FILAS, Accion, ResultadoHerramienta, puede_usar, resolver_periodo, sanitizar_texto_externo
from domain.credito import ETIQUETAS_ESTADO
from domain.dinero import centavos_a_texto_localizado
from domain.partida import ESTADO_PROXIMO, ESTADO_VENCIDO
from domain.usuario import Usuario
from excepciones import DatosInvalidosError, PermisoDenegadoError
from services import (
    servicio_analitica,
    servicio_caja,
    servicio_clientes,
    servicio_configuracion,
    servicio_deuda_proveedores,
    servicio_partidas,
    servicio_proveedores,
    servicio_reportes,
    servicio_stock,
)
from services.asistente import guia

Ejecutor = Callable[[dict, Usuario, date], ResultadoHerramienta]


@dataclass(frozen=True)
class Herramienta:
    nombre: str
    descripcion: str
    parametros: dict
    ejecutar: Ejecutor

    @property
    def definicion(self) -> dict:
        """Formato de `tools` de la API de chat (estilo OpenAI/Ollama)."""
        return {
            "type": "function",
            "function": {"name": self.nombre, "description": self.descripcion, "parameters": self.parametros},
        }


def _dinero(centavos: int) -> str:
    return f"$ {centavos_a_texto_localizado(centavos)}"


def _nombre(texto: object) -> str:
    return sanitizar_texto_externo(texto)


_PARAMETRO_PERIODO = {
    "type": "object",
    "properties": {
        "periodo": {
            "type": "string",
            "enum": list(reglas.PERIODOS),
            "description": "Período a consultar: hoy, ayer, semana (desde el lunes), mes (desde el 1), mes_anterior o ultimos_30_dias.",
        }
    },
    "required": [],
}
_SIN_PARAMETROS = {"type": "object", "properties": {}, "required": []}


def _periodo(argumentos: dict, hoy: date, por_defecto: str) -> reglas.Periodo:
    return resolver_periodo(argumentos.get("periodo") or por_defecto, hoy)


def _ventas_del_periodo(argumentos: dict, usuario: Usuario, hoy: date) -> ResultadoHerramienta:
    periodo = _periodo(argumentos, hoy, reglas.PERIODO_HOY)
    reporte = servicio_reportes.generar_reporte_ventas(periodo.desde, periodo.hasta)
    acciones = (
        Accion("Reporte de ventas", f"/reportes?fecha_desde={periodo.desde}&fecha_hasta={periodo.hasta}"),
        Accion("Analítica de ventas", f"/analitica/ventas?fecha_desde={periodo.desde}&fecha_hasta={periodo.hasta}"),
    )
    medios = [
        {"medio": m.tipo_pago, "ventas": m.cantidad_ventas, "total_centavos": m.total_centavos}
        for m in reporte.ventas_por_medio_pago
    ]
    datos = {
        "periodo": {"desde": periodo.desde, "hasta": periodo.hasta, "etiqueta": periodo.etiqueta},
        "cantidad_ventas": reporte.cantidad_ventas,
        "total_centavos": reporte.total_facturado_centavos,
        "ticket_promedio_centavos": reporte.ticket_promedio_centavos,
        "por_medio_de_pago": medios,
        "ventas_anuladas": reporte.resumen_anulaciones.cantidad if hasattr(reporte.resumen_anulaciones, "cantidad") else None,
    }
    if reporte.cantidad_ventas == 0:
        return ResultadoHerramienta(datos, f"No hay ventas registradas {periodo.etiqueta}.", acciones)
    detalle_medios = " · ".join(f"{m['medio'].replace('_', ' ').capitalize()} {_dinero(m['total_centavos'])}" for m in medios)
    texto = (
        f"Ventas de {periodo.etiqueta}: {reporte.cantidad_ventas} venta{'s' if reporte.cantidad_ventas != 1 else ''} "
        f"por {_dinero(reporte.total_facturado_centavos)} (ticket promedio {_dinero(reporte.ticket_promedio_centavos)})."
    )
    if detalle_medios:
        texto += f"\nPor medio de pago: {detalle_medios}."
    return ResultadoHerramienta(datos, texto, acciones)


def _ventas_de_la_caja(argumentos: dict, usuario: Usuario, hoy: date) -> ResultadoHerramienta:
    arqueo = servicio_caja.calcular_arqueo_de_sesion()
    acciones = (Accion("Caja", "/caja"),)
    datos = {
        "caja_abierta": arqueo.sesion_abierta,
        "cantidad_ventas": arqueo.cantidad_ventas,
        "total_centavos": arqueo.total_vendido_centavos,
        "efectivo_ventas_centavos": arqueo.total_efectivo_ventas_centavos,
    }
    if not arqueo.sesion_abierta and arqueo.cantidad_ventas == 0:
        return ResultadoHerramienta(datos, "La caja está cerrada y no hay una sesión anterior con ventas.", acciones)
    encabezado = "En la caja abierta" if arqueo.sesion_abierta else "La caja está cerrada; en la última sesión"
    texto = (
        f"{encabezado} se registraron {arqueo.cantidad_ventas} venta{'s' if arqueo.cantidad_ventas != 1 else ''} "
        f"por {_dinero(arqueo.total_vendido_centavos)}, de las cuales {_dinero(arqueo.total_efectivo_ventas_centavos)} "
        "fueron en efectivo. El total corresponde a la sesión de caja, no al día calendario."
    )
    return ResultadoHerramienta(datos, texto, acciones)


def _productos_mas_vendidos(argumentos: dict, usuario: Usuario, hoy: date) -> ResultadoHerramienta:
    periodo = _periodo(argumentos, hoy, reglas.PERIODO_MES)
    reporte = servicio_reportes.generar_reporte_ventas(periodo.desde, periodo.hasta)
    filas = reporte.productos_mas_vendidos[:LIMITE_FILAS]
    acciones = (Accion("Reporte de ventas", f"/reportes?fecha_desde={periodo.desde}&fecha_hasta={periodo.hasta}"),)
    datos = {
        "periodo": {"desde": periodo.desde, "hasta": periodo.hasta, "etiqueta": periodo.etiqueta},
        "productos": [
            {"producto": _nombre(p.producto_nombre), "unidades": p.unidades_vendidas, "total_centavos": p.total_vendido_centavos}
            for p in filas
        ],
    }
    if not filas:
        return ResultadoHerramienta(datos, f"No hay ventas registradas {periodo.etiqueta}.", acciones)
    lineas = "\n".join(
        f"{i}. {_nombre(p.producto_nombre)}: {p.unidades_vendidas} u. ({_dinero(p.total_vendido_centavos)})"
        for i, p in enumerate(filas, start=1)
    )
    return ResultadoHerramienta(datos, f"Los más vendidos {periodo.etiqueta}, por unidades:\n{lineas}", acciones)


def _productos_por_vencer(argumentos: dict, usuario: Usuario, hoy: date) -> ResultadoHerramienta:
    dias = servicio_configuracion.obtener_dias_alerta_vencimiento()
    resumenes = servicio_partidas.resumenes_de_vencimiento(dias)
    vencidos = [(pid, r) for pid, r in resumenes.items() if r.cantidad_vencida > 0]
    proximos = [(pid, r) for pid, r in resumenes.items() if r.estado == ESTADO_PROXIMO and r.cantidad_vencida == 0]
    proximos.sort(key=lambda par: par[1].proximo_vencimiento or "")
    acciones = (Accion("Próximos a vencer", "/productos?vencimiento=proximo"), Accion("Vencidos", "/productos?vencimiento=vencido"))

    def fila(pid: int, resumen) -> dict:
        producto = servicio_stock.obtener_por_id_incluyendo_inactivos(pid)
        return {
            "producto": _nombre(producto.nombre if producto else f"#{pid}"),
            "proximo_vencimiento": resumen.proximo_vencimiento,
            "unidades_proximas": resumen.cantidad_proxima,
            "unidades_vencidas": resumen.cantidad_vencida,
        }

    filas_vencidos = [fila(pid, r) for pid, r in vencidos[:LIMITE_FILAS]]
    filas_proximos = [fila(pid, r) for pid, r in proximos[:LIMITE_FILAS]]
    datos = {
        "dias_de_aviso": dias,
        "vencidos": filas_vencidos,
        "total_productos_vencidos": len(vencidos),
        "proximos_a_vencer": filas_proximos,
        "total_productos_proximos": len(proximos),
    }
    if not vencidos and not proximos:
        return ResultadoHerramienta(datos, f"No hay mercadería vencida ni próxima a vencer en los próximos {dias} días.", acciones)
    partes = []
    if vencidos:
        lista = "; ".join(f"{f['producto']} ({f['unidades_vencidas']} u.)" for f in filas_vencidos)
        partes.append(f"Vencidos: {len(vencidos)} producto{'s' if len(vencidos) != 1 else ''}: {lista}.")
    if proximos:
        lista = "; ".join(f"{f['producto']} vence {f['proximo_vencimiento']} ({f['unidades_proximas']} u.)" for f in filas_proximos)
        partes.append(f"Próximos a vencer (≤ {dias} días): {len(proximos)}: {lista}.")
    return ResultadoHerramienta(datos, "\n".join(partes), acciones)


def _reposicion_sugerida(argumentos: dict, usuario: Usuario, hoy: date) -> ResultadoHerramienta:
    sugerencias = servicio_stock.listar_reposicion()
    ordenadas = sorted(sugerencias, key=lambda s: (s.cobertura_dias if s.cobertura_dias is not None else 10**6, -s.cantidad_sugerida))
    filas = ordenadas[:LIMITE_FILAS]
    acciones = (Accion("Reposición", "/reposicion"),)
    costo_total = sum(s.costo_estimado_centavos for s in sugerencias)
    datos = {
        "total_productos": len(sugerencias),
        "costo_estimado_total_centavos": costo_total,
        "productos": [
            {
                "producto": _nombre(s.nombre),
                "stock_actual": s.stock_actual,
                "stock_minimo": s.stock_minimo,
                "cantidad_sugerida": s.cantidad_sugerida,
                "cobertura_dias": s.cobertura_dias,
                "proveedor": _nombre(s.proveedor_nombre) if s.proveedor_nombre else None,
            }
            for s in filas
        ],
    }
    if not sugerencias:
        return ResultadoHerramienta(datos, "No hay productos por debajo de su stock mínimo.", acciones)
    lineas = "\n".join(
        f"- {f['producto']}: stock {f['stock_actual']} de mínimo {f['stock_minimo']}, comprar {f['cantidad_sugerida']}"
        + (f" ({f['proveedor']})" if f["proveedor"] else "")
        for f in datos["productos"]
    )
    texto = (
        f"Hay {len(sugerencias)} producto{'s' if len(sugerencias) != 1 else ''} por debajo del mínimo; "
        f"reponerlos cuesta aproximadamente {_dinero(costo_total)}."
        + (" Los más urgentes:" if len(sugerencias) > len(filas) else "")
        + f"\n{lineas}"
    )
    return ResultadoHerramienta(datos, texto, acciones)


def _margen_negativo(argumentos: dict, usuario: Usuario, hoy: date) -> ResultadoHerramienta:
    periodo = _periodo(argumentos, hoy, reglas.PERIODO_MES)
    analisis = servicio_analitica.analizar_margen(periodo.desde, periodo.hasta, "producto")
    negativos = sorted(analisis.con_margen_negativo, key=lambda g: g.margen_ref_centavos or 0)[:LIMITE_FILAS]
    acciones = (
        Accion("Analítica de margen", f"/analitica/margen?fecha_desde={periodo.desde}&fecha_hasta={periodo.hasta}&criterio=producto"),
    )
    datos = {
        "periodo": {"desde": periodo.desde, "hasta": periodo.hasta, "etiqueta": periodo.etiqueta},
        "productos": [
            {
                "producto": _nombre(g.etiqueta),
                "unidades": g.unidades,
                "facturado_centavos": g.facturado_centavos,
                "margen_centavos": g.margen_ref_centavos,
                "margen_porcentual": g.margen_ref_porcentual,
            }
            for g in negativos
        ],
        "total_con_margen_negativo": len(analisis.con_margen_negativo),
        "unidades_sin_costo_conocido": analisis.total.unidades_sin_costo_ref,
    }
    if not negativos:
        texto = f"Ningún producto vendido {periodo.etiqueta} tiene margen negativo sobre su costo de referencia."
    else:
        lineas = "\n".join(
            f"- {f['producto']}: margen {_dinero(f['margen_centavos'])} sobre {_dinero(f['facturado_centavos'])} facturados ({f['unidades']} u.)"
            for f in datos["productos"]
        )
        texto = f"Productos con margen negativo {periodo.etiqueta} ({len(analisis.con_margen_negativo)}):\n{lineas}"
    if analisis.total.unidades_sin_costo_ref:
        texto += f"\n{analisis.total.unidades_sin_costo_ref} unidades vendidas no tienen costo de referencia y no se evalúan."
    return ResultadoHerramienta(datos, texto, acciones)


def _proveedores_con_reclamos(argumentos: dict, usuario: Usuario, hoy: date) -> ResultadoHerramienta:
    resumenes = servicio_proveedores.listar_resumen(filtro="reclamos_abiertos")
    filas = sorted(resumenes, key=lambda r: -r.reclamos_abiertos)[:LIMITE_FILAS]
    acciones = (Accion("Proveedores con reclamos", "/proveedores?filtro=reclamos_abiertos"),)
    datos = {
        "total_proveedores": len(resumenes),
        "proveedores": [
            {"proveedor": _nombre(r.proveedor.nombre), "proveedor_id": r.proveedor.id, "reclamos_abiertos": r.reclamos_abiertos}
            for r in filas
        ],
    }
    if not resumenes:
        return ResultadoHerramienta(datos, "No hay proveedores con reclamos abiertos.", acciones)
    lineas = "\n".join(f"- {f['proveedor']}: {f['reclamos_abiertos']} reclamo{'s' if f['reclamos_abiertos'] != 1 else ''}" for f in datos["proveedores"])
    acciones += tuple(Accion(f"Ficha de {f['proveedor']}", f"/proveedores/{f['proveedor_id']}") for f in datos["proveedores"][:3])
    return ResultadoHerramienta(datos, f"Proveedores con reclamos abiertos ({len(resumenes)}):\n{lineas}", acciones)


def _deuda_de_clientes(argumentos: dict, usuario: Usuario, hoy: date) -> ResultadoHerramienta:
    total = servicio_clientes.obtener_deuda_total()
    acciones = (Accion("Clientes", "/clientes"),)
    datos: dict = {"total_centavos": total.total_centavos, "cantidad_clientes": total.cantidad_clientes}
    if total.cantidad_clientes == 0:
        return ResultadoHerramienta(datos, "Ningún cliente debe dinero en cuenta corriente.", acciones)
    texto = (
        f"Los clientes deben {_dinero(total.total_centavos)} en total "
        f"({total.cantidad_clientes} cliente{'s' if total.cantidad_clientes != 1 else ''})."
    )
    # El detalle por antigüedad y los nombres de los deudores son del dueño: el rol lo decide el backend.
    if usuario.rol == reglas.ROL_OWNER:
        clientes = servicio_analitica.analizar_deuda(hoy).clientes
        por_estado = {estado: clientes.total_por_estado(estado) for estado in ORDEN_ESTADOS_DEUDA}
        deudores = sorted(clientes.clientes, key=lambda c: -c.saldo_centavos)[:5]
        datos["por_antiguedad"] = {ETIQUETAS_ESTADO[e]: c for e, c in por_estado.items()}
        datos["mayores_deudores"] = [
            {"cliente": _nombre(c.cliente_nombre), "saldo_centavos": c.saldo_centavos, "estado": ETIQUETAS_ESTADO[c.estado]}
            for c in deudores
        ]
        texto += "\nPor antigüedad: " + " · ".join(f"{ETIQUETAS_ESTADO[e]} {_dinero(c)}" for e, c in por_estado.items() if c) + "."
        if deudores:
            texto += "\nMayores deudores: " + "; ".join(
                f"{_nombre(c.cliente_nombre)} {_dinero(c.saldo_centavos)} ({ETIQUETAS_ESTADO[c.estado].lower()})" for c in deudores
            ) + "."
        acciones += (Accion("Analítica de deuda", "/analitica/deuda"), Accion("Reporte de cuenta corriente", "/reportes/cuenta-corriente"))
    return ResultadoHerramienta(datos, texto, acciones)


def _deuda_a_proveedores(argumentos: dict, usuario: Usuario, hoy: date) -> ResultadoHerramienta:
    reporte = servicio_deuda_proveedores.generar_reporte_deuda_proveedores(hoy=hoy)
    acciones = (Accion("Deuda a proveedores", "/reportes/deuda-proveedores"),)
    consistentes = sorted((f for f in reporte.filas if f.consistente), key=lambda f: -(f.saldo_total_centavos or 0))[:5]
    datos = {
        "total_centavos": reporte.total_saldo_centavos,
        "vencida_centavos": reporte.total_vencida_centavos,
        "proxima_centavos": reporte.total_proxima_centavos,
        "vigente_centavos": reporte.total_vigente_centavos,
        "sin_vencimiento_centavos": reporte.total_sin_vencimiento_centavos,
        "proveedores_con_datos_inconsistentes": reporte.cantidad_inconsistentes,
        "mayores_saldos": [{"proveedor": _nombre(f.nombre), "saldo_centavos": f.saldo_total_centavos} for f in consistentes],
    }
    if not reporte.filas:
        return ResultadoHerramienta(datos, "No hay deuda pendiente con proveedores.", acciones)
    texto = (
        f"Debés {_dinero(reporte.total_saldo_centavos)} a proveedores: vencido {_dinero(reporte.total_vencida_centavos)}, "
        f"próximo a vencer {_dinero(reporte.total_proxima_centavos)}, vigente {_dinero(reporte.total_vigente_centavos)}, "
        f"sin vencimiento {_dinero(reporte.total_sin_vencimiento_centavos)}."
    )
    if consistentes:
        texto += "\nMayores saldos: " + "; ".join(f"{m['proveedor']} {_dinero(m['saldo_centavos'])}" for m in datos["mayores_saldos"]) + "."
    if reporte.cantidad_inconsistentes:
        texto += f"\n{reporte.cantidad_inconsistentes} proveedor(es) con datos inconsistentes no entran en estos totales."
    return ResultadoHerramienta(datos, texto, acciones)


def _guia_de_uso(argumentos: dict, usuario: Usuario, hoy: date) -> ResultadoHerramienta:
    texto, acciones, datos = guia.responder_tema(str(argumentos.get("tema") or ""), usuario.rol)
    return ResultadoHerramienta(datos, texto, acciones)


HERRAMIENTAS: dict[str, Herramienta] = {
    h.nombre: h
    for h in (
        Herramienta(
            reglas.VENTAS_DEL_PERIODO,
            "Total vendido, cantidad de ventas, ticket promedio y desglose por medio de pago en un período.",
            _PARAMETRO_PERIODO,
            _ventas_del_periodo,
        ),
        Herramienta(
            reglas.VENTAS_DE_LA_CAJA,
            "Ventas registradas en la sesión de caja abierta (o la última cerrada): cantidad, total y efectivo.",
            _SIN_PARAMETROS,
            _ventas_de_la_caja,
        ),
        Herramienta(
            reglas.PRODUCTOS_MAS_VENDIDOS,
            "Ranking de los productos más vendidos por unidades en un período, con lo facturado por cada uno.",
            _PARAMETRO_PERIODO,
            _productos_mas_vendidos,
        ),
        Herramienta(
            reglas.PRODUCTOS_POR_VENCER,
            "Productos con mercadería vencida o próxima a vencer según los días de aviso configurados.",
            _SIN_PARAMETROS,
            _productos_por_vencer,
        ),
        Herramienta(
            reglas.REPOSICION_SUGERIDA,
            "Productos por debajo de su stock mínimo con la cantidad sugerida a comprar y su proveedor principal.",
            _SIN_PARAMETROS,
            _reposicion_sugerida,
        ),
        Herramienta(
            reglas.MARGEN_NEGATIVO,
            "Productos vendidos en un período cuyo margen bruto sobre costo de referencia es negativo.",
            _PARAMETRO_PERIODO,
            _margen_negativo,
        ),
        Herramienta(
            reglas.PROVEEDORES_CON_RECLAMOS,
            "Proveedores con reclamos abiertos (pendientes o reconocidos) y cuántos tiene cada uno.",
            _SIN_PARAMETROS,
            _proveedores_con_reclamos,
        ),
        Herramienta(
            reglas.DEUDA_DE_CLIENTES,
            "Cuánto deben los clientes en cuenta corriente en total; para el dueño, también por antigüedad y mayores deudores.",
            _SIN_PARAMETROS,
            _deuda_de_clientes,
        ),
        Herramienta(
            reglas.DEUDA_A_PROVEEDORES,
            "Cuánto se debe a proveedores: total, vencido, próximo a vencer, vigente y mayores saldos.",
            _SIN_PARAMETROS,
            _deuda_a_proveedores,
        ),
        Herramienta(
            reglas.GUIA_DE_USO,
            "Pasos para hacer algo en Twinx Express (cómo registrar una compra, restaurar un backup, vender, cerrar la caja...).",
            {
                "type": "object",
                "properties": {"tema": {"type": "string", "enum": sorted(guia.TEMAS), "description": "Tema de la guía."}},
                "required": ["tema"],
            },
            _guia_de_uso,
        ),
    )
}

assert set(HERRAMIENTAS) == set(reglas.PERMISOS), "Toda herramienta necesita su regla de permiso y viceversa."


def definiciones_para(rol: str) -> list[dict]:
    """Las herramientas que se le describen al modelo: solo las que el rol puede usar. Lo que el modelo no
    conoce no lo puede pedir; y aunque lo pidiera, `ejecutar` lo rechaza."""
    return [HERRAMIENTAS[nombre].definicion for nombre in reglas.herramientas_para(rol)]


def validar_argumentos(herramienta: Herramienta, argumentos: object) -> dict:
    """Solo los parámetros declarados, del tipo declarado y dentro de su `enum`. Los argumentos pueden venir
    de un modelo: cualquier cosa fuera del esquema es un error de datos, no una conjetura."""
    if argumentos is None:
        argumentos = {}
    if not isinstance(argumentos, dict):
        raise DatosInvalidosError("Los argumentos de la herramienta deben ser un objeto.")
    propiedades: dict = herramienta.parametros.get("properties", {})
    limpios: dict = {}
    for clave, valor in argumentos.items():
        if clave not in propiedades:
            raise DatosInvalidosError(f"La herramienta {herramienta.nombre} no acepta el argumento {clave!r}.")
        if valor is None or valor == "":
            continue
        esquema = propiedades[clave]
        if esquema.get("type") == "string" and not isinstance(valor, str):
            raise DatosInvalidosError(f"El argumento {clave!r} debe ser texto.")
        if "enum" in esquema and valor not in esquema["enum"]:
            raise DatosInvalidosError(f"El argumento {clave!r} no admite el valor {sanitizar_texto_externo(valor, 40)!r}.")
        limpios[clave] = valor
    for requerido in herramienta.parametros.get("required", []):
        if requerido not in limpios:
            raise DatosInvalidosError(f"La herramienta {herramienta.nombre} requiere el argumento {requerido!r}.")
    return limpios


def ejecutar(nombre: str, argumentos: object, usuario: Usuario, hoy: date | None = None) -> ResultadoHerramienta:
    """Ejecuta una herramienta en nombre de `usuario`. El permiso se verifica acá, siempre, sin importar si el
    pedido vino de la ayuda integrada o de un modelo.

    Raises:
        DatosInvalidosError: herramienta desconocida o argumentos fuera del esquema.
        PermisoDenegadoError: el rol del usuario no puede usar esa herramienta."""
    herramienta = HERRAMIENTAS.get(nombre)
    if herramienta is None:
        raise DatosInvalidosError(f"No existe la herramienta {sanitizar_texto_externo(nombre, 40)!r}.")
    if not puede_usar(nombre, usuario.rol):
        raise PermisoDenegadoError("Esa información es solo para el dueño.")
    limpios = validar_argumentos(herramienta, argumentos)
    return herramienta.ejecutar(limpios, usuario, hoy or date.today())
