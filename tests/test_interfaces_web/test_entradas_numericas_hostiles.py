"""V1.5-A: ninguna entrada numérica hostil (NaN, sNaN, Infinity, exponentes gigantes, enteros de más de 64 bits)
puede terminar en una excepción no controlada ni en HTTP >= 500, en ninguna ruta con campos numéricos.

Cada caso parte de una petición VÁLIDA (se comprueba que funciona) y cambia un solo campo por un valor hostil.
La respuesta debe ser un error controlado (toast de error, 4xx) y NO dejar ningún rastro: ni registros, ni
auditoría, ni stock, ni caja, ni saldo, ni clave de idempotencia consumida. Después, la operación válida con la
misma clave funciona de inmediato.

Un test descubre las rutas desde el OpenAPI de la app y exige que toda ruta con campos numéricos esté cubierta
(o exenta con motivo): si aparece una ruta nueva, alguien tiene que decidir cómo se prueba."""

import re

import pytest

from domain.venta import ItemVenta
from interfaces.web.app import app
from services import servicio_caja, servicio_clientes, servicio_compras, servicio_inventario, servicio_proveedores
from services import servicio_ventas
from tests.utilidades_clientes import consultar, crear_producto, usuario_logueado

from ._asgi_cliente import solicitud

MONTOS_HOSTILES = [
    "NaN",
    "sNaN",
    "Infinity",
    "-Infinity",
    "1E+9999999",
    "1e19",
    "92233720368547758.08",
    "9" * 40,
]
ENTEROS_HOSTILES = ["99999999999999999999", str(2**63), "-" + str(2**63 + 1), "1e30", "NaN", "Infinity", "1E+9999999", "²"]
IDS_HOSTILES = [str(2**63), str(10**21), str(2**64)]

# Tablas cuyo contenido no debe cambiar ante una entrada hostil.
TABLAS = (
    "ventas", "detalle_venta", "compras", "detalle_compra", "caja_movimientos", "sesiones_caja", "movimientos_cuenta",
    "ajustes_stock", "auditoria", "inventarios", "inventario_lineas", "productos", "clientes", "proveedores",
    "producto_proveedor", "historial_precios", "lotes_precios",
)


@pytest.fixture
def e(base_datos_temporal, caja_abierta):
    owner, cookies = usuario_logueado("OWNER", "duenio")
    producto = crear_producto("7790000000001", stock=50)
    cliente = servicio_clientes.crear_cliente("Ana", owner.id)
    servicio_ventas.registrar_venta(
        [ItemVenta(producto.id, 2)], "CUENTA_CORRIENTE", usuario_id=owner.id, cliente_id=cliente.id
    )  # saldo 2 x 100 = 200 centavos
    venta = servicio_ventas.registrar_venta([ItemVenta(producto.id, 1)], "EFECTIVO", usuario_id=owner.id)
    proveedor = servicio_proveedores.crear_proveedor("Prov")
    inventario = servicio_inventario.crear_inventario(owner.id, [producto.id])
    return type(
        "E",
        (),
        {
            "ruta": base_datos_temporal,
            "cookies": cookies,
            "owner": owner,
            "pid": producto.id,
            "cid": cliente.id,
            "vid": venta.id,
            "prov": proveedor.id,
            "iid": inventario.id,
        },
    )


def _instantanea(ruta) -> dict:
    """Contenido completo de cada tabla relevante (no solo su cantidad) y claves de idempotencia."""
    resultado = {}
    for tabla in TABLAS:
        resultado[tabla] = [tuple(f) for f in consultar(ruta, f"SELECT * FROM {tabla} ORDER BY 1")]
    return resultado


# (nombre, método, ruta, campos válidos, campos que se vuelven hostiles y su clase de valor)
# `{...}` en la ruta se reemplaza con los ids del escenario. Los campos `json` van en el cuerpo JSON.
CASOS = [
    ("caja_ingreso", "POST", "/caja/ingreso", {"monto": "5.00", "descripcion": "aporte", "clave_idempotencia": "k-ing"}, {"monto": "monto"}),
    ("caja_egreso", "POST", "/caja/egreso", {"monto": "1.00", "descripcion": "retiro", "clave_idempotencia": "k-egr"}, {"monto": "monto"}),
    ("caja_cerrar", "POST", "/caja/cerrar", {"monto_final": "1.00", "descripcion": ""}, {"monto_final": "monto"}),
    ("cobro", "POST", "/clientes/{cid}/cobro", {"monto": "1.00", "descripcion": "", "clave_idempotencia": "k-cobro"}, {"monto": "monto"}),
    (
        "compra",
        "POST",
        "/compras/nueva",
        {"proveedor_id": "{prov}", "observaciones": "", "producto_id": "{pid}", "cantidad": "1", "costo_unitario": "1.00", "clave_idempotencia": "k-compra"},
        {"costo_unitario": "monto", "cantidad": "entero", "proveedor_id": "entero", "producto_id": "entero"},
    ),
    (
        "producto_nuevo",
        "POST",
        "/productos/nuevo",
        {"codigo_barras": "7799999999991", "nombre": "N", "precio_costo": "1", "precio_venta": "2", "stock_actual": "1", "stock_minimo": "0", "categoria_id": ""},
        {"precio_costo": "monto", "precio_venta": "monto", "stock_actual": "entero", "stock_minimo": "entero", "categoria_id": "entero"},
    ),
    (
        "producto_editar",
        "POST",
        "/productos/{pid}/editar",
        {"codigo_barras": "7790000000001", "nombre": "Editado", "precio_costo": "1", "precio_venta": "2", "stock_minimo": "3", "categoria_id": "", "unidad_medida": "UNIDAD"},
        {"precio_costo": "monto", "precio_venta": "monto", "stock_minimo": "entero", "categoria_id": "entero"},
    ),
    (
        "ajuste",
        "POST",
        "/productos/{pid}/ajustar",
        {"motivo": "MERMA", "direccion": "restar", "cantidad": "1", "observaciones": "", "clave_idempotencia": "k-aj"},
        {"cantidad": "entero"},
    ),
    ("conteo_form", "POST", "/inventario/conteo/{pid}", {"cantidad": "3"}, {"cantidad": "entero"}),
    ("conteo_api", "POST", "/api/inventario/conteo/{pid}", {"json": {"cantidad": 3}}, {"json.cantidad": "entero_json"}),
    (
        "venta_api",
        "POST",
        "/api/ventas",
        {"json": {"items": [{"producto_id": "{pid}", "cantidad": 1}], "tipo_pago": "EFECTIVO", "clave_idempotencia": "k-venta"}},
        {"json.items.0.cantidad": "entero_json", "json.items.0.producto_id": "entero_json"},
    ),
    (
        "venta_a_cuenta_api",
        "POST",
        "/api/ventas",
        {"json": {"items": [{"producto_id": "{pid}", "cantidad": 1}], "tipo_pago": "CUENTA_CORRIENTE", "cliente_id": "{cid}", "clave_idempotencia": "k-vcc"}},
        {"json.cliente_id": "entero_json"},
    ),
    ("precios_vista_previa", "GET", "/precios/vista-previa", {"tipo": "PORCENTAJE", "direccion": "AUMENTAR", "valor": "10", "redondeo": "NINGUNO", "alcance": "TODOS"}, {"valor": "monto"}),
    (
        "precios_aplicar",
        "POST",
        "/precios/aplicar",
        {"tipo": "MONTO", "direccion": "AUMENTAR", "valor": "1", "redondeo": "NINGUNO", "alcance": "TODOS", "seleccion": ["{pid}:100"], "clave_idempotencia": "k-pr"},
        {"valor": "monto"},
    ),
    ("caja_abrir", "POST", "/caja/abrir", {"monto_inicial": "1.00", "descripcion": ""}, {"monto_inicial": "monto"}),
    (
        "inventario_nuevo",
        "POST",
        "/inventario/nuevo",
        {"alcance": "manual", "producto_id": "{pid}", "observaciones": ""},
        {"producto_id": "entero"},
    ),
    ("compras_listado", "GET", "/compras", {"proveedor_id": "{prov}"}, {"proveedor_id": "entero"}),
    ("productos_listado", "GET", "/productos", {"categoria_id": "1"}, {"categoria_id": "entero"}),
    ("auditoria_listado", "GET", "/auditoria", {"usuario_id": "1"}, {"usuario_id": "entero"}),
    ("historial_cliente", "GET", "/ventas/historial", {"cliente_id": "1"}, {"cliente_id": "entero"}),
]

# Estado previo que necesitan algunos casos (el escenario base tiene caja abierta e inventario abierto).
PREPARACION = {"caja_abrir": "caja_cerrada", "inventario_nuevo": "sin_inventario"}


def _preparar(e, nombre):
    if PREPARACION.get(nombre) == "caja_cerrada":
        servicio_caja.cerrar_caja(0)
    elif PREPARACION.get(nombre) == "sin_inventario":
        servicio_inventario.cancelar_inventario(e.iid, e.owner.id)


# Rutas con un id en el path: solo GET/POST simples, sin cuerpo relevante.
RUTAS_CON_ID = [
    ("GET", "/ventas/{id}"),
    ("GET", "/ventas/{id}/ticket"),
    ("GET", "/ventas/{id}/anular"),
    ("POST", "/ventas/{id}/anular"),
    ("GET", "/clientes/{id}"),
    ("GET", "/clientes/{id}/cobro"),
    ("GET", "/clientes/{id}/editar"),
    ("POST", "/clientes/{id}/desactivar"),
    ("POST", "/clientes/{id}/reactivar"),
    ("GET", "/compras/{id}"),
    ("GET", "/proveedores/{id}"),
    ("GET", "/proveedores/{id}/editar"),
    ("POST", "/proveedores/{id}/eliminar"),
    ("POST", "/proveedores/{id}/reactivar"),
    ("GET", "/inventario/{id}"),
    ("POST", "/inventario/{id}/confirmar"),
    ("POST", "/inventario/{id}/cancelar"),
    ("GET", "/productos/{id}/editar"),
    ("GET", "/productos/{id}/ajustes"),
    ("GET", "/productos/{id}/precios"),
    ("GET", "/productos/{id}/ajustar"),
    ("POST", "/productos/{id}/eliminar"),
    ("POST", "/productos/{id}/reactivar"),
    ("GET", "/empleados/{id}/editar"),
    ("POST", "/empleados/{id}/editar"),
    ("POST", "/empleados/{id}/resetear-password"),
    ("POST", "/clientes/{id}/editar"),
    ("POST", "/proveedores/{id}/editar"),
    ("POST", "/proveedores/{id}/productos"),
    ("POST", "/proveedores/{id}/productos/{id}/principal"),
    ("POST", "/proveedores/{id}/productos/{id}/quitar"),
    ("GET", "/productos/categorias/{id}/editar"),
    ("POST", "/productos/categorias/{id}/editar"),
    ("POST", "/productos/categorias/{id}/eliminar"),
    ("POST", "/productos/categorias/{id}/reactivar"),
    ("POST", "/empleados/{id}/activar"),
    ("POST", "/empleados/{id}/desactivar"),
    ("POST", "/empleados/{id}/rol"),
    ("GET", "/api/productos/buscar-codigo/{id}"),
]


def _sustituir(valor, e):
    if isinstance(valor, str):
        return re.sub(r"\{(\w+)\}", lambda m: str(getattr(e, m.group(1))), valor)
    if isinstance(valor, dict):
        return {k: _sustituir(v, e) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_sustituir(v, e) for v in valor]
    return valor


def _entero_si_corresponde(valor):
    """En JSON los ids y cantidades sustituidos vuelven a ser enteros."""
    if isinstance(valor, str) and re.fullmatch(r"\d+", valor):
        return int(valor)
    if isinstance(valor, dict):
        return {k: _entero_si_corresponde(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_entero_si_corresponde(v) for v in valor]
    return valor


def _poner(cuerpo: dict, camino: str, valor):
    """`json.items.0.cantidad` -> cuerpo['items'][0]['cantidad'] = valor (sin mutar el original)."""
    import copy

    cuerpo = copy.deepcopy(cuerpo)
    destino = cuerpo
    partes = camino.split(".")
    for parte in partes[:-1]:
        destino = destino[int(parte)] if parte.isdigit() else destino[parte]
    destino[int(partes[-1]) if partes[-1].isdigit() else partes[-1]] = valor
    return cuerpo


def _enviar(e, metodo, ruta, campos):
    ruta = _sustituir(ruta, e)
    campos = _sustituir(campos, e)
    if "json" in campos:
        return solicitud(metodo, ruta, cookies=e.cookies, json_body=_entero_si_corresponde(campos["json"]))
    if metodo == "GET" and campos:
        from urllib.parse import urlencode

        return solicitud("GET", f"{ruta}?{urlencode(campos)}", cookies=e.cookies)
    return solicitud(metodo, ruta, cookies=e.cookies, formulario=campos or None)


def _es_error_controlado(respuesta) -> bool:
    if respuesta.status in (400, 404, 422):
        return True
    return respuesta.status == 303 and "tipo=error" in (respuesta.header("location") or "")


def _es_exito(respuesta) -> bool:
    if respuesta.status == 200:
        return True
    return respuesta.status == 303 and "tipo=error" not in (respuesta.header("location") or "")


def _casos_de_campos():  # noqa: D103
    for nombre, metodo, ruta, campos, hostiles in CASOS:
        for campo, clase in hostiles.items():
            valores = {"monto": MONTOS_HOSTILES, "entero": ENTEROS_HOSTILES, "entero_json": [2**63, 10**30, -(2**63) - 1]}[clase]
            for valor in valores:
                yield pytest.param(nombre, metodo, ruta, campos, campo, valor, id=f"{nombre}-{campo}-{str(valor)[:14]}")


@pytest.mark.parametrize("nombre", [c[0] for c in CASOS])
def test_cada_caso_parte_de_una_peticion_valida_que_funciona(e, nombre):
    """Sin esto la matriz podría pasar en vacío (rechazada por otro motivo antes de llegar al código)."""
    _, metodo, ruta, campos, _ = next(c for c in CASOS if c[0] == nombre)
    _preparar(e, nombre)

    respuesta = _enviar(e, metodo, ruta, campos)

    assert _es_exito(respuesta), (nombre, respuesta.status, respuesta.header("location"))


@pytest.mark.parametrize(("nombre", "metodo", "ruta", "campos", "campo", "valor"), list(_casos_de_campos()))
def test_un_campo_numerico_hostil_da_un_error_controlado_sin_efectos_y_la_operacion_valida_sigue_funcionando(
    e, nombre, metodo, ruta, campos, campo, valor
):
    _preparar(e, nombre)
    antes = _instantanea(e.ruta)
    if campo.startswith("json."):
        hostil = {"json": _poner(campos["json"], campo.removeprefix("json."), valor)}
    else:
        hostil = {**campos, campo: valor}

    respuesta = _enviar(e, metodo, ruta, hostil)  # una excepción no controlada haría fallar el test acá

    assert respuesta.status < 500, (nombre, campo, valor, respuesta.status)
    # En un GET de listado, un filtro que no se puede interpretar puede dar "sin filtro" o un listado vacío (200).
    controlado = _es_error_controlado(respuesta) or (metodo == "GET" and respuesta.status == 200)
    assert controlado, (nombre, campo, valor, respuesta.status, respuesta.header("location"))
    assert _instantanea(e.ruta) == antes  # ni registros, ni auditoría, ni stock, ni caja, ni saldo
    assert _es_exito(_enviar(e, metodo, ruta, campos)), "la operación válida (misma clave) debe funcionar enseguida"


@pytest.mark.parametrize("id_hostil", IDS_HOSTILES)
@pytest.mark.parametrize(("metodo", "ruta"), RUTAS_CON_ID)
def test_un_id_gigante_en_la_ruta_no_produce_500_ni_efectos(e, metodo, ruta, id_hostil):
    antes = _instantanea(e.ruta)
    formulario = (
        {"motivo": "ERROR_CARGA", "observaciones": "", "rol": "CASHIER", "nombre_completo": "x", "nombre": "x", "producto_id": "1", "password": "clave-larga-12345", "nueva_password": "clave-larga-12345"}
        if metodo == "POST"
        else None
    )

    respuesta = solicitud(metodo, ruta.replace("{id}", id_hostil), cookies=e.cookies, formulario=formulario)

    assert respuesta.status < 500, (metodo, ruta, id_hostil, respuesta.status)
    assert _instantanea(e.ruta) == antes


def _archivo_csv(celdas: list[str]) -> dict:
    encabezado = "codigo_barras,nombre,precio_costo,precio_venta,stock_actual,stock_minimo"
    filas = [encabezado, "7788000000001," + ",".join(celdas)]
    return {"archivo": ("productos.csv", "\n".join(filas).encode("utf-8"))}


@pytest.mark.parametrize("celda", ["NaN", "Infinity", "1e400", "1E+9999999", "99999999999999999999999", "9223372036854775808"])
@pytest.mark.parametrize("posicion", [1, 2, 3, 4])
def test_la_importacion_con_celdas_hostiles_no_da_500_y_no_crea_nada(e, celda, posicion):
    celdas = ["Producto importado", "1", "2", "3", "0"]
    celdas[posicion] = celda
    antes = _instantanea(e.ruta)

    respuesta = solicitud(
        "POST", "/productos/importar", cookies=e.cookies, formulario={"modo": "CREAR_Y_ACTUALIZAR"}, archivos=_archivo_csv(celdas)
    )

    assert respuesta.status < 500, (celda, posicion, respuesta.status)
    assert _instantanea(e.ruta) == antes  # "todo o nada" por defecto: la fila inválida no crea nada


@pytest.mark.parametrize("valor", MONTOS_HOSTILES + ENTEROS_HOSTILES)
@pytest.mark.parametrize("campo", ["fecha_desde", "fecha_hasta"])
@pytest.mark.parametrize("ruta", ["/ventas/historial", "/reportes", "/compras", "/auditoria"])
def test_fechas_con_texto_numerico_hostil_no_dan_500(e, ruta, campo, valor):
    """Las fechas son texto libre (`YYYY-MM-DD`): un valor que no es fecha da un listado vacío, no un 500."""
    from urllib.parse import urlencode

    respuesta = solicitud("GET", f"{ruta}?{urlencode({campo: valor})}", cookies=e.cookies)

    assert respuesta.status < 500, (ruta, campo, valor, respuesta.status)


# --- cobertura: toda ruta con campos numéricos está probada o exenta con motivo -------------------------------------

_NOMBRE_NUMERICO = re.compile(r"(monto|precio|costo|valor|importe|total|cantidad|stock|delta|saldo|recibido|_id$|^id$)")
EXENTAS = {
    # (método, ruta): motivo
    ("POST", "/empleados/nuevo"): "solo texto (usuario, nombre, contraseña, rol): sin campos numéricos",
    ("POST", "/productos/categorias"): "solo el nombre de la categoría",
    ("GET", "/api/clientes/buscar"): "búsqueda de texto con límite fijo",
    ("GET", "/reportes"): "fechas como texto: se ejercita en `historial_fecha`",
    ("GET", "/auditoria"): "filtros de texto y fecha con tope fijo; el usuario_id se cubre abajo",
}


def _rutas_con_campos_numericos() -> set[tuple[str, str]]:
    esquemas = app.openapi()["components"]["schemas"]
    resultado = set()
    for ruta, metodos in app.openapi()["paths"].items():
        for metodo, detalle in metodos.items():
            campos = [p["name"] for p in detalle.get("parameters", [])]
            for medio in detalle.get("requestBody", {}).get("content", {}).values():
                referencia = medio.get("schema", {}).get("$ref")
                if referencia:
                    campos += list(esquemas[referencia.split("/")[-1]].get("properties", {}))
            if any(_NOMBRE_NUMERICO.search(c) for c in campos):
                resultado.add((metodo.upper(), ruta))
    return resultado


def _rutas_cubiertas() -> set[tuple[str, str]]:
    cubiertas = {(m, r) for _, m, r, _, _ in CASOS}
    cubiertas |= {(m, r.replace("{id}", "{x}")) for m, r in RUTAS_CON_ID}
    cubiertas |= {("POST", "/productos/importar"), ("GET", "/productos/importar")}
    return cubiertas


def _normalizar(ruta: str) -> str:
    return re.sub(r"\{[^}]+\}", "{x}", ruta)


def test_toda_ruta_con_campos_numericos_esta_cubierta_o_exenta_con_motivo():
    cubiertas = {(m, _normalizar(r)) for m, r in _rutas_cubiertas()}
    exentas = {(m, _normalizar(r)) for (m, r) in EXENTAS}
    sin_cubrir = sorted((m, r) for m, r in _rutas_con_campos_numericos() if (m, _normalizar(r)) not in cubiertas | exentas)

    assert sin_cubrir == [], f"rutas con campos numéricos sin probar contra entradas hostiles: {sin_cubrir}"


def test_auditoria_usuario_id_con_mas_de_4300_digitos_es_error_controlado(e):
    """`int()` rechaza más de 4300 dígitos con `ValueError`: no debe llegar como 500."""
    respuesta = solicitud("GET", "/auditoria?usuario_id=" + "9" * 5000, cookies=e.cookies)
    assert respuesta.status == 303
