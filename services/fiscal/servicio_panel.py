"""Estado fiscal para el panel del OWNER, el aviso persistente y el POS. Solo lecturas locales: nunca
llama a ARCA (la conectividad se prueba con una acción explícita)."""

from dataclasses import dataclass, field

from db.repositorios import fiscal as repositorio_fiscal
from domain.fiscal import comprobante as estados
from domain.fiscal.comprobante import AMBIENTE_HOMOLOGACION, AMBIENTE_PRODUCCION, ComprobanteFiscal
from domain.fiscal.emisor import PerfilFiscal
from services.fiscal import credenciales, servicio_parametros

_CERTIFICADO_USABLE = (credenciales.VIGENTE, credenciales.POR_VENCER)


@dataclass(frozen=True)
class EstadoPanel:
    perfil: PerfilFiscal | None
    certificados: dict[str, credenciales.InfoCertificado]
    conteos: dict[str, int]
    inciertos: list[ComprobanteFiscal]
    pendientes: list[ComprobanteFiscal]
    rechazados: list[ComprobanteFiscal]
    autorizados_recientes: list[ComprobanteFiscal]
    errores_recientes: list
    productos_sin_clasificar: int
    tablas: list
    avisos: list[str] = field(default_factory=list)

    @property
    def listo(self) -> bool:
        return fiscalizacion_activa_para(self.perfil, self.certificados)


def fiscalizacion_activa_para(perfil: PerfilFiscal | None, certificados: dict | None = None) -> bool:
    if perfil is None or not perfil.punto_venta_cae_listo:
        return False
    certificado = (certificados or {}).get(perfil.ambiente) or credenciales.estado(perfil.ambiente)
    return certificado.estado in _CERTIFICADO_USABLE


def fiscalizacion_activa() -> bool:
    """El POS fiscaliza automáticamente solo si el emisor está configurado, validado y con certificado."""
    return fiscalizacion_activa_para(repositorio_fiscal.obtener_perfil())


def avisos(perfil: PerfilFiscal | None, certificados: dict[str, credenciales.InfoCertificado], conteos: dict[str, int]) -> list[str]:
    resultado = []
    if conteos.get(estados.INCIERTO):
        resultado.append(f"Hay {conteos[estados.INCIERTO]} comprobante(s) con resultado incierto: conciliá antes de seguir facturando esa secuencia.")
    if conteos.get(estados.ENVIANDO):
        resultado.append(f"Hay {conteos[estados.ENVIANDO]} comprobante(s) enviándose a ARCA.")
    if perfil is not None:
        certificado = certificados[perfil.ambiente]
        if certificado.estado == credenciales.VENCIDO:
            resultado.append("El certificado fiscal está vencido: no se puede facturar hasta renovarlo.")
        elif certificado.estado == credenciales.POR_VENCER:
            resultado.append(f"El certificado fiscal vence en {certificado.dias_restantes} día(s): renovalo en ARCA.")
        elif certificado.estado in (credenciales.AUSENTE, credenciales.ILEGIBLE):
            resultado.append("No hay un certificado fiscal utilizable para el ambiente en uso.")
    return resultado


def estado() -> EstadoPanel:
    perfil = repositorio_fiscal.obtener_perfil()
    certificados = {ambiente: credenciales.estado(ambiente) for ambiente in (AMBIENTE_HOMOLOGACION, AMBIENTE_PRODUCCION)}
    conteos = repositorio_fiscal.contar_por_estado()
    return EstadoPanel(
        perfil=perfil,
        certificados=certificados,
        conteos=conteos,
        inciertos=repositorio_fiscal.listar_por_estados((estados.INCIERTO, estados.ENVIANDO)),
        pendientes=repositorio_fiscal.listar_por_estados((estados.PENDIENTE,), 20),
        rechazados=repositorio_fiscal.listar_por_estados((estados.RECHAZADO,), 10),
        autorizados_recientes=repositorio_fiscal.listar_por_estados(tuple(estados.ESTADOS_AUTORIZADOS), 10),
        errores_recientes=repositorio_fiscal.listar_errores_recientes(10),
        productos_sin_clasificar=repositorio_fiscal.contar_productos_activos_sin_clasificar(),
        tablas=servicio_parametros.tablas(perfil.ambiente) if perfil else [],
        avisos=avisos(perfil, certificados, conteos),
    )


def alerta_fiscal() -> list[str]:
    """Avisos críticos para mostrar al OWNER fuera del panel (sin datos sensibles)."""
    perfil = repositorio_fiscal.obtener_perfil()
    if perfil is None:
        return []
    return avisos(perfil, {perfil.ambiente: credenciales.estado(perfil.ambiente)}, repositorio_fiscal.contar_por_estado())


def estado_fiscal_de_ventas(venta_ids: list[int]) -> dict[int, str]:
    """Etiqueta fiscal por venta para el historial ("Venta registrada" si no tiene comprobante vivo)."""
    por_venta = repositorio_fiscal.estado_fiscal_de_ventas(venta_ids)
    return {venta_id: estados.ETIQUETAS_ESTADO.get(por_venta[venta_id], por_venta[venta_id]) for venta_id in por_venta}
