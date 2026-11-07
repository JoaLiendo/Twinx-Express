"""Twinx Asistente (V1.18): reglas puras del asistente conversacional de solo lectura.

Acá viven las decisiones que no dependen de la base ni de ningún motor de IA: qué herramientas de consulta
existen y qué rol puede usar cada una, cómo se reconoce una pregunta frecuente sin ningún modelo (la
"ayuda integrada"), qué período significa "hoy" o "este mes", y los límites de tamaño que protegen al
sistema. El modelo de lenguaje, cuando existe, nunca decide permisos: los decide `puede_usar`, que el
servicio consulta antes de exponer o ejecutar cualquier herramienta.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

from excepciones import DatosInvalidosError

# --- Límites ----------------------------------------------------------------------------------------

LIMITE_PREGUNTA = 500
LIMITE_HISTORIAL = 10
LIMITE_FILAS = 10
LIMITE_TEXTO_EXTERNO = 80
LIMITE_RESPUESTA = 2500

# --- Orígenes de una respuesta y modos del motor ----------------------------------------------------

ORIGEN_INTEGRADO = "integrado"
ORIGEN_IA_LOCAL = "ia_local"

MODO_INTEGRADO = "integrado"
MODO_OLLAMA = "ollama"
MODOS_MOTOR = (MODO_INTEGRADO, MODO_OLLAMA)

ROL_OWNER = "OWNER"
ROL_CASHIER = "CASHIER"
_AMBOS = frozenset({ROL_OWNER, ROL_CASHIER})
_SOLO_OWNER = frozenset({ROL_OWNER})

# --- Herramientas de consulta y permisos ------------------------------------------------------------

VENTAS_DEL_PERIODO = "ventas_del_periodo"
VENTAS_DE_LA_CAJA = "ventas_de_la_caja"
PRODUCTOS_MAS_VENDIDOS = "productos_mas_vendidos"
PRODUCTOS_POR_VENCER = "productos_por_vencer"
REPOSICION_SUGERIDA = "reposicion_sugerida"
MARGEN_NEGATIVO = "margen_negativo"
PROVEEDORES_CON_RECLAMOS = "proveedores_con_reclamos"
DEUDA_DE_CLIENTES = "deuda_de_clientes"
DEUDA_A_PROVEEDORES = "deuda_a_proveedores"
GUIA_DE_USO = "guia_de_uso"

# Quién puede usar cada herramienta. Deny-by-default: una herramienta que no figura acá no la usa nadie.
# El CASHIER no accede a costos, márgenes, compras, proveedores ni reportes del dueño, igual que en el menú.
PERMISOS: dict[str, frozenset[str]] = {
    VENTAS_DEL_PERIODO: _SOLO_OWNER,
    VENTAS_DE_LA_CAJA: _AMBOS,
    PRODUCTOS_MAS_VENDIDOS: _SOLO_OWNER,
    PRODUCTOS_POR_VENCER: _AMBOS,
    REPOSICION_SUGERIDA: _SOLO_OWNER,
    MARGEN_NEGATIVO: _SOLO_OWNER,
    PROVEEDORES_CON_RECLAMOS: _SOLO_OWNER,
    DEUDA_DE_CLIENTES: _AMBOS,
    DEUDA_A_PROVEEDORES: _SOLO_OWNER,
    GUIA_DE_USO: _AMBOS,
}


def puede_usar(herramienta: str, rol: str | None) -> bool:
    """Única regla de autorización del asistente: la decide el backend, nunca el modelo ni el navegador."""
    return rol is not None and rol in PERMISOS.get(herramienta, frozenset())


def herramientas_para(rol: str | None) -> list[str]:
    return [nombre for nombre in PERMISOS if puede_usar(nombre, rol)]


# --- Períodos ---------------------------------------------------------------------------------------

PERIODO_HOY = "hoy"
PERIODO_AYER = "ayer"
PERIODO_SEMANA = "semana"
PERIODO_MES = "mes"
PERIODO_MES_ANTERIOR = "mes_anterior"
PERIODO_30_DIAS = "ultimos_30_dias"
PERIODOS = (PERIODO_HOY, PERIODO_AYER, PERIODO_SEMANA, PERIODO_MES, PERIODO_MES_ANTERIOR, PERIODO_30_DIAS)


@dataclass(frozen=True)
class Periodo:
    clave: str
    desde: str
    hasta: str
    etiqueta: str


def resolver_periodo(clave: str | None, hoy: date) -> Periodo:
    """Traduce una clave de período a fechas `AAAA-MM-DD` inclusivas. Una clave desconocida es un error de
    datos (puede venir del modelo): nunca se adivina."""
    clave = clave or PERIODO_HOY
    if clave == PERIODO_HOY:
        return Periodo(clave, hoy.isoformat(), hoy.isoformat(), "hoy")
    if clave == PERIODO_AYER:
        ayer = hoy - timedelta(days=1)
        return Periodo(clave, ayer.isoformat(), ayer.isoformat(), "ayer")
    if clave == PERIODO_SEMANA:
        lunes = hoy - timedelta(days=hoy.weekday())
        return Periodo(clave, lunes.isoformat(), hoy.isoformat(), "esta semana")
    if clave == PERIODO_MES:
        return Periodo(clave, hoy.replace(day=1).isoformat(), hoy.isoformat(), "este mes")
    if clave == PERIODO_MES_ANTERIOR:
        fin = hoy.replace(day=1) - timedelta(days=1)
        return Periodo(clave, fin.replace(day=1).isoformat(), fin.isoformat(), "el mes pasado")
    if clave == PERIODO_30_DIAS:
        return Periodo(clave, (hoy - timedelta(days=29)).isoformat(), hoy.isoformat(), "los últimos 30 días")
    raise DatosInvalidosError(f"Período desconocido: {clave!r}.")


# --- Texto ------------------------------------------------------------------------------------------

_CONTROLES = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ESPACIOS = re.compile(r"\s+")


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes ni signos, espacios simples: la base para reconocer preguntas."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return _ESPACIOS.sub(" ", re.sub(r"[^a-z0-9ñ ]", " ", sin_tildes.lower())).strip()


def sanitizar_texto_externo(texto: object, limite: int = LIMITE_TEXTO_EXTERNO) -> str:
    """Nombres de productos, clientes o proveedores antes de entrar a un prompt o a una respuesta: sin
    caracteres de control ni saltos de línea (un nombre no puede "escribir" instrucciones en otra línea)
    y acotados. No se interpreta nada: es contenido, nunca una instrucción."""
    plano = _ESPACIOS.sub(" ", _CONTROLES.sub("", str(texto))).strip()
    return plano if len(plano) <= limite else plano[: limite - 1] + "…"


def validar_pregunta(texto: object) -> str:
    """La pregunta del usuario, limpia. Vacía o demasiado larga es un error de datos."""
    pregunta = sanitizar_texto_externo(texto, LIMITE_PREGUNTA + 1)
    if not pregunta:
        raise DatosInvalidosError("Escribí una pregunta.")
    if len(pregunta) > LIMITE_PREGUNTA:
        raise DatosInvalidosError(f"La pregunta no puede superar los {LIMITE_PREGUNTA} caracteres.")
    return pregunta


# --- Mensajes y respuestas --------------------------------------------------------------------------

ROL_MENSAJE_USUARIO = "usuario"
ROL_MENSAJE_ASISTENTE = "asistente"


@dataclass(frozen=True)
class MensajeChat:
    rol: str
    texto: str


def recortar_historial(mensajes: list[MensajeChat]) -> list[MensajeChat]:
    """Los últimos `LIMITE_HISTORIAL` mensajes bien formados, con el texto acotado. El historial lo manda
    el navegador: se trata como entrada no confiable."""
    validos = [
        MensajeChat(m.rol, sanitizar_texto_externo(m.texto, LIMITE_PREGUNTA))
        for m in mensajes
        if m.rol in (ROL_MENSAJE_USUARIO, ROL_MENSAJE_ASISTENTE) and sanitizar_texto_externo(m.texto, LIMITE_PREGUNTA)
    ]
    return validos[-LIMITE_HISTORIAL:]


@dataclass(frozen=True)
class Accion:
    """Un enlace a la pantalla relacionada. Siempre lo define el servidor: el modelo no fabrica URLs."""

    etiqueta: str
    href: str


@dataclass(frozen=True)
class EstadoMotor:
    modo: str
    disponible: bool
    modelo: str | None = None
    detalle: str = ""

    @property
    def etiqueta(self) -> str:
        if self.modo == MODO_OLLAMA and self.disponible:
            return f"IA local conectada · {self.modelo}"
        if self.modo == MODO_OLLAMA:
            return "IA local no disponible · ayuda integrada"
        return "Ayuda integrada"


@dataclass(frozen=True)
class RespuestaAsistente:
    texto: str
    origen: str
    acciones: tuple[Accion, ...] = ()
    herramientas: tuple[str, ...] = ()
    modelo: str | None = None
    advertencias: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResultadoHerramienta:
    """Lo que devuelve una herramienta de consulta: datos acotados para el modelo, el texto ya redactado
    para la ayuda integrada y los accesos a la pantalla de origen."""

    datos: dict
    texto: str
    acciones: tuple[Accion, ...] = ()


# --- Ayuda integrada: reconocimiento de preguntas frecuentes sin modelo -----------------------------


@dataclass(frozen=True)
class Intencion:
    herramienta: str
    argumentos: dict = field(default_factory=dict)


_PERIODOS_POR_PALABRA = (
    (("mes pasado", "mes anterior"), PERIODO_MES_ANTERIOR),
    (("ultimos 30 dias", "30 dias"), PERIODO_30_DIAS),
    (("este mes", "del mes", "en el mes", "mes"), PERIODO_MES),
    (("esta semana", "semana"), PERIODO_SEMANA),
    (("ayer",), PERIODO_AYER),
    (("hoy",), PERIODO_HOY),
)


def _periodo_mencionado(texto: str, por_defecto: str) -> str:
    for palabras, clave in _PERIODOS_POR_PALABRA:
        if any(_contiene(texto, palabra) for palabra in palabras):
            return clave
    return por_defecto


def _contiene(texto: str, palabra: str) -> bool:
    return re.search(rf"(^| ){re.escape(palabra)}($| )", texto) is not None


def _alguna(texto: str, *raices: str) -> bool:
    return any(raiz in texto for raiz in raices)


# Temas de la guía de uso con sus palabras clave (normalizadas). El primero que coincide gana.
TEMAS_GUIA: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("restauracion", ("restaur", "recuperar los datos", "volver atras", "rollback")),
    ("backup", ("backup", "copia de seguridad", "respaldo")),
    ("compras", ("compra", "ingres", "mercaderia", "proveedor")),
    ("vencimientos", ("venc",)),
    ("reposicion", ("repon", "reposicion")),
    ("inventario", ("inventario", "conteo", "contar")),
    ("caja", ("caja", "arqueo", "cierre", "abrir")),
    ("clientes", ("cliente", "cuenta corriente", "fiado", "credito", "cobro")),
    ("ventas", ("vend", "venta", "cobr", "ticket", "lector", "codigo de barras")),
    ("soporte", ("soporte", "diagnostico", "problema", "error", "ayuda")),
)


def detectar_intencion(pregunta: str, rol: str | None) -> Intencion | None:
    """Reconoce las preguntas que la ayuda integrada responde sin ningún modelo. Devuelve `None` cuando no
    reconoce la pregunta: el servicio decide entonces si hay un motor de IA que pueda intentarlo.

    El rol entra acá solo para elegir la variante de una misma pregunta ("¿cuánto vendí hoy?" es el
    período para el dueño y la sesión de caja para el cajero). La autorización final sigue siendo
    `puede_usar`, que el servicio verifica antes de ejecutar."""
    texto = normalizar(pregunta)
    if not texto:
        return None
    es_consulta_de_uso = _alguna(texto, "como ", "donde ", "que hago", "que pasos", "de que manera")
    if es_consulta_de_uso:
        for tema, raices in TEMAS_GUIA:
            if _alguna(texto, *raices):
                return Intencion(GUIA_DE_USO, {"tema": tema})
        return None
    if _alguna(texto, "margen", "margenes", "pierdo plata", "vendo por debajo del costo"):
        return Intencion(MARGEN_NEGATIVO, {"periodo": _periodo_mencionado(texto, PERIODO_MES)})
    if _alguna(texto, "reclamo"):
        return Intencion(PROVEEDORES_CON_RECLAMOS)
    if _alguna(texto, "mas vendid", "vendi mas", "vendo mas", "vende mas", "venden mas", "ranking", "top ", "mas se vend", "mas salen", "mas sale"):
        return Intencion(PRODUCTOS_MAS_VENDIDOS, {"periodo": _periodo_mencionado(texto, PERIODO_MES)})
    if _alguna(texto, "venc"):
        return Intencion(PRODUCTOS_POR_VENCER)
    if _alguna(texto, "repon", "reposicion", "reponer", "falta stock", "stock bajo", "stock critico", "comprar"):
        return Intencion(REPOSICION_SUGERIDA)
    if _alguna(texto, "deb", "deuda", "cuenta corriente", "fiado", "a cuenta", "por cobrar", "por pagar"):
        if _alguna(texto, "proveedor", "por pagar", "les debo", "debo a"):
            return Intencion(DEUDA_A_PROVEEDORES)
        return Intencion(DEUDA_DE_CLIENTES)
    if _alguna(texto, "vend", "factur", "cuanto hice", "cuanto entro", "ventas", "recaud"):
        if rol == ROL_CASHIER:
            return Intencion(VENTAS_DE_LA_CAJA)
        return Intencion(VENTAS_DEL_PERIODO, {"periodo": _periodo_mencionado(texto, PERIODO_HOY)})
    return None


# --- Preguntas sugeridas según la pantalla ----------------------------------------------------------

_SUGERIDAS_GENERALES = ("¿Cuánto vendí hoy?", "¿Cuáles están por vencer?", "¿Cuánto me deben los clientes?")
_SUGERIDAS_POR_PANTALLA: tuple[tuple[str, tuple[tuple[str, frozenset[str]], ...]], ...] = (
    ("/ventas", (("¿Cómo cobro una venta a cuenta?", _AMBOS), ("¿Qué productos vendí más este mes?", _SOLO_OWNER))),
    ("/caja", (("¿Cuánto vendí hoy?", _AMBOS), ("¿Cómo cierro la caja?", _AMBOS))),
    ("/productos", (("¿Cuáles están por vencer?", _AMBOS), ("¿Qué tengo que reponer?", _SOLO_OWNER))),
    ("/inventario", (("¿Cómo hago un inventario?", _AMBOS), ("¿Cuáles están por vencer?", _AMBOS))),
    ("/compras", (("¿Cómo registro una compra?", _AMBOS), ("¿Qué tengo que reponer?", _SOLO_OWNER))),
    ("/reposicion", (("¿Qué tengo que reponer?", _SOLO_OWNER), ("¿Cómo registro una compra?", _AMBOS))),
    ("/proveedores", (("¿Qué proveedores tienen reclamos abiertos?", _SOLO_OWNER), ("¿Cuánto debo a proveedores?", _SOLO_OWNER))),
    ("/clientes", (("¿Cuánto me deben los clientes?", _AMBOS), ("¿Cómo funciona el crédito de un cliente?", _AMBOS))),
    ("/analitica", (("¿Qué productos tienen margen negativo?", _SOLO_OWNER), ("¿Qué productos vendí más este mes?", _SOLO_OWNER))),
    ("/reportes", (("¿Qué productos vendí más este mes?", _SOLO_OWNER), ("¿Qué productos tienen margen negativo?", _SOLO_OWNER))),
    ("/configuracion", (("¿Cómo restauro un backup?", _SOLO_OWNER), ("¿Cómo hago un backup?", _SOLO_OWNER))),
    ("/ayuda", (("¿Cómo registro una compra?", _AMBOS), ("¿Cómo restauro un backup?", _AMBOS))),
)
_SUGERIDAS_OWNER_EXTRA = ("¿Qué tengo que reponer?", "¿Qué productos tienen margen negativo?")


def preguntas_sugeridas(pantalla: str | None, rol: str | None) -> list[str]:
    """Tres o cuatro preguntas para la pantalla actual, nunca una que el rol no pueda preguntar."""
    ruta = (pantalla or "/").split("?")[0]
    sugeridas: list[str] = []
    for prefijo, preguntas in _SUGERIDAS_POR_PANTALLA:
        if ruta == prefijo or ruta.startswith(prefijo + "/"):
            sugeridas.extend(p for p, roles in preguntas if rol in roles)
            break
    for pregunta in _SUGERIDAS_GENERALES + (_SUGERIDAS_OWNER_EXTRA if rol == ROL_OWNER else ()):
        if pregunta not in sugeridas:
            sugeridas.append(pregunta)
    return sugeridas[:4]
