"""V1.13-B: evaluaciones de proveedores (historial inmutable, calificación actual derivada, permisos)."""

import threading

import pytest

from domain.compra import ItemCompra
from domain.evaluacion_proveedor import requiere_advertencia
from excepciones import (
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    PermisoDenegadoError,
    ProveedorNoEncontradoError,
)
from services import servicio_compras, servicio_evaluaciones_proveedor, servicio_proveedores
from tests.utilidades_clientes import consultar, crear_owner, crear_producto, crear_usuario, escribir

evaluar = servicio_evaluaciones_proveedor.registrar_evaluacion


@pytest.fixture
def e(base_datos_temporal):
    return type(
        "E", (), {
            "ruta": base_datos_temporal,
            "owner": crear_owner(),
            "prov": servicio_proveedores.crear_proveedor("Norte"),
        },
    )


@pytest.mark.parametrize("estrellas", [1, 5])
def test_extremos_validos(e, estrellas):
    evaluacion = evaluar(e.prov.id, e.owner.id, estrellas, "ok")
    assert evaluacion.estrellas == estrellas and evaluacion.usuario_id == e.owner.id and evaluacion.fecha


@pytest.mark.parametrize("estrellas", [0, 6, -1, True, 2.5, "3"])
def test_estrellas_fuera_de_rango_o_de_tipo_se_rechazan(e, estrellas):
    with pytest.raises(DatosInvalidosError):
        evaluar(e.prov.id, e.owner.id, estrellas, "ok")
    assert consultar(e.ruta, "SELECT COUNT(*) FROM evaluaciones_proveedor")[0][0] == 0


@pytest.mark.parametrize("comentario", [None, "", "   \n "])
def test_comentario_obligatorio(e, comentario):
    with pytest.raises(DatosInvalidosError, match="obligatorio"):
        evaluar(e.prov.id, e.owner.id, 4, comentario)


def test_comentario_demasiado_largo(e):
    with pytest.raises(DatosInvalidosError):
        evaluar(e.prov.id, e.owner.id, 4, "x" * 501)


def test_criterios_opcionales_parciales_y_la_general_no_se_promedia(e):
    evaluacion = evaluar(
        e.prov.id, e.owner.id, 2, "  caro pero bueno  ",
        {"PRECIO": 1, "CALIDAD": 5, "ENTREGA": None},
    )

    assert evaluacion.estrellas == 2
    assert evaluacion.comentario == "caro pero bueno"
    assert evaluacion.criterios == {"PRECIO": 1, "CALIDAD": 5}


@pytest.mark.parametrize("criterios", [{"PRECIO": 0}, {"ATENCION": 6}, {"INVENTADO": 3}])
def test_criterio_invalido_se_rechaza(e, criterios):
    with pytest.raises(DatosInvalidosError):
        evaluar(e.prov.id, e.owner.id, 3, "x", criterios)


def test_historial_y_calificacion_actual_es_la_mas_reciente_sin_tocar_las_anteriores(e):
    primera = evaluar(e.prov.id, e.owner.id, 5, "excelente", {"ENTREGA": 5})
    filas_antes = consultar(e.ruta, "SELECT * FROM evaluaciones_proveedor WHERE id = ?", (primera.id,))
    segunda = evaluar(e.prov.id, e.owner.id, 1, "no entregó")

    historial = servicio_evaluaciones_proveedor.historial(e.prov.id)
    assert [ev.id for ev in historial] == [segunda.id, primera.id]
    assert servicio_evaluaciones_proveedor.calificaciones_actuales() == {e.prov.id: 1}
    assert [tuple(f) for f in consultar(e.ruta, "SELECT * FROM evaluaciones_proveedor WHERE id = ?", (primera.id,))] == [
        tuple(f) for f in filas_antes
    ]
    ficha = servicio_proveedores.obtener_ficha(e.prov.id)
    assert ficha.evaluacion_actual.id == segunda.id and ficha.evaluacion_actual.requiere_advertencia


def test_misma_fecha_desempata_por_id(e):
    evaluar(e.prov.id, e.owner.id, 5, "a")
    evaluar(e.prov.id, e.owner.id, 2, "b")
    escribir(e.ruta, "DROP TRIGGER trg_evaluaciones_proveedor_inmutable")
    escribir(e.ruta, "UPDATE evaluaciones_proveedor SET fecha = '2026-01-01 10:00:00'")

    assert servicio_evaluaciones_proveedor.calificaciones_actuales([e.prov.id]) == {e.prov.id: 2}


def test_la_base_impide_editar_o_borrar_una_evaluacion(e):
    evaluacion = evaluar(e.prov.id, e.owner.id, 4, "bien")
    with pytest.raises(Exception, match="no se puede modificar"):
        escribir(e.ruta, "UPDATE evaluaciones_proveedor SET estrellas = 5 WHERE id = ?", (evaluacion.id,))
    with pytest.raises(Exception, match="no se puede borrar"):
        escribir(e.ruta, "DELETE FROM evaluaciones_proveedor WHERE id = ?", (evaluacion.id,))


@pytest.mark.parametrize(
    "columna, valor", [("estrellas", 7), ("estrellas", "cuatro"), ("comentario", "  "), ("precio", 0), ("atencion", 3.5)]
)
def test_la_base_rechaza_valores_invalidos_escritos_por_fuera(e, columna, valor):
    valores = {"proveedor_id": e.prov.id, "usuario_id": e.owner.id, "estrellas": 3, "comentario": "ok", columna: valor}
    with pytest.raises(Exception, match="CHECK"):
        escribir(
            e.ruta,
            f"INSERT INTO evaluaciones_proveedor ({', '.join(valores)}) VALUES ({', '.join('?' for _ in valores)})",
            tuple(valores.values()),
        )


def test_cashier_o_owner_inactivo_no_pueden_evaluar(e):
    cajera = crear_usuario("cajera", "CASHIER")
    inactivo = crear_usuario("viejo", "OWNER", activo=False)
    for usuario in (cajera, inactivo):
        with pytest.raises(PermisoDenegadoError):
            evaluar(e.prov.id, usuario.id, 5, "x")
    assert consultar(e.ruta, "SELECT COUNT(*) FROM evaluaciones_proveedor")[0][0] == 0


def test_proveedor_inexistente(e):
    with pytest.raises(ProveedorNoEncontradoError):
        evaluar(9999, e.owner.id, 3, "x")


def test_proveedor_dado_de_baja_se_puede_evaluar_y_una_evaluacion_impide_su_baja_fisica(e):
    nuevo = servicio_proveedores.crear_proveedor("Sin compras")
    evaluar(nuevo.id, e.owner.id, 3, "regular")

    assert servicio_proveedores.eliminar_proveedor(nuevo.id) is True  # tiene historia -> baja lógica
    evaluar(nuevo.id, e.owner.id, 2, "sigue mal")
    assert servicio_evaluaciones_proveedor.calificaciones_actuales([nuevo.id]) == {nuevo.id: 2}


def test_sin_calificacion_no_hay_advertencia_y_umbral_es_dos(e):
    assert servicio_evaluaciones_proveedor.calificaciones_actuales() == {}
    assert [requiere_advertencia(n) for n in (None, 1, 2, 3, 4, 5)] == [False, True, True, False, False, False]


def test_reenvio_idempotente_y_clave_con_otro_contenido(e):
    primera = evaluar(e.prov.id, e.owner.id, 4, "bien", {"PRECIO": 3}, clave_idempotencia="k1")
    repetida = evaluar(e.prov.id, e.owner.id, 4, " bien ", {"PRECIO": 3, "CALIDAD": None}, clave_idempotencia="k1")

    assert repetida.id == primera.id
    assert consultar(e.ruta, "SELECT COUNT(*) FROM evaluaciones_proveedor")[0][0] == 1
    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        evaluar(e.prov.id, e.owner.id, 5, "bien", {"PRECIO": 3}, clave_idempotencia="k1")


def test_evaluaciones_concurrentes_quedan_ambas_en_el_historial(e):
    errores = []

    def evaluar_en_hilo(estrellas):
        try:
            evaluar(e.prov.id, e.owner.id, estrellas, f"hilo {estrellas}")
        except Exception as error:  # noqa: BLE001 -- se reporta abajo
            errores.append(error)

    hilos = [threading.Thread(target=evaluar_en_hilo, args=(n,)) for n in (1, 2, 3, 4, 5)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert errores == []
    historial = servicio_evaluaciones_proveedor.historial(e.prov.id)
    assert len(historial) == 5
    assert servicio_evaluaciones_proveedor.calificaciones_actuales() == {e.prov.id: historial[0].estrellas}


def test_se_audita_la_evaluacion(e):
    evaluar(e.prov.id, e.owner.id, 2, "x")
    [fila] = consultar(e.ruta, "SELECT usuario_id, entidad_id, resumen FROM auditoria WHERE accion = 'PROVEEDOR_EVALUADO'")
    assert (fila["usuario_id"], fila["entidad_id"]) == (e.owner.id, e.prov.id)
    assert "2 estrella(s)" in fila["resumen"]


def test_comprarle_a_un_proveedor_de_una_estrella_no_se_bloquea_ni_cambia_su_calificacion(e):
    evaluar(e.prov.id, e.owner.id, 1, "pésimo")
    compra = servicio_compras.registrar_compra(e.prov.id, e.owner.id, [ItemCompra(crear_producto().id, 2, 100)])

    assert compra.total_centavos == 200
    assert servicio_evaluaciones_proveedor.calificaciones_actuales() == {e.prov.id: 1}
    assert len(servicio_evaluaciones_proveedor.historial(e.prov.id)) == 1


def test_listado_trae_la_calificacion_actual(e):
    otro = servicio_proveedores.crear_proveedor("Sur")
    evaluar(e.prov.id, e.owner.id, 2, "x")

    resumen = {r.proveedor.nombre: (r.calificacion, r.requiere_advertencia) for r in servicio_proveedores.listar_resumen()}
    assert resumen == {"Norte": (2, True), "Sur": (None, False)}
    assert otro.id
