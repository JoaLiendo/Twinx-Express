"""Estado de preparación para emitir facturas electrónicas (cierre operativo, E2).

Lista verificable de lo que falta para emitir comprobantes con validez fiscal, con su responsable. Solo
lee lo que ya está en `servicio_panel.EstadoPanel` (nunca consulta ARCA al abrir el panel): que un dato
esté cargado no lo da por válido, salvo que ARCA lo haya confirmado (punto de venta validado en el ambiente
en uso) o el archivo lo demuestre (certificado vigente). Distingue falta de configuración (dueño), falta de
autorización externa (ARCA / contador / técnico), homologación no verificada, error temporal (comprobantes
inciertos) y advertencias.
"""

from dataclasses import dataclass

from domain.fiscal import comprobante as estados
from domain.fiscal.comprobante import AMBIENTE_HOMOLOGACION, AMBIENTE_PRODUCCION
from services.fiscal import credenciales
from services.fiscal.servicio_panel import EstadoPanel

PASO_OK = "OK"
PASO_PENDIENTE = "PENDIENTE"  # falta configuración: lo resuelve el dueño en Twinx
PASO_EXTERNO = "EXTERNO"  # falta un trámite o autorización fuera de Twinx
PASO_ADVERTENCIA = "ADVERTENCIA"  # no bloquea del todo, pero hay que revisarlo

RESPONSABLE_DUENO = "Dueño (en Twinx)"
RESPONSABLE_TECNICO = "Técnico"
RESPONSABLE_ARCA = "ARCA / contador"


@dataclass(frozen=True)
class PasoPreparacion:
    clave: str
    titulo: str
    estado: str
    responsable: str
    detalle: str
    enlace: str | None = None


@dataclass(frozen=True)
class Preparacion:
    pasos: list[PasoPreparacion]
    situacion: str
    puede_emitir_con_validez: bool
    solo_pruebas: bool

    @property
    def pendientes(self) -> list[PasoPreparacion]:
        return [p for p in self.pasos if p.estado in (PASO_PENDIENTE, PASO_EXTERNO)]


def _paso_certificado(ambiente: str, certificado: credenciales.InfoCertificado | None) -> PasoPreparacion:
    nombre = "homologación" if ambiente == AMBIENTE_HOMOLOGACION else "producción"
    titulo = f"Certificado digital de {nombre}"
    if certificado is None or certificado.estado in (credenciales.AUSENTE, credenciales.ILEGIBLE):
        return PasoPreparacion(
            "certificado", titulo, PASO_PENDIENTE, RESPONSABLE_DUENO,
            "No hay un certificado utilizable para el ambiente en uso. Se genera en ARCA (Administrador de "
            "Relaciones de Clave Fiscal, servicio «wsfe») y se carga acá con su clave privada.",
            "/fiscal/certificados",
        )
    if certificado.estado == credenciales.VENCIDO:
        return PasoPreparacion(
            "certificado", titulo, PASO_EXTERNO, RESPONSABLE_ARCA,
            f"Vencido el {certificado.vence}: hay que renovarlo en ARCA y volver a cargarlo.", "/fiscal/certificados",
        )
    if certificado.estado == credenciales.POR_VENCER:
        return PasoPreparacion(
            "certificado", titulo, PASO_ADVERTENCIA, RESPONSABLE_ARCA,
            f"Vigente, pero vence en {certificado.dias_restantes} día(s) ({certificado.vence}): renovalo a tiempo.",
            "/fiscal/certificados",
        )
    return PasoPreparacion("certificado", titulo, PASO_OK, RESPONSABLE_DUENO, f"Vigente hasta {certificado.vence}.", "/fiscal/certificados")


def preparacion(panel: EstadoPanel) -> Preparacion:
    perfil = panel.perfil
    ambiente = perfil.ambiente if perfil else AMBIENTE_HOMOLOGACION
    nombre_ambiente = "homologación" if ambiente == AMBIENTE_HOMOLOGACION else "producción"
    pasos: list[PasoPreparacion] = []

    if perfil is None:
        pasos.append(PasoPreparacion(
            "perfil", "Perfil fiscal del emisor", PASO_PENDIENTE, RESPONSABLE_DUENO,
            "Cargá CUIT, razón social, condición frente al IVA y domicilio.", "/fiscal/configuracion",
        ))
    else:
        pasos.append(PasoPreparacion(
            "perfil", "Perfil fiscal del emisor", PASO_OK, RESPONSABLE_DUENO,
            f"{perfil.razon_social} · CUIT {perfil.cuit} · {perfil.condicion_etiqueta}.", "/fiscal/configuracion",
        ))

    pasos.append(_paso_certificado(ambiente, panel.certificados.get(ambiente)))

    if perfil is None or perfil.punto_venta_cae is None:
        pasos.append(PasoPreparacion(
            "punto_venta", "Punto de venta", PASO_PENDIENTE, RESPONSABLE_DUENO,
            "Indicá el punto de venta habilitado en ARCA para factura electrónica por web service.", "/fiscal/configuracion",
        ))
    elif not perfil.punto_venta_cae_listo:
        pasos.append(PasoPreparacion(
            "punto_venta", "Punto de venta validado con ARCA", PASO_PENDIENTE, RESPONSABLE_DUENO,
            f"El punto de venta {perfil.punto_venta_cae} está cargado pero ARCA todavía no lo confirmó en {nombre_ambiente}: "
            "usá «Validar punto de venta» (requiere certificado y conexión).", "/fiscal/configuracion",
        ))
    else:
        pasos.append(PasoPreparacion(
            "punto_venta", "Punto de venta validado con ARCA", PASO_OK, RESPONSABLE_DUENO,
            f"Punto de venta {perfil.punto_venta_cae} confirmado por ARCA en {nombre_ambiente}.", "/fiscal/configuracion",
        ))

    if perfil is not None:
        if panel.tablas:
            pasos.append(PasoPreparacion("tablas", "Tablas de parámetros de ARCA", PASO_OK, RESPONSABLE_DUENO,
                                         f"{len(panel.tablas)} tabla(s) en caché local.", "/fiscal"))
        else:
            pasos.append(PasoPreparacion(
                "tablas", "Tablas de parámetros de ARCA", PASO_PENDIENTE, RESPONSABLE_DUENO,
                "Todavía no se descargaron las tablas de ARCA (tipos de comprobante, alícuotas, documentos): se bajan "
                "al validar el punto de venta.", "/fiscal",
            ))

    sin_iva = panel.productos_sin_clasificar
    otros = panel.otros_impuestos_pendientes
    pendientes_otros = otros.get("SIN_CLASIFICAR", 0) + otros.get("REQUIERE_TRATAMIENTO", 0)
    if sin_iva or pendientes_otros:
        pasos.append(PasoPreparacion(
            "productos", "Información fiscal de los productos", PASO_ADVERTENCIA, RESPONSABLE_DUENO,
            f"{sin_iva} producto(s) sin IVA clasificado y {pendientes_otros} con otros impuestos pendientes: una venta "
            "que los incluya no se fiscaliza hasta clasificarlos.", "/fiscal/productos",
        ))
    else:
        pasos.append(PasoPreparacion(
            "productos", "Información fiscal de los productos", PASO_OK, RESPONSABLE_DUENO,
            "Todos los productos activos tienen IVA y otros impuestos clasificados.", "/fiscal/productos",
        ))

    pasos.append(PasoPreparacion(
        "conectividad", "Conectividad con ARCA", PASO_ADVERTENCIA, RESPONSABLE_DUENO,
        "No se comprueba sola al abrir este panel. Usá «Probar conexión» para verificarla ahora (requiere Internet y "
        "certificado).", "/fiscal",
    ))

    if ambiente == AMBIENTE_HOMOLOGACION:
        pasos.append(PasoPreparacion(
            "homologacion", "Homologación con ARCA", PASO_EXTERNO, RESPONSABLE_ARCA,
            "Twinx opera en el ambiente de pruebas: lo que emite acá NO tiene validez fiscal. La homologación real "
            "(pruebas contra ARCA con el certificado de homologación del emisor) no fue verificada.", None,
        ))
        pasos.append(PasoPreparacion(
            "produccion", "Autorización de producción", PASO_EXTERNO, RESPONSABLE_TECNICO,
            "Después de homologar, el técnico habilita producción desde la consola (KioscoApp.exe "
            "--habilitar-produccion-fiscal) con un certificado de producción vigente del emisor.", None,
        ))
    else:
        pasos.append(PasoPreparacion("produccion", "Autorización de producción", PASO_OK, RESPONSABLE_TECNICO,
                                     "Producción habilitada por el técnico.", None))

    inciertos = panel.conteos.get(estados.INCIERTO, 0) + panel.conteos.get(estados.ENVIANDO, 0)
    if inciertos:
        pasos.append(PasoPreparacion(
            "inciertos", "Comprobantes con resultado incierto", PASO_ADVERTENCIA, RESPONSABLE_DUENO,
            f"{inciertos} comprobante(s) sin resultado confirmado (error temporal de comunicación): conciliá antes de "
            "seguir facturando esa secuencia.", "/fiscal",
        ))

    listo = panel.listo
    if listo and ambiente == AMBIENTE_PRODUCCION:
        situacion = "Lista para emitir facturas electrónicas con validez fiscal."
    elif listo:
        situacion = (
            "Fiscaliza en HOMOLOGACIÓN (pruebas): los comprobantes emitidos NO tienen validez fiscal. Faltan la "
            "homologación externa y la habilitación de producción."
        )
    else:
        situacion = (
            "Todavía no puede emitir facturas electrónicas: faltan pasos de configuración. Mientras tanto, Twinx "
            "emite solo comprobantes internos (no válidos como factura)."
        )
    return Preparacion(
        pasos,
        situacion,
        puede_emitir_con_validez=listo and ambiente == AMBIENTE_PRODUCCION,
        solo_pruebas=listo and ambiente != AMBIENTE_PRODUCCION,
    )
