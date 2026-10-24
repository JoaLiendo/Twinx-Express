"""V1.13-D: documentación fiscal declarada de proveedores y resumen OWNER del listado (dimensiones
independientes: calificación, reclamos y deuda nunca se combinan)."""

from datetime import date

import pytest

from domain.compra import ItemCompra
from excepciones import (
    ClaveIdempotenciaReutilizadaError,
    DatosInvalidosError,
    PermisoDenegadoError,
    ProveedorNoEncontradoError,
)
from services import (
    servicio_compras,
    servicio_documentos_proveedor,
    servicio_evaluaciones_proveedor,
    servicio_proveedores,
    servicio_reclamos_proveedor,
)
from tests.utilidades_clientes import consultar, crear_owner, crear_producto, crear_usuario, escribir

registrar = servicio_documentos_proveedor.registrar_documento
HOY = date(2026, 10, 24)


@pytest.fixture
def e(base_datos_temporal):
    return type("E", (), {"ruta": base_datos_temporal, "owner": crear_owner(),
                          "prov": servicio_proveedores.crear_proveedor("Norte")})


def test_registrar_documentos_y_listarlos_del_mas_reciente_al_mas_antiguo(e):
    viejo = registrar(e.prov.id, e.owner.id, "CONSTANCIA_FISCAL", "2025-03-01", hoy=HOY)
    cm05 = registrar(e.prov.id, e.owner.id, "CM05", "2026-05-31", " 2025 ", "  presentado por mail ", hoy=HOY)

    assert (cm05.referencia, cm05.observacion, cm05.usuario_id) == ("2025", "presentado por mail", e.owner.id)
    assert [d.id for d in servicio_documentos_proveedor.listar_de_proveedor(e.prov.id)] == [cm05.id, viejo.id]
    assert viejo.referencia is None and viejo.observacion is None and viejo.fecha_registro


@pytest.mark.parametrize(
    "tipo, fecha", [("ARCA", "2026-01-01"), ("CM05", ""), ("CM05", "2026-02-30"), ("CM05", "01/02/2026"), ("CM05", "2026-10-25")]
)
def test_tipo_o_fecha_invalidos_o_futuros(e, tipo, fecha):
    with pytest.raises(DatosInvalidosError):
        registrar(e.prov.id, e.owner.id, tipo, fecha, hoy=HOY)


def test_textos_demasiado_largos(e):
    with pytest.raises(DatosInvalidosError):
        registrar(e.prov.id, e.owner.id, "OTRO_DOCUMENTO_FISCAL", "2026-01-01", "x" * 101, hoy=HOY)
    with pytest.raises(DatosInvalidosError):
        registrar(e.prov.id, e.owner.id, "OTRO_DOCUMENTO_FISCAL", "2026-01-01", None, "x" * 501, hoy=HOY)


def test_permisos_y_proveedor_inexistente(e):
    with pytest.raises(PermisoDenegadoError):
        registrar(e.prov.id, crear_usuario("cajera", "CASHIER").id, "CM05", "2026-01-01", hoy=HOY)
    with pytest.raises(ProveedorNoEncontradoError):
        registrar(9999, e.owner.id, "CM05", "2026-01-01", hoy=HOY)


def test_idempotencia_del_formulario(e):
    primero = registrar(e.prov.id, e.owner.id, "CM05", "2026-01-01", clave_idempotencia="d1", hoy=HOY)
    assert registrar(e.prov.id, e.owner.id, "CM05", "2026-01-01", clave_idempotencia="d1", hoy=HOY).id == primero.id
    with pytest.raises(ClaveIdempotenciaReutilizadaError):
        registrar(e.prov.id, e.owner.id, "CM05", "2026-01-02", clave_idempotencia="d1", hoy=HOY)


def test_la_base_impide_editar_borrar_o_cargar_fechas_invalidas(e):
    documento = registrar(e.prov.id, e.owner.id, "INGRESOS_BRUTOS", "2026-01-01", hoy=HOY)
    with pytest.raises(Exception, match="no se puede modificar"):
        escribir(e.ruta, "UPDATE documentos_proveedor SET tipo = 'CM05' WHERE id = ?", (documento.id,))
    with pytest.raises(Exception, match="no se puede borrar"):
        escribir(e.ruta, "DELETE FROM documentos_proveedor WHERE id = ?", (documento.id,))
    with pytest.raises(Exception, match="CHECK"):
        escribir(
            e.ruta,
            "INSERT INTO documentos_proveedor (proveedor_id, tipo, fecha_documento, usuario_id) VALUES (?, 'CM05', '2026-13-01', ?)",
            (e.prov.id, e.owner.id),
        )


def test_un_documento_impide_la_baja_fisica_del_proveedor(e):
    registrar(e.prov.id, e.owner.id, "CONSTANCIA_FISCAL", "2026-01-01", hoy=HOY)
    assert servicio_proveedores.eliminar_proveedor(e.prov.id) is True
    assert consultar(e.ruta, "SELECT activo FROM proveedores WHERE id = ?", (e.prov.id,))[0][0] == 0


def test_se_audita_el_documento(e):
    registrar(e.prov.id, e.owner.id, "CM05", "2026-01-01", hoy=HOY)
    [fila] = consultar(e.ruta, "SELECT resumen FROM auditoria WHERE accion = 'DOCUMENTO_PROVEEDOR_REGISTRADO'")
    assert "CM05" in fila["resumen"] and "2026-01-01" in fila["resumen"]


# --- resumen del listado ------------------------------------------------------------------------------


@pytest.fixture
def resumen(e):
    malo = servicio_proveedores.crear_proveedor("Malo")
    deudor = servicio_proveedores.crear_proveedor("Deudor")
    reclamado = servicio_proveedores.crear_proveedor("Reclamado")
    servicio_evaluaciones_proveedor.registrar_evaluacion(malo.id, e.owner.id, 2, "flojo")
    servicio_evaluaciones_proveedor.registrar_evaluacion(deudor.id, e.owner.id, 5, "excelente")
    servicio_compras.registrar_compra(
        deudor.id, e.owner.id, [ItemCompra(crear_producto().id, 3, 100)], condicion_pago="CREDITO"
    )
    servicio_reclamos_proveedor.crear_reclamo(reclamado.id, e.owner.id, "ENTREGA", "tarde")
    return type("R", (), {"malo": malo, "deudor": deudor, "reclamado": reclamado})


def _nombres(**filtros) -> list[str]:
    return [r.proveedor.nombre for r in servicio_proveedores.listar_resumen(**filtros)]


def test_cada_filtro_mira_una_sola_dimension(e, resumen):
    assert _nombres(filtro="baja_calificacion") == ["Malo"]
    assert _nombres(filtro="reclamos_abiertos") == ["Reclamado"]
    assert _nombres(filtro="con_deuda") == ["Deudor"]
    assert _nombres() == ["Deudor", "Malo", "Norte", "Reclamado"]
    [deudor] = [r for r in servicio_proveedores.listar_resumen() if r.proveedor.id == resumen.deudor.id]
    assert deudor.deuda.saldo_total_centavos == 300 and deudor.calificacion == 5 and not deudor.requiere_advertencia


def test_filtro_combinado_con_busqueda_e_inactivos(e, resumen):
    servicio_proveedores.eliminar_proveedor(resumen.deudor.id)  # tiene historia: baja lógica, deuda intacta

    assert _nombres(filtro="con_deuda") == []
    assert _nombres(filtro="con_deuda", incluir_inactivos=True) == ["Deudor"]
    assert _nombres(texto="mal", filtro="reclamos_abiertos") == []


def test_reclamo_resuelto_deja_de_contar_como_abierto(e, resumen):
    [reclamo] = servicio_reclamos_proveedor.listar_de_proveedor(resumen.reclamado.id)
    servicio_reclamos_proveedor.cambiar_estado(reclamo.id, e.owner.id, "RESUELTO", "llegó", "PENDIENTE")
    assert _nombres(filtro="reclamos_abiertos") == []


def test_filtro_inexistente(e):
    with pytest.raises(DatosInvalidosError):
        servicio_proveedores.listar_resumen(filtro="indice_magico")
