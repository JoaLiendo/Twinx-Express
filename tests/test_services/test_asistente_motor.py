"""Orquestación de Twinx Asistente (V1.18) con el proveedor falso: ayuda integrada primero, IA local con
herramientas filtradas por rol, degradación cuando el motor falla y estado del motor."""

from datetime import date

import pytest

from domain import asistente as reglas
from domain.asistente import MensajeChat
from domain.venta import ItemVenta
from services import servicio_caja, servicio_stock, servicio_ventas
from services.asistente import configuracion as configuracion_asistente
from services.asistente import motor
from services.asistente.proveedores import LlamadaHerramienta, ProveedorFalso, RespuestaModelo
from tests.utilidades_clientes import crear_usuario

pytestmark = pytest.mark.sin_caja_abierta

HOY = date.today()


@pytest.fixture
def e(base_datos_temporal):
    owner = crear_usuario("duenio", "OWNER")
    cajero = crear_usuario("cajero", "CASHIER")
    servicio_caja.abrir_caja(0, None, owner.id)
    gaseosa = servicio_stock.registrar_producto("1", "Gaseosa Cola", 100, 250, stock_actual=10, stock_minimo=0)
    servicio_ventas.registrar_venta([ItemVenta(gaseosa.id, 2)], "EFECTIVO", usuario_id=owner.id)
    motor._detector.olvidar()
    return type("E", (), {"owner": owner, "cajero": cajero})


def _llamada(nombre: str, **argumentos) -> RespuestaModelo:
    return RespuestaModelo("", (LlamadaHerramienta(nombre, argumentos),))


class TestAyudaIntegrada:
    def test_una_pregunta_reconocida_no_consulta_al_modelo(self, e):
        falso = ProveedorFalso([RespuestaModelo("no debería llegar")])
        respuesta = motor.responder("¿Cuánto vendí hoy?", [], e.owner, "/", proveedor=falso, hoy=HOY)
        assert respuesta.origen == reglas.ORIGEN_INTEGRADO and respuesta.modelo is None
        assert "1 venta por $ 5,00" in respuesta.texto and respuesta.herramientas == (reglas.VENTAS_DEL_PERIODO,)
        assert falso.recibidos == []

    def test_el_cajero_que_pide_margenes_recibe_la_negativa_sin_cifras(self, e):
        respuesta = motor.responder("¿Qué productos tienen margen negativo?", [], e.cajero, "/", hoy=HOY)
        assert respuesta.texto == "Esa información es solo para el dueño." and "$" not in respuesta.texto
        assert respuesta.origen == reglas.ORIGEN_INTEGRADO

    def test_sin_motor_y_sin_reconocer_lista_lo_que_puede_responder(self, e):
        respuesta = motor.responder("¿Qué opinás del clima?", [], e.owner, "/clientes", hoy=HOY)
        assert respuesta.texto.startswith("No reconocí la pregunta") and "¿Cuánto me deben los clientes?" in respuesta.texto
        assert respuesta.advertencias == () and respuesta.acciones == (reglas.Accion("Ayuda", "/ayuda"),)

    def test_pregunta_vacia_es_error_de_datos(self, e):
        from excepciones import DatosInvalidosError

        with pytest.raises(DatosInvalidosError):
            motor.responder("   ", [], e.owner, "/", hoy=HOY)


class TestIALocal:
    def test_el_modelo_pide_una_herramienta_y_responde_con_sus_datos(self, e):
        falso = ProveedorFalso([_llamada(reglas.VENTAS_DEL_PERIODO, periodo="hoy"), RespuestaModelo("Hoy vendiste $ 5,00 en 1 venta.\n- Efectivo: $ 5,00")])
        historial = [MensajeChat("usuario", "hola"), MensajeChat("asistente", "¿En qué te ayudo?")]
        respuesta = motor.responder("dame un resumen rápido de la jornada", historial, e.owner, "/", proveedor=falso, hoy=HOY)

        assert respuesta.origen == reglas.ORIGEN_IA_LOCAL and respuesta.modelo == "falso:test"
        assert respuesta.texto == "Hoy vendiste $ 5,00 en 1 venta.\n- Efectivo: $ 5,00"
        assert respuesta.herramientas == (reglas.VENTAS_DEL_PERIODO,)
        assert respuesta.acciones[0].href.startswith("/reportes?fecha_desde=")
        # Primer turno: sistema + historial + pregunta, con TODAS las herramientas del dueño.
        mensajes, herramientas = falso.recibidos[0]
        assert [m.rol for m in mensajes] == ["system", "user", "assistant", "user"]
        assert "Nunca inventes" in mensajes[0].contenido and f"Hoy es {HOY.isoformat()}" in mensajes[0].contenido
        assert {h["function"]["name"] for h in herramientas} == set(reglas.PERMISOS)
        # Segundo turno: la llamada del modelo y el resultado como `tool` con los datos reales.
        mensajes, _ = falso.recibidos[1]
        assert mensajes[-2].rol == "assistant" and mensajes[-2].llamadas[0].nombre == reglas.VENTAS_DEL_PERIODO
        assert mensajes[-1].rol == "tool" and '"total_centavos": 500' in mensajes[-1].contenido

    def test_el_cajero_no_recibe_herramientas_del_dueno_y_si_el_modelo_insiste_se_le_niega(self, e):
        falso = ProveedorFalso([_llamada(reglas.MARGEN_NEGATIVO, periodo="mes"), RespuestaModelo("No tengo acceso a los márgenes.")])
        respuesta = motor.responder("análisis de rentabilidad de noviembre", [], e.cajero, "/", proveedor=falso, hoy=HOY)

        _, herramientas = falso.recibidos[0]
        assert {h["function"]["name"] for h in herramientas} == set(reglas.herramientas_para("CASHIER"))
        mensajes, _ = falso.recibidos[1]
        assert mensajes[-1].rol == "tool" and "no disponible" in mensajes[-1].contenido
        assert respuesta.texto == "No tengo acceso a los márgenes." and respuesta.herramientas == ()
        assert any("no permitida" in a for a in respuesta.advertencias)

    def test_argumentos_invalidos_del_modelo_vuelven_como_error_sin_ejecutar(self, e):
        falso = ProveedorFalso([_llamada(reglas.VENTAS_DEL_PERIODO, periodo="siempre"), RespuestaModelo("No pude consultar ese período.")])
        respuesta = motor.responder("dame el resumen de la jornada de siempre", [], e.owner, "/", proveedor=falso, hoy=HOY)
        mensajes, _ = falso.recibidos[1]
        assert '"error"' in mensajes[-1].contenido and respuesta.herramientas == ()

    def test_un_modelo_que_no_para_de_pedir_herramientas_se_corta_y_se_degrada(self, e):
        falso = ProveedorFalso([_llamada(reglas.PRODUCTOS_POR_VENCER)] * 10)
        respuesta = motor.responder("algo raro", [], e.owner, "/", proveedor=falso, hoy=HOY)
        assert respuesta.origen == reglas.ORIGEN_INTEGRADO and respuesta.texto.startswith("No reconocí la pregunta")
        assert any("demasiadas consultas" in a for a in respuesta.advertencias)
        assert len(falso.recibidos) == motor.MAX_RONDAS_DE_HERRAMIENTAS + 1

    def test_si_el_motor_falla_se_informa_y_no_se_inventa(self, e):
        falso = ProveedorFalso(fallar_con="El motor de IA tardó demasiado en responder.")
        respuesta = motor.responder("algo libre", [], e.owner, "/", proveedor=falso, hoy=HOY)
        assert respuesta.origen == reglas.ORIGEN_INTEGRADO
        assert respuesta.advertencias == ("La IA local no pudo responder: El motor de IA tardó demasiado en responder.",)

    def test_una_sola_conversacion_con_la_ia_por_vez(self, e):
        """Auditoría H6: varias preguntas libres en paralelo ocuparían hilos del servidor hasta 4 × timeout."""
        falso = ProveedorFalso([RespuestaModelo("Respondido.")])
        assert motor._turno_de_ia.acquire(blocking=False)
        try:
            ocupado = motor.responder("algo libre", [], e.owner, "/", proveedor=falso, hoy=HOY)
        finally:
            motor._turno_de_ia.release()
        assert ocupado.origen == reglas.ORIGEN_INTEGRADO and any("ocupada" in a for a in ocupado.advertencias)
        assert falso.recibidos == []
        libre = motor.responder("algo libre", [], e.owner, "/", proveedor=falso, hoy=HOY)
        assert libre.texto == "Respondido." and motor._turno_de_ia.acquire(blocking=False)
        motor._turno_de_ia.release()

    def test_argumentos_crudos_del_modelo_se_rechazan_sin_ejecutar(self, e):
        falso = ProveedorFalso([RespuestaModelo("", (LlamadaHerramienta(reglas.VENTAS_DEL_PERIODO, "no-json"),)), RespuestaModelo("No pude.")])
        respuesta = motor.responder("dame el resumen de la jornada", [], e.owner, "/", proveedor=falso, hoy=HOY)
        mensajes, _ = falso.recibidos[1]
        assert '"error"' in mensajes[-1].contenido and respuesta.herramientas == ()

    def test_motor_apagado_lo_dice_y_la_ayuda_integrada_sigue(self, e):
        falso = ProveedorFalso(disponible=False)
        libre = motor.responder("algo libre", [], e.owner, "/", proveedor=falso, hoy=HOY)
        assert libre.advertencias == ("IA local no disponible: Motor de prueba apagado.",)
        reconocida = motor.responder("¿Cuáles están por vencer?", [], e.owner, "/", proveedor=falso, hoy=HOY)
        assert reconocida.origen == reglas.ORIGEN_INTEGRADO and reconocida.advertencias == ()

    def test_la_respuesta_del_modelo_se_acota_y_se_limpia(self, e):
        falso = ProveedorFalso([RespuestaModelo("línea\x00 uno\n\n" + "x" * 5000)])
        respuesta = motor.responder("texto largo", [], e.owner, "/", proveedor=falso, hoy=HOY)
        assert respuesta.texto.startswith("línea uno\n") and len(respuesta.texto) <= reglas.LIMITE_RESPUESTA
        vacio = ProveedorFalso([RespuestaModelo("")])
        assert "no devolvió una respuesta" in motor.responder("texto", [], e.owner, "/", proveedor=vacio, hoy=HOY).texto


class TestEstado:
    def test_estado_por_defecto_y_sugeridas(self, e):
        estado = motor.estado_del_asistente(e.cajero, "/ventas")
        assert estado.motor.modo == reglas.MODO_INTEGRADO and estado.motor.etiqueta == "Ayuda integrada"
        assert "¿Cómo cobro una venta a cuenta?" in estado.sugeridas

    def test_el_estado_del_motor_se_recuerda_treinta_segundos(self, e):
        configuracion_asistente.guardar(configuracion_asistente.validar("ollama", "http://127.0.0.1:9", "llama3.2:3b", 5, 1024), e.owner.id)
        llamadas = []

        class Contador(ProveedorFalso):
            def estado(self):
                llamadas.append(1)
                return super().estado()

        proveedor = Contador(disponible=False)
        motor.estado_del_asistente(e.owner, "/", proveedor=proveedor)
        motor.estado_del_asistente(e.owner, "/", proveedor=proveedor)
        assert len(llamadas) == 1
        motor._detector.olvidar()
        motor.estado_del_asistente(e.owner, "/", proveedor=proveedor)
        assert len(llamadas) == 2

    def test_probar_motor_no_guarda_y_deja_su_resultado_en_la_cache(self, e):
        configuracion = configuracion_asistente.validar("ollama", "http://127.0.0.1:9", "llama3.2:3b", 5, 1024)
        estado = motor.probar_motor(configuracion)
        assert estado.disponible is False and configuracion_asistente.obtener().motor == "integrado"
        # El panel ve lo mismo que vio el botón, sin volver a sondear.
        assert motor._detector.estado(configuracion, ProveedorFalso(disponible=True)) is estado

    def test_un_solo_sondeo_a_la_vez_y_los_demas_reciben_el_ultimo_estado(self, e):
        import threading

        configuracion = configuracion_asistente.validar("ollama", "http://127.0.0.1:9", "llama3.2:3b", 5, 1024)
        empezo, seguir = threading.Event(), threading.Event()

        class Lento(ProveedorFalso):
            def estado(self):
                empezo.set()
                seguir.wait(5)
                return super().estado()

        lento = Lento(disponible=False)
        detector = motor.DetectorDeMotor()
        hilo = threading.Thread(target=lambda: detector.estado(configuracion, lento), daemon=True)
        hilo.start()
        assert empezo.wait(5)
        mientras = detector.estado(configuracion, lento)  # no espera al sondeo en curso
        assert mientras.disponible is False and "Comprobando" in mientras.detalle
        seguir.set()
        hilo.join(5)
        assert detector.estado(configuracion, lento).detalle == "Motor de prueba apagado."


class TestConfiguracion:
    def test_valores_corruptos_en_la_base_vuelven_al_modo_integrado(self, e):
        from db.conexion import obtener_conexion
        from db.repositorios import configuracion as repositorio_configuracion

        with obtener_conexion(inmediata=True) as conexion:
            repositorio_configuracion.guardar_en_conexion(conexion, configuracion_asistente.CLAVE_MOTOR, "ollama")
            repositorio_configuracion.guardar_en_conexion(conexion, configuracion_asistente.CLAVE_URL, "http://evil.example")
        assert configuracion_asistente.obtener().motor == "integrado"

    def test_guardar_audita_y_valida_limites(self, e):
        from excepciones import MotorIAError
        from services import servicio_auditoria

        with pytest.raises(MotorIAError):
            configuracion_asistente.validar("ollama", "http://127.0.0.1:11434", "llama3.2:3b", 2, 4096)
        with pytest.raises(MotorIAError):
            configuracion_asistente.validar("ollama", "http://127.0.0.1:11434", "llama3.2:3b", 30, 999)
        with pytest.raises(MotorIAError):
            configuracion_asistente.validar("nube", "http://127.0.0.1:11434", "llama3.2:3b", 30, 4096)
        guardada = configuracion_asistente.guardar(configuracion_asistente.validar("ollama", "http://127.0.0.1:11434", "llama3.2:3b", 30, 4096), e.owner.id)
        assert guardada.usa_ia and configuracion_asistente.obtener() == guardada
        eventos = servicio_auditoria.listar(accion="CONFIGURACION_ASISTENTE_CAMBIADA")
        assert len(eventos) == 1 and "llama3.2:3b" in eventos[0].resumen
