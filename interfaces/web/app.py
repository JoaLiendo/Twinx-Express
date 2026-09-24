"""Aplicación FastAPI de la interfaz web local (Fase 5).

Cliente delgado: las rutas (`interfaces/web/rutas/`) solo llaman a
`services/` y renderizan plantillas. Cero lógica de negocio y cero SQL
en esta capa (ver CLAUDE.md).

Para levantarla en http://127.0.0.1:8000:

    python -m interfaces.web.app

Para desarrollo con recarga automática ante cambios de código:

    uvicorn interfaces.web.app:app --reload
"""

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from config import DIRECTORIO_BACKUPS, DIRECTORIO_IMAGENES_PRODUCTOS, SEMBRAR_CATALOGO_INICIAL
from excepciones import ErrorAplicacion, NoAutenticadoError, PermisoDenegadoError
from interfaces.web.plantillas import templates
from interfaces.web.rutas import (
    autenticacion,
    backup,
    caja,
    compras,
    configuracion_inicial,
    dashboard,
    empleados,
    pedidos,
    precios,
    productos,
    proveedores,
    reportes,
    ventas,
)
from interfaces.web.utilidades import contexto_base, redireccionar_con_mensaje
from services import servicio_backup, servicio_catalogo_inicial
from services.control_escrituras import control_escrituras

logger = logging.getLogger(__name__)

_METODOS_DE_LECTURA = {"GET", "HEAD", "OPTIONS"}

DIRECTORIO_WEB = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Migraciones con backup preventivo (solo si la base ya existía y tenía
    migraciones pendientes: si el backup falla, no se migra), catálogo inicial
    (solo en una instalación nueva del ejecutable) y luego backup
    automático V1 (ver auditoría de distribución/backup): el chequeo corre
    en un hilo daemon de un solo uso, disparado y olvidado --
    nunca se espera (`.join()`) desde acá, así un backup lento (o que
    directamente falla) no demora el arranque del servidor ni la apertura
    del navegador. `servicio_backup.ejecutar_backup_automatico_si_corresponde`
    ya no propaga ninguna excepción, pero igual corre en su propio hilo:
    aunque lo hiciera, un hilo separado no puede tumbar el arranque.

    `ControlEscrituras` es un singleton por proceso: no coordina entre
    procesos del sistema operativo distintos (ej. el proceso worker que
    `uvicorn --reload` reinicia en desarrollo), solo entre requests dentro
    del mismo proceso -- suficiente para el ejecutable single-process real
    de Twinx Express, que es el único caso que este bloque necesita cubrir.
    """
    try:
        servicio_backup.migrar_base_datos_con_backup_preventivo(DIRECTORIO_BACKUPS, control_escrituras)
        if SEMBRAR_CATALOGO_INICIAL:
            # Si falla, el error se propaga: el arranque se aborta y el operador ve el
            # error en la consola (`lanzador.py`), en vez de seguir con un catálogo a medias.
            servicio_catalogo_inicial.sembrar_si_corresponde()
    except Exception as error:
        # Uvicorn convierte cualquier fallo de lifespan en `SystemExit`, igual
        # que un puerto ocupado: se deja la causa real en `app.state` para que
        # el lanzador pueda distinguirlos (ver `lanzador.py`).
        logger.critical("No se pudo iniciar la aplicación: %s", error, exc_info=True)
        app.state.error_de_inicio = error
        raise
    threading.Thread(
        target=servicio_backup.ejecutar_backup_automatico_si_corresponde,
        args=(DIRECTORIO_BACKUPS, control_escrituras),
        daemon=True,
    ).start()
    yield


app = FastAPI(title="Kiosco - Panel de Control", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(DIRECTORIO_WEB / "static")), name="static")
# Imágenes de producto (Fase 3D): se monta ÚNICAMENTE el subdirectorio de
# imágenes, nunca `data/` completo -- si se montara `data/` entero,
# `kiosco.db` quedaría descargable por HTTP (ver auditoría de Fase 3D).
app.mount(
    "/media/productos",
    StaticFiles(directory=str(DIRECTORIO_IMAGENES_PRODUCTOS)),
    name="media_productos",
)

@app.middleware("http")
async def bloquear_escrituras_durante_backup(request: Request, call_next):
    """Único punto central que coordina con `ControlEscrituras` (ver
    auditoría de distribución/backup): deja pasar toda lectura sin
    tocar nada, y para cualquier escritura de negocio, rechaza
    limpiamente con 503 si hay un backup activo o registra/libera su
    paso por el contador (siempre, incluso si el handler real lanza
    una excepción). No requiere tocar ningún router de negocio.

    El request que dispara el backup (`backup.RUTA_CREAR_BACKUP`) se
    excluye deliberadamente de este conteo: es quien orquesta el
    propio bloqueo (`ControlEscrituras.iniciar_backup`), y contarlo
    como una escritura de negocio más generaría una espera circular
    sobre sí mismo (deadlock ya corregido -- ver auditoría). El
    rechazo de un segundo backup concurrente lo resuelve
    `iniciar_backup` de forma atómica, no este middleware.
    """
    if request.method in _METODOS_DE_LECTURA or request.url.path == backup.RUTA_CREAR_BACKUP:
        return await call_next(request)

    if not control_escrituras.permitir_escritura_de_negocio():
        return JSONResponse(
            status_code=503,
            content={"error": "Hay un backup en curso. Probá de nuevo en unos segundos."},
        )
    try:
        return await call_next(request)
    finally:
        control_escrituras.finalizar_escritura_de_negocio()


app.include_router(autenticacion.router)
app.include_router(backup.router)
app.include_router(configuracion_inicial.router)
app.include_router(dashboard.router)
app.include_router(productos.router)
app.include_router(ventas.router)
app.include_router(caja.router)
app.include_router(pedidos.router)
app.include_router(precios.router)
app.include_router(proveedores.router)
app.include_router(compras.router)
app.include_router(reportes.router)
app.include_router(empleados.router)
app.include_router(empleados.router_cuenta)


@app.exception_handler(NoAutenticadoError)
async def manejar_no_autenticado(request: Request, exc: NoAutenticadoError) -> Response:
    """Sin sesión válida en una ruta protegida: a `/login`, no un error.

    Se registra específicamente para `NoAutenticadoError` (más
    específico que `ErrorAplicacion`, por eso Starlette lo prioriza)
    en vez de dejar que caiga en el manejador genérico de abajo, que
    haría un redirect al `referer` con un toast — acá no hay ninguna
    página anterior "de origen" con sentido, el destino correcto es
    siempre `/login`, preservando a dónde se quería ir.
    """
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"error": "Se requiere haber iniciado sesión."})
    return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)


@app.exception_handler(PermisoDenegadoError)
async def manejar_permiso_denegado(request: Request, exc: PermisoDenegadoError) -> Response:
    """Hay sesión válida pero el rol no alcanza: 403, nunca redirect a
    `/login` (el usuario ya está autenticado; mandarlo al login sería
    confuso y no resuelve nada)."""
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=403, content={"error": "No tiene permiso para esta acción."})
    return templates.TemplateResponse(request, "error_403.html", contexto_base(request), status_code=403)


@app.exception_handler(ErrorAplicacion)
async def manejar_error_de_aplicacion(request: Request, exc: ErrorAplicacion) -> Response:
    """Traduce cualquier excepción de negocio (ver excepciones.py) a una
    respuesta clara: JSON para la API del POS, toast de error + redirect
    de vuelta a la página de origen para el resto. Único lugar de la
    capa web que atrapa estas excepciones.
    """
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=422, content={"error": str(exc)})
    referer = request.headers.get("referer", "/")
    return redireccionar_con_mensaje(referer, "error", str(exc))


@app.exception_handler(RequestValidationError)
async def manejar_error_de_validacion(request: Request, exc: RequestValidationError) -> Response:
    """Datos mal formados en la API JSON del POS -> mensaje claro, no un 500."""
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=422, content={"error": "Los datos enviados no son válidos."})
    return await request_validation_exception_handler(request, exc)


if __name__ == "__main__":
    uvicorn.run("interfaces.web.app:app", host="127.0.0.1", port=8000, reload=True)
