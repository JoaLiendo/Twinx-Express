"""Repositorio del modelo fiscal (migración 041): perfil del emisor, tratamiento de IVA de productos,
comprobantes fiscales con sus líneas y alícuotas, e intentos contra ARCA. Solo SQL parametrizado.

Las invariantes críticas (máquina de estados, inmutabilidad, unicidad de numeración, una sola factura
principal viva por venta, una incertidumbre por secuencia) las garantiza el esquema: este módulo no las
reimplementa, solo las traduce a excepciones de dominio cuando chocan.
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass

from db.conexion import obtener_conexion
from domain.fiscal.comprobante import ComprobanteFiscal, SnapshotEmisor, SnapshotReceptor
from domain.fiscal.emisor import PerfilFiscal
from domain.fiscal.iva import AlicuotaFiscal, ImportesFiscales, LineaFiscal, TratamientoIva

# --- Perfil del emisor ---------------------------------------------------------------------------

_COLUMNAS_PERFIL = (
    "cuit, razon_social, condicion_iva, domicilio, ingresos_brutos, inicio_actividades, habilitacion_clase_a,"
    " punto_venta_cae, punto_venta_cae_validado, punto_venta_caea, punto_venta_caea_validado, contingencia_caea,"
    " ambiente"
)


def _fila_a_perfil(fila: sqlite3.Row) -> PerfilFiscal:
    return PerfilFiscal(
        cuit=fila["cuit"],
        razon_social=fila["razon_social"],
        condicion_iva=fila["condicion_iva"],
        domicilio=fila["domicilio"],
        ingresos_brutos=fila["ingresos_brutos"],
        inicio_actividades=fila["inicio_actividades"],
        habilitacion_clase_a=fila["habilitacion_clase_a"],
        punto_venta_cae=fila["punto_venta_cae"],
        punto_venta_cae_validado=fila["punto_venta_cae_validado"],
        punto_venta_caea=fila["punto_venta_caea"],
        punto_venta_caea_validado=fila["punto_venta_caea_validado"],
        contingencia_caea=bool(fila["contingencia_caea"]),
        ambiente=fila["ambiente"],
    )


def obtener_perfil_en_conexion(conexion: sqlite3.Connection) -> PerfilFiscal | None:
    fila = conexion.execute(f"SELECT {_COLUMNAS_PERFIL} FROM perfil_fiscal WHERE id = 1").fetchone()
    return _fila_a_perfil(fila) if fila is not None else None


def obtener_perfil() -> PerfilFiscal | None:
    with obtener_conexion() as conexion:
        return obtener_perfil_en_conexion(conexion)


def guardar_perfil_en_conexion(conexion: sqlite3.Connection, perfil: PerfilFiscal) -> None:
    """Alta o edición de los datos del emisor. Nunca cambia el ambiente (ver `cambiar_ambiente_en_conexion`).
    Un punto de venta que cambia pierde su validación contra ARCA."""
    actual = obtener_perfil_en_conexion(conexion)
    if actual is None:
        conexion.execute(
            """
            INSERT INTO perfil_fiscal
                (id, cuit, razon_social, condicion_iva, domicilio, ingresos_brutos, inicio_actividades,
                 habilitacion_clase_a, punto_venta_cae, punto_venta_caea, contingencia_caea)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                perfil.cuit,
                perfil.razon_social,
                perfil.condicion_iva,
                perfil.domicilio,
                perfil.ingresos_brutos,
                perfil.inicio_actividades,
                perfil.habilitacion_clase_a,
                perfil.punto_venta_cae,
                perfil.punto_venta_caea,
                int(perfil.contingencia_caea),
            ),
        )
        return
    validado_cae = actual.punto_venta_cae_validado if (
        actual.punto_venta_cae == perfil.punto_venta_cae and actual.cuit == perfil.cuit
    ) else None
    validado_caea = actual.punto_venta_caea_validado if (
        actual.punto_venta_caea == perfil.punto_venta_caea and actual.cuit == perfil.cuit
    ) else None
    conexion.execute(
        """
        UPDATE perfil_fiscal
        SET cuit = ?, razon_social = ?, condicion_iva = ?, domicilio = ?, ingresos_brutos = ?,
            inicio_actividades = ?, habilitacion_clase_a = ?, punto_venta_cae = ?, punto_venta_cae_validado = ?,
            punto_venta_caea = ?, punto_venta_caea_validado = ?, contingencia_caea = ?,
            fecha_actualizacion = datetime('now', 'localtime')
        WHERE id = 1
        """,
        (
            perfil.cuit,
            perfil.razon_social,
            perfil.condicion_iva,
            perfil.domicilio,
            perfil.ingresos_brutos,
            perfil.inicio_actividades,
            perfil.habilitacion_clase_a,
            perfil.punto_venta_cae,
            validado_cae,
            perfil.punto_venta_caea,
            validado_caea,
            int(perfil.contingencia_caea),
        ),
    )


def marcar_punto_venta_validado_en_conexion(conexion: sqlite3.Connection, caea: bool, ambiente: str | None) -> None:
    """Registra en qué ambiente ARCA confirmó el punto de venta (o lo invalida con `None`)."""
    columna = "punto_venta_caea_validado" if caea else "punto_venta_cae_validado"
    conexion.execute(
        f"UPDATE perfil_fiscal SET {columna} = ?, fecha_actualizacion = datetime('now', 'localtime') WHERE id = 1",
        (ambiente,),
    )


def registrar_habilitacion_produccion_en_conexion(
    conexion: sqlite3.Connection, cuit: str, huella_certificado: str, detalle: str
) -> None:
    conexion.execute(
        "INSERT INTO habilitaciones_produccion_fiscal (cuit, huella_certificado, detalle) VALUES (?, ?, ?)",
        (cuit, huella_certificado, detalle),
    )


def cambiar_ambiente_en_conexion(conexion: sqlite3.Connection, ambiente: str) -> None:
    """Cambia de ambiente invalidando la validación de los puntos de venta (son de otro ambiente). Pasar a
    PRODUCCION sin habilitación registrada lo rechaza un trigger."""
    conexion.execute(
        """
        UPDATE perfil_fiscal
        SET punto_venta_cae_validado = NULL, punto_venta_caea_validado = NULL, ambiente = ?,
            fecha_actualizacion = datetime('now', 'localtime')
        WHERE id = 1
        """,
        (ambiente,),
    )


# --- Tratamiento de IVA de productos ---------------------------------------------------------------


@dataclass(frozen=True)
class ProductoConTratamiento:
    producto_id: int
    codigo_barras: str
    nombre: str
    activo: bool
    precio_venta_centavos: int
    tratamiento: TratamientoIva | None


def _tratamiento_de_fila(tratamiento: str | None, alicuota: int | None) -> TratamientoIva | None:
    return TratamientoIva(tratamiento, alicuota) if tratamiento is not None else None


def obtener_tratamiento_en_conexion(conexion: sqlite3.Connection, producto_id: int) -> TratamientoIva | None:
    fila = conexion.execute(
        "SELECT tratamiento_iva, alicuota_iva_id FROM productos_fiscal WHERE producto_id = ?", (producto_id,)
    ).fetchone()
    return _tratamiento_de_fila(fila["tratamiento_iva"], fila["alicuota_iva_id"]) if fila is not None else None


def obtener_tratamiento(producto_id: int) -> TratamientoIva | None:
    with obtener_conexion() as conexion:
        return obtener_tratamiento_en_conexion(conexion, producto_id)


def guardar_tratamiento_en_conexion(
    conexion: sqlite3.Connection, producto_id: int, tratamiento: TratamientoIva, usuario_id: int | None
) -> None:
    conexion.execute(
        """
        INSERT INTO productos_fiscal (producto_id, tratamiento_iva, alicuota_iva_id, usuario_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(producto_id) DO UPDATE SET tratamiento_iva = excluded.tratamiento_iva,
            alicuota_iva_id = excluded.alicuota_iva_id, usuario_id = excluded.usuario_id,
            fecha_actualizacion = datetime('now', 'localtime')
        """,
        (producto_id, tratamiento.tratamiento, tratamiento.alicuota_iva_id, usuario_id),
    )


def listar_productos_con_tratamiento(solo_sin_clasificar: bool, texto: str, limite: int) -> list[ProductoConTratamiento]:
    """Productos (activos primero, por nombre) con su tratamiento de IVA, filtrables por nombre o código."""
    condiciones = ["1 = 1"]
    parametros: list = []
    if solo_sin_clasificar:
        condiciones.append("pf.producto_id IS NULL")
    if texto:
        condiciones.append("(p.nombre LIKE ? ESCAPE '\\' OR p.codigo_barras = ?)")
        patron = "%" + texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        parametros += [patron, texto]
    parametros.append(limite)
    with obtener_conexion() as conexion:
        filas = conexion.execute(
            f"""
            SELECT p.id, p.codigo_barras, p.nombre, p.activo, p.precio_venta_centavos,
                   pf.tratamiento_iva, pf.alicuota_iva_id
            FROM productos p LEFT JOIN productos_fiscal pf ON pf.producto_id = p.id
            WHERE {' AND '.join(condiciones)}
            ORDER BY p.activo DESC, p.nombre COLLATE NOCASE, p.id
            LIMIT ?
            """,
            parametros,
        ).fetchall()
    return [
        ProductoConTratamiento(
            producto_id=fila["id"],
            codigo_barras=fila["codigo_barras"],
            nombre=fila["nombre"],
            activo=bool(fila["activo"]),
            precio_venta_centavos=fila["precio_venta_centavos"],
            tratamiento=_tratamiento_de_fila(fila["tratamiento_iva"], fila["alicuota_iva_id"]),
        )
        for fila in filas
    ]


def contar_productos_activos_sin_clasificar() -> int:
    with obtener_conexion() as conexion:
        return conexion.execute(
            "SELECT COUNT(*) FROM productos p LEFT JOIN productos_fiscal pf ON pf.producto_id = p.id"
            " WHERE p.activo = 1 AND pf.producto_id IS NULL"
        ).fetchone()[0]


# --- Venta a fiscalizar -------------------------------------------------------------------------------


@dataclass(frozen=True)
class VentaParaFiscalizar:
    venta_id: int
    estado: str
    total_centavos: int
    cliente_id: int | None
    lineas: tuple[LineaFiscal, ...]


def venta_para_fiscalizar_en_conexion(conexion: sqlite3.Connection, venta_id: int) -> VentaParaFiscalizar | None:
    """La venta con sus líneas (precio congelado de `detalle_venta`, nombre y código actuales del producto,
    que quedan congelados en el snapshot) y el tratamiento de IVA vigente de cada producto."""
    cabecera = conexion.execute(
        "SELECT id, estado, total_centavos, cliente_id FROM ventas WHERE id = ?", (venta_id,)
    ).fetchone()
    if cabecera is None:
        return None
    filas = conexion.execute(
        """
        SELECT d.producto_id, p.codigo_barras, p.nombre, d.cantidad, d.precio_unitario_centavos, d.subtotal_centavos,
               pf.tratamiento_iva, pf.alicuota_iva_id
        FROM detalle_venta d
        JOIN productos p ON p.id = d.producto_id
        LEFT JOIN productos_fiscal pf ON pf.producto_id = d.producto_id
        WHERE d.venta_id = ?
        ORDER BY d.id
        """,
        (venta_id,),
    ).fetchall()
    lineas = tuple(
        LineaFiscal(
            producto_id=fila["producto_id"],
            codigo=fila["codigo_barras"],
            descripcion=fila["nombre"],
            cantidad=fila["cantidad"],
            precio_unitario_centavos=fila["precio_unitario_centavos"],
            subtotal_centavos=fila["subtotal_centavos"],
            tratamiento=_tratamiento_de_fila(fila["tratamiento_iva"], fila["alicuota_iva_id"]),
        )
        for fila in filas
    )
    return VentaParaFiscalizar(
        venta_id=cabecera["id"],
        estado=cabecera["estado"],
        total_centavos=cabecera["total_centavos"],
        cliente_id=cabecera["cliente_id"],
        lineas=lineas,
    )


# --- Comprobantes ---------------------------------------------------------------------------------

_COLUMNAS_COMPROBANTE = """
    id, venta_id, rol, comprobante_asociado_id, ambiente, modalidad, caea_id, punto_venta, tipo_comprobante, clase,
    numero, fecha_comprobante, fecha_hora_generacion, concepto, moneda, cotizacion, imp_total_centavos,
    imp_tot_conc_centavos, imp_neto_centavos, imp_op_ex_centavos, imp_trib_centavos, imp_iva_centavos,
    snapshot_emisor_json, snapshot_receptor_json, contenido_hash, estado, resultado, codigo_autorizacion,
    vencimiento_autorizacion, fecha_proceso, observaciones_json, errores_json, fecha_creacion, fecha_actualizacion
"""


@dataclass(frozen=True)
class NuevoComprobante:
    """Todo lo que se congela al crear un comprobante (el snapshot)."""

    venta_id: int
    rol: str
    comprobante_asociado_id: int | None
    ambiente: str
    modalidad: str
    caea_id: int | None
    tipo_comprobante: int
    clase: str
    concepto: int
    moneda: str
    cotizacion: str
    emisor: SnapshotEmisor
    receptor: SnapshotReceptor
    importes: ImportesFiscales
    lineas: tuple[LineaFiscal, ...]
    contenido_hash: str
    usuario_id: int | None
    estado: str
    numero: int | None = None
    fecha_comprobante: str | None = None
    fecha_hora_generacion: str | None = None
    codigo_autorizacion: str | None = None
    vencimiento_autorizacion: str | None = None


def insertar_comprobante_en_conexion(conexion: sqlite3.Connection, nuevo: NuevoComprobante) -> int:
    importes = nuevo.importes
    fila = conexion.execute(
        """
        INSERT INTO comprobantes_fiscales
            (venta_id, rol, comprobante_asociado_id, ambiente, modalidad, caea_id, cuit_emisor, punto_venta,
             tipo_comprobante, clase, numero, fecha_comprobante, fecha_hora_generacion, concepto, moneda, cotizacion,
             doc_tipo, doc_nro, condicion_iva_receptor_id, imp_total_centavos, imp_tot_conc_centavos,
             imp_neto_centavos, imp_op_ex_centavos, imp_trib_centavos, imp_iva_centavos, snapshot_emisor_json,
             snapshot_receptor_json, contenido_hash, estado, codigo_autorizacion, vencimiento_autorizacion, usuario_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            nuevo.venta_id,
            nuevo.rol,
            nuevo.comprobante_asociado_id,
            nuevo.ambiente,
            nuevo.modalidad,
            nuevo.caea_id,
            nuevo.emisor.cuit,
            nuevo.emisor.punto_venta,
            nuevo.tipo_comprobante,
            nuevo.clase,
            nuevo.numero,
            nuevo.fecha_comprobante,
            nuevo.fecha_hora_generacion,
            nuevo.concepto,
            nuevo.moneda,
            nuevo.cotizacion,
            nuevo.receptor.doc_tipo,
            nuevo.receptor.doc_nro,
            nuevo.receptor.condicion_iva_receptor_id,
            importes.total_centavos,
            importes.no_gravado_centavos,
            importes.neto_centavos,
            importes.exento_centavos,
            importes.tributos_centavos,
            importes.iva_centavos,
            nuevo.emisor.a_json(),
            nuevo.receptor.a_json(),
            nuevo.contenido_hash,
            nuevo.estado,
            nuevo.codigo_autorizacion,
            nuevo.vencimiento_autorizacion,
            nuevo.usuario_id,
        ),
    ).fetchone()
    comprobante_id = fila["id"]
    for orden, linea in enumerate(nuevo.lineas, start=1):
        tratamiento = linea.tratamiento
        conexion.execute(
            """
            INSERT INTO comprobante_fiscal_lineas
                (comprobante_id, orden, producto_id, codigo, descripcion, cantidad, precio_unitario_centavos,
                 subtotal_centavos, tratamiento_iva, alicuota_iva_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                comprobante_id,
                orden,
                linea.producto_id,
                linea.codigo,
                linea.descripcion,
                linea.cantidad,
                linea.precio_unitario_centavos,
                linea.subtotal_centavos,
                tratamiento.tratamiento if tratamiento else None,
                tratamiento.alicuota_iva_id if tratamiento else None,
            ),
        )
    for alicuota in importes.alicuotas:
        conexion.execute(
            "INSERT INTO comprobante_fiscal_alicuotas (comprobante_id, alicuota_iva_id, base_imponible_centavos,"
            " importe_centavos) VALUES (?, ?, ?, ?)",
            (comprobante_id, alicuota.alicuota_iva_id, alicuota.base_imponible_centavos, alicuota.importe_centavos),
        )
    return comprobante_id


def _fila_a_comprobante(conexion: sqlite3.Connection, fila: sqlite3.Row) -> ComprobanteFiscal:
    alicuotas = tuple(
        AlicuotaFiscal(a["alicuota_iva_id"], a["base_imponible_centavos"], a["importe_centavos"])
        for a in conexion.execute(
            "SELECT alicuota_iva_id, base_imponible_centavos, importe_centavos FROM comprobante_fiscal_alicuotas"
            " WHERE comprobante_id = ? ORDER BY alicuota_iva_id",
            (fila["id"],),
        )
    )
    lineas = tuple(
        LineaFiscal(
            producto_id=linea["producto_id"],
            codigo=linea["codigo"],
            descripcion=linea["descripcion"],
            cantidad=linea["cantidad"],
            precio_unitario_centavos=linea["precio_unitario_centavos"],
            subtotal_centavos=linea["subtotal_centavos"],
            tratamiento=_tratamiento_de_fila(linea["tratamiento_iva"], linea["alicuota_iva_id"]),
        )
        for linea in conexion.execute(
            "SELECT * FROM comprobante_fiscal_lineas WHERE comprobante_id = ? ORDER BY orden", (fila["id"],)
        )
    )
    importes = ImportesFiscales(
        total_centavos=fila["imp_total_centavos"],
        no_gravado_centavos=fila["imp_tot_conc_centavos"],
        neto_centavos=fila["imp_neto_centavos"],
        exento_centavos=fila["imp_op_ex_centavos"],
        tributos_centavos=fila["imp_trib_centavos"],
        iva_centavos=fila["imp_iva_centavos"],
        alicuotas=alicuotas,
    )
    return ComprobanteFiscal(
        id=fila["id"],
        venta_id=fila["venta_id"],
        rol=fila["rol"],
        comprobante_asociado_id=fila["comprobante_asociado_id"],
        ambiente=fila["ambiente"],
        modalidad=fila["modalidad"],
        caea_id=fila["caea_id"],
        punto_venta=fila["punto_venta"],
        tipo_comprobante=fila["tipo_comprobante"],
        clase=fila["clase"],
        numero=fila["numero"],
        fecha_comprobante=fila["fecha_comprobante"],
        fecha_hora_generacion=fila["fecha_hora_generacion"],
        concepto=fila["concepto"],
        moneda=fila["moneda"],
        cotizacion=fila["cotizacion"],
        emisor=SnapshotEmisor.desde_json(fila["snapshot_emisor_json"]),
        receptor=SnapshotReceptor.desde_json(fila["snapshot_receptor_json"]),
        importes=importes,
        estado=fila["estado"],
        resultado=fila["resultado"],
        codigo_autorizacion=fila["codigo_autorizacion"],
        vencimiento_autorizacion=fila["vencimiento_autorizacion"],
        fecha_proceso=fila["fecha_proceso"],
        observaciones=json.loads(fila["observaciones_json"]) if fila["observaciones_json"] else [],
        errores=json.loads(fila["errores_json"]) if fila["errores_json"] else [],
        contenido_hash=fila["contenido_hash"],
        fecha_creacion=fila["fecha_creacion"],
        fecha_actualizacion=fila["fecha_actualizacion"],
        lineas=lineas,
    )


def obtener_comprobante_en_conexion(conexion: sqlite3.Connection, comprobante_id: int) -> ComprobanteFiscal | None:
    fila = conexion.execute(
        f"SELECT {_COLUMNAS_COMPROBANTE} FROM comprobantes_fiscales WHERE id = ?", (comprobante_id,)
    ).fetchone()
    return _fila_a_comprobante(conexion, fila) if fila is not None else None


def obtener_comprobante(comprobante_id: int) -> ComprobanteFiscal | None:
    with obtener_conexion() as conexion:
        return obtener_comprobante_en_conexion(conexion, comprobante_id)


def _listar(conexion: sqlite3.Connection, condicion: str, parametros: tuple, sufijo: str = "") -> list[ComprobanteFiscal]:
    filas = conexion.execute(
        f"SELECT {_COLUMNAS_COMPROBANTE} FROM comprobantes_fiscales WHERE {condicion} {sufijo}", parametros
    ).fetchall()
    return [_fila_a_comprobante(conexion, fila) for fila in filas]


def listar_comprobantes_de_venta(venta_id: int) -> list[ComprobanteFiscal]:
    with obtener_conexion() as conexion:
        return _listar(conexion, "venta_id = ?", (venta_id,), "ORDER BY id")


def principal_vivo_de_venta_en_conexion(conexion: sqlite3.Connection, venta_id: int) -> ComprobanteFiscal | None:
    encontrados = _listar(
        conexion, "venta_id = ? AND rol = 'PRINCIPAL' AND estado NOT IN ('RECHAZADO', 'DESCARTADO')", (venta_id,)
    )
    return encontrados[0] if encontrados else None


def nota_viva_de_en_conexion(conexion: sqlite3.Connection, comprobante_id: int, rol: str) -> ComprobanteFiscal | None:
    encontrados = _listar(
        conexion,
        "comprobante_asociado_id = ? AND rol = ? AND estado NOT IN ('RECHAZADO', 'DESCARTADO')",
        (comprobante_id, rol),
    )
    return encontrados[0] if encontrados else None


def en_vuelo_de_secuencia_en_conexion(
    conexion: sqlite3.Connection, ambiente: str, cuit: str, punto_venta: int, tipo: int
) -> ComprobanteFiscal | None:
    encontrados = _listar(
        conexion,
        "ambiente = ? AND cuit_emisor = ? AND punto_venta = ? AND tipo_comprobante = ?"
        " AND estado IN ('ENVIANDO', 'INCIERTO')",
        (ambiente, cuit, punto_venta, tipo),
    )
    return encontrados[0] if encontrados else None


def listar_por_estados(estados: tuple[str, ...], limite: int = 100) -> list[ComprobanteFiscal]:
    marcadores = ", ".join("?" for _ in estados)
    with obtener_conexion() as conexion:
        return _listar(conexion, f"estado IN ({marcadores})", (*estados, limite), "ORDER BY id DESC LIMIT ?")


def ids_por_estados_en_conexion(conexion: sqlite3.Connection, estados: tuple[str, ...]) -> list[int]:
    marcadores = ", ".join("?" for _ in estados)
    return [
        fila[0]
        for fila in conexion.execute(f"SELECT id FROM comprobantes_fiscales WHERE estado IN ({marcadores}) ORDER BY id", estados)
    ]


def contar_por_estado() -> dict[str, int]:
    with obtener_conexion() as conexion:
        return {
            fila["estado"]: fila["n"]
            for fila in conexion.execute("SELECT estado, COUNT(*) AS n FROM comprobantes_fiscales GROUP BY estado")
        }


def ultimo_autorizado_local_en_conexion(
    conexion: sqlite3.Connection, ambiente: str, cuit: str, punto_venta: int, tipo: int
) -> int:
    """Mayor número autorizado que Twinx registró para la secuencia (0 si ninguno)."""
    fila = conexion.execute(
        """
        SELECT COALESCE(MAX(numero), 0) FROM comprobantes_fiscales
        WHERE ambiente = ? AND cuit_emisor = ? AND punto_venta = ? AND tipo_comprobante = ?
          AND estado IN ('AUTORIZADO', 'CAEA_PENDIENTE_INFORMAR', 'CAEA_INFORMADO', 'CAEA_INFORME_RECHAZADO')
        """,
        (ambiente, cuit, punto_venta, tipo),
    ).fetchone()
    return fila[0]


_CAMPOS_MUTABLES = frozenset(
    {
        "numero",
        "fecha_comprobante",
        "resultado",
        "codigo_autorizacion",
        "vencimiento_autorizacion",
        "fecha_proceso",
        "observaciones_json",
        "errores_json",
    }
)


def actualizar_estado_en_conexion(conexion: sqlite3.Connection, comprobante_id: int, estado: str, **campos) -> None:
    """Transiciona el comprobante. Los triggers deciden si la transición y los campos son válidos."""
    desconocidos = set(campos) - _CAMPOS_MUTABLES
    if desconocidos:
        raise ValueError(f"Campos no actualizables de un comprobante fiscal: {sorted(desconocidos)}")
    asignaciones = ", ".join(f"{campo} = ?" for campo in campos)
    sentencia = "UPDATE comprobantes_fiscales SET estado = ?, fecha_actualizacion = datetime('now', 'localtime')"
    if asignaciones:
        sentencia += ", " + asignaciones
    conexion.execute(sentencia + " WHERE id = ?", (estado, *campos.values(), comprobante_id))


def venta_tiene_comprobante_vivo_en_conexion(conexion: sqlite3.Connection, venta_id: int) -> bool:
    """La venta tiene una factura principal no rechazada ni descartada (la anulación local queda bloqueada)."""
    return (
        conexion.execute(
            "SELECT 1 FROM comprobantes_fiscales WHERE venta_id = ? AND rol = 'PRINCIPAL'"
            " AND estado NOT IN ('RECHAZADO', 'DESCARTADO')",
            (venta_id,),
        ).fetchone()
        is not None
    )


def estado_fiscal_de_ventas(venta_ids: list[int]) -> dict[int, str]:
    """Estado del comprobante principal vivo de cada venta (las que no tienen, no figuran)."""
    if not venta_ids:
        return {}
    marcadores = ", ".join("?" for _ in venta_ids)
    with obtener_conexion() as conexion:
        return {
            fila["venta_id"]: fila["estado"]
            for fila in conexion.execute(
                f"SELECT venta_id, estado FROM comprobantes_fiscales WHERE rol = 'PRINCIPAL'"
                f" AND estado NOT IN ('RECHAZADO', 'DESCARTADO') AND venta_id IN ({marcadores})",
                venta_ids,
            )
        }


# --- Intentos -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IntentoFiscal:
    id: int
    comprobante_id: int | None
    operacion: str
    ambiente: str
    correlacion: str
    numero: int | None
    fecha_inicio: str
    fecha_fin: str | None
    duracion_ms: int | None
    resultado: str | None
    detalle: dict


def abrir_intento_en_conexion(
    conexion: sqlite3.Connection,
    comprobante_id: int | None,
    operacion: str,
    ambiente: str,
    numero: int | None = None,
    request_hash: str | None = None,
) -> str:
    correlacion = uuid.uuid4().hex
    conexion.execute(
        "INSERT INTO intentos_fiscales (comprobante_id, operacion, ambiente, correlacion, numero, request_hash)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (comprobante_id, operacion, ambiente, correlacion, numero, request_hash),
    )
    return correlacion


def cerrar_intento_en_conexion(
    conexion: sqlite3.Connection, correlacion: str, resultado: str, duracion_ms: int, detalle: dict | None
) -> None:
    conexion.execute(
        "UPDATE intentos_fiscales SET fecha_fin = strftime('%Y-%m-%d %H:%M:%f', 'now', 'localtime'), resultado = ?,"
        " duracion_ms = ?, detalle_json = ? WHERE correlacion = ?",
        (resultado, duracion_ms, json.dumps(detalle, ensure_ascii=False) if detalle else None, correlacion),
    )


def _fila_a_intento(fila: sqlite3.Row) -> IntentoFiscal:
    return IntentoFiscal(
        id=fila["id"],
        comprobante_id=fila["comprobante_id"],
        operacion=fila["operacion"],
        ambiente=fila["ambiente"],
        correlacion=fila["correlacion"],
        numero=fila["numero"],
        fecha_inicio=fila["fecha_inicio"],
        fecha_fin=fila["fecha_fin"],
        duracion_ms=fila["duracion_ms"],
        resultado=fila["resultado"],
        detalle=json.loads(fila["detalle_json"]) if fila["detalle_json"] else {},
    )


def listar_intentos(comprobante_id: int) -> list[IntentoFiscal]:
    with obtener_conexion() as conexion:
        return [
            _fila_a_intento(fila)
            for fila in conexion.execute(
                "SELECT * FROM intentos_fiscales WHERE comprobante_id = ? ORDER BY id", (comprobante_id,)
            )
        ]


def listar_errores_recientes(limite: int = 10) -> list[IntentoFiscal]:
    with obtener_conexion() as conexion:
        return [
            _fila_a_intento(fila)
            for fila in conexion.execute(
                "SELECT * FROM intentos_fiscales WHERE resultado IN ('R', 'INCIERTO', 'NO_ENVIADO', 'ERROR')"
                " ORDER BY id DESC LIMIT ?",
                (limite,),
            )
        ]
