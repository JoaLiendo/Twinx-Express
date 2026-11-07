"""Twinx Asistente (V1.18): API JSON del panel de chat. Ambos roles; qué puede consultar cada uno lo decide
`services.asistente` (permisos por herramienta), nunca el navegador ni el modelo."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from domain.asistente import LIMITE_HISTORIAL, LIMITE_PREGUNTA, MensajeChat
from domain.usuario import Usuario
from interfaces.web.auth import obtener_usuario_actual, requiere_rol
from services.asistente import motor

router = APIRouter(prefix="/api/asistente", dependencies=[Depends(requiere_rol("OWNER", "CASHIER"))])


class MensajeEntrada(BaseModel):
    rol: str = Field(max_length=20)
    texto: str = Field(max_length=LIMITE_PREGUNTA * 4)


class PreguntaEntrada(BaseModel):
    pregunta: str = Field(max_length=LIMITE_PREGUNTA * 2)
    historial: list[MensajeEntrada] = Field(default_factory=list, max_length=LIMITE_HISTORIAL * 2)
    pantalla: str | None = Field(default=None, max_length=200)


def _estado_a_json(estado: motor.EstadoAsistente) -> dict:
    return {
        "motor": {
            "modo": estado.motor.modo,
            "disponible": estado.motor.disponible,
            "modelo": estado.motor.modelo,
            "detalle": estado.motor.detalle,
            "etiqueta": estado.motor.etiqueta,
        },
        "sugeridas": estado.sugeridas,
    }


@router.get("/estado")
def estado(pantalla: str | None = Query(default=None, max_length=200), usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    return _estado_a_json(motor.estado_del_asistente(usuario_actual, pantalla))


@router.post("/preguntar")
def preguntar(datos: PreguntaEntrada, usuario_actual: Usuario = Depends(obtener_usuario_actual)):
    historial = [MensajeChat(m.rol, m.texto) for m in datos.historial]
    respuesta = motor.responder(datos.pregunta, historial, usuario_actual, datos.pantalla)
    return {
        "texto": respuesta.texto,
        "origen": respuesta.origen,
        "modelo": respuesta.modelo,
        "acciones": [{"etiqueta": a.etiqueta, "href": a.href} for a in respuesta.acciones],
        "herramientas": list(respuesta.herramientas),
        "advertencias": list(respuesta.advertencias),
    }
