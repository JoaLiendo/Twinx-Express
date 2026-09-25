"""Arranca un servidor FastAPI REAL para los tests E2E de Playwright
(Fase 5E.1), con SQLite completamente aislada en un archivo temporal.

Por qué existe este script (no es una conveniencia, es la pieza que
resuelve el problema central de esta fase): Playwright (`@playwright/test`)
corre en un proceso Node separado del servidor Python -- no hay forma de
"monkeypatchear" nada entre ambos, a diferencia de `tests/conftest.py`,
donde pytest y la app corren en el mismo intérprete. Acá el aislamiento
de la base de datos tiene que resolverse con **configuración explícita
entre procesos**: la ruta de la base temporal viaja como argumento de
línea de comandos, nunca como un valor implícito ni un default.

Qué hace, en orden:
1. Exige `<ruta_db> <puerto>` como argumentos -- sin default. Si faltan,
   este script se niega a arrancar. Esto hace estructuralmente imposible
   que, por un error de invocación, termine sirviendo sobre
   `data/kiosco.db`: no hay ningún camino de código que use esa ruta.
2. Si ya existe un archivo en `<ruta_db>` -- o alguno de sus posibles
   sidecars de SQLite (`-journal`, `-wal`, `-shm`), que pueden quedar
   sueltos si un proceso anterior murió a mitad de una transacción sin
   llegar a hacer *commit*/*rollback* -- los borra a todos. Corrección de
   auditoría (5E.1): antes solo se borraba el archivo principal. Esta
   limpieza actúa exclusivamente sobre `<ruta_db>` y los tres sufijos de
   arriba concatenados sobre ese mismo nombre -- nunca sobre ninguna otra
   ruta. Con `playwright.config.js` generando ahora un directorio único
   por ejecución (ver ese archivo), esta limpieza es una segunda capa de
   defensa, no la única: ya es prácticamente imposible que dos corridas
   compartan `<ruta_db>` para empezar.
3. Reasigna `db.conexion.RUTA_BASE_DATOS` a esa ruta ANTES de importar la
   app -- mismo mecanismo exacto que ya usa
   `tests/conftest.py::base_datos_temporal` (parchear el atributo en el
   módulo `db.conexion`, no en `config`, porque `db/repositorios/*.py`
   hace `from db.conexion import obtener_conexion`: ese `import` crea su
   propio binding a la función, pero la función en sí sigue leyendo
   `RUTA_BASE_DATOS` desde el namespace de `db.conexion` en cada llamada,
   así que reasignar el atributo del módulo alcanza).
4. Inicializa el esquema y siembra los datos mínimos de prueba (Fase
   5E.2: un usuario OWNER + dos productos con precio/stock/código de
   barras deterministas; Fase 5E.3: un tercer producto con un nombre
   deliberadamente malicioso, exclusivo del test de regresión de XSS),
   reutilizando `domain.usuario`/`services.servicio_auth`/
   `services.servicio_stock`/`db.repositorios.usuarios` tal cual --
   ninguna regla de negocio nueva, ningún endpoint de test agregado a
   la app.
5. Levanta uvicorn SIN `reload=True` (nada de proceso "reloader" extra:
   un único proceso, determinista, fácil de matar al terminar).

Uso (invocado por Playwright vía `webServer` en `playwright.config.js`,
con el directorio de trabajo en la raíz de `stock_app/`):

    python -m tests_e2e.servidor_pruebas <ruta_db_temporal> <puerto>
"""

import sys
from pathlib import Path

NOMBRE_USUARIO_PRUEBA = "e2e_owner"
PASSWORD_PRUEBA = "clave-e2e-12345"
NOMBRE_CASHIER_PRUEBA = "e2e_cashier"

# Fase 5E.2: datos deterministas y explícitos para los flujos de POS.
# Precios en centavos enteros (nunca floats, mismo criterio que todo el
# dominio -- ver domain/dinero.py). Stock generoso (100 unidades) porque
# varios tests E2E comparten esta misma DB dentro de UNA corrida de
# `npx playwright test` (un solo `webServer` por invocación): cada test
# consume como máximo unas pocas unidades, nunca cerca de agotarlo.
PRODUCTO_1_CODIGO_BARRAS = "7790000000001"
PRODUCTO_1_NOMBRE = "Alfajor E2E"
PRODUCTO_1_PRECIO_VENTA_CENTAVOS = 250  # $2.50
PRODUCTO_1_STOCK = 100

PRODUCTO_2_CODIGO_BARRAS = "7790000000002"
PRODUCTO_2_NOMBRE = "Gaseosa E2E"
PRODUCTO_2_PRECIO_VENTA_CENTAVOS = 500  # $5.00
PRODUCTO_2_STOCK = 100

# Fase 5E.3 (corrección de auditoría, XSS en pos.js): producto exclusivo
# del test de seguridad `08-seguridad-xss.spec.js`. El nombre combina, en
# un solo string, los vectores que la vulnerabilidad original permitía:
# una comilla doble seguida de un atributo nuevo (el vector más grave,
# rompía `aria-label="..."`), una etiqueta `<img onerror=...>` (se
# ejecuta igual insertada por `innerHTML`, a diferencia de `<script>`),
# comilla simple, `&`, y una etiqueta genérica. `domain.producto` no
# limita caracteres ni longitud del nombre (ver auditoría 5E.3), así que
# esto es un nombre de producto perfectamente válido para el dominio.
PRODUCTO_XSS_CODIGO_BARRAS = "7790000000099"
PRODUCTO_XSS_NOMBRE = (
    '<img src=x onerror="window.__xssEjecutado = true">'
    'Nombre" onmouseover="window.__xssEjecutado = true" raro\' & <b>negrita</b>'
)
PRODUCTO_XSS_PRECIO_VENTA_CENTAVOS = 100  # $1.00
PRODUCTO_XSS_STOCK = 100


def _sembrar_usuario_de_prueba() -> None:
    """Usuarios que necesitan los tests: un OWNER y un CASHIER válidos. Reutiliza
    el dominio/servicios reales -- no es un endpoint de la app, es un
    script de bootstrap que corre antes de que uvicorn empiece a
    escuchar."""
    from db.repositorios import usuarios as repositorio_usuarios
    from domain.usuario import Usuario
    from services import servicio_auth

    # iteraciones=1000 (no las 600.000 de producción): mismo parámetro explícito que ya usan los
    # tests HTTP existentes (ver tests/test_interfaces_web/*.py) para no pagar ~220ms de hashing
    # por cada arranque de servidor E2E.
    password_hash = servicio_auth.hashear_password(PASSWORD_PRUEBA, iteraciones=1000)
    for nombre_usuario, nombre_completo, rol in (
        (NOMBRE_USUARIO_PRUEBA, "E2E Owner", "OWNER"),
        (NOMBRE_CASHIER_PRUEBA, "E2E Cashier", "CASHIER"),
    ):
        repositorio_usuarios.crear_usuario(
            Usuario(
                nombre_usuario=nombre_usuario,
                nombre_completo=nombre_completo,
                password_hash=password_hash,
                rol=rol,
            )
        )


def _abrir_caja_de_prueba() -> None:
    """Deja la caja abierta: desde V1.1 el POS rechaza cualquier venta sin
    una caja abierta, y los flujos de cobro de los tests la necesitan."""
    from services import servicio_caja

    servicio_caja.abrir_caja(0)


def _sembrar_productos_de_prueba() -> None:
    """Dos productos mínimos para los flujos de carrito/cobro de Fase
    5E.2, reutilizando `services.servicio_stock.registrar_producto` tal
    cual (misma función que usa la app real, la misma que ya usan los
    tests pytest) -- ninguna regla de negocio nueva, ningún acceso a
    `data/kiosco.db` (la DB activa en este proceso ya es la temporal,
    reasignada antes de llegar acá)."""
    from services import servicio_stock

    servicio_stock.registrar_producto(
        PRODUCTO_1_CODIGO_BARRAS,
        PRODUCTO_1_NOMBRE,
        precio_costo_centavos=150,
        precio_venta_centavos=PRODUCTO_1_PRECIO_VENTA_CENTAVOS,
        stock_actual=PRODUCTO_1_STOCK,
    )
    servicio_stock.registrar_producto(
        PRODUCTO_2_CODIGO_BARRAS,
        PRODUCTO_2_NOMBRE,
        precio_costo_centavos=300,
        precio_venta_centavos=PRODUCTO_2_PRECIO_VENTA_CENTAVOS,
        stock_actual=PRODUCTO_2_STOCK,
    )
    # V1.1: producto con precio 0 (como los del catálogo inicial), para el test
    # de "Precio no configurado" del POS.
    servicio_stock.registrar_producto(
        "7790000000088",
        "Sin Precio E2E",
        precio_costo_centavos=0,
        precio_venta_centavos=0,
        stock_actual=10,
    )
    # V1.2: producto bajo el stock mínimo (reposición) y producto exclusivo del
    # test de actualización masiva de precios.
    servicio_stock.registrar_producto(
        "7790000000077",
        "Bajo Stock E2E",
        precio_costo_centavos=300,
        precio_venta_centavos=600,
        stock_actual=1,
        stock_minimo=5,
    )
    servicio_stock.registrar_producto(
        "7790000000066",
        "Precio Masivo E2E",
        precio_costo_centavos=500,
        precio_venta_centavos=1000,
        stock_actual=10,
    )
    servicio_stock.registrar_producto(
        PRODUCTO_XSS_CODIGO_BARRAS,
        PRODUCTO_XSS_NOMBRE,
        precio_costo_centavos=50,
        precio_venta_centavos=PRODUCTO_XSS_PRECIO_VENTA_CENTAVOS,
        stock_actual=PRODUCTO_XSS_STOCK,
    )


# Sufijos que SQLite puede dejar junto a la base principal: "" es el
# archivo mismo, los otros tres son sus posibles sidecars (journal de
# rollback -- el modo por defecto, que es el que usa esta app -- y WAL/
# shared-memory, por si algún día se activara ese modo). Se concatenan
# sobre el nombre completo del archivo, nunca lo reemplazan (SQLite
# nombra "kiosco.db-journal", no "kiosco-journal").
_SUFIJOS_SIDECAR_SQLITE = ("", "-journal", "-wal", "-shm")


def _limpiar_db_temporal(ruta_db: Path) -> None:
    """Borra `ruta_db` y sus posibles sidecars de SQLite, si existen.

    Actúa exclusivamente sobre `ruta_db` (la ruta temporal recibida por
    argumento) y los tres sufijos de arriba concatenados sobre ese mismo
    nombre -- nunca sobre ninguna otra ruta ni, en particular, sobre
    nada relacionado con `data/kiosco.db`.
    """
    for sufijo in _SUFIJOS_SIDECAR_SQLITE:
        ruta_sidecar = Path(str(ruta_db) + sufijo)
        if ruta_sidecar.exists():
            ruta_sidecar.unlink()


def main() -> None:
    if len(sys.argv) != 3:
        print(
            "Uso: python -m tests_e2e.servidor_pruebas <ruta_db_temporal> <puerto>",
            file=sys.stderr,
        )
        sys.exit(1)

    ruta_db = Path(sys.argv[1]).resolve()
    puerto = int(sys.argv[2])

    ruta_db.parent.mkdir(parents=True, exist_ok=True)
    _limpiar_db_temporal(ruta_db)

    import db.conexion as modulo_conexion

    modulo_conexion.RUTA_BASE_DATOS = ruta_db

    from db.conexion import inicializar_base_datos

    inicializar_base_datos()
    _sembrar_usuario_de_prueba()
    _sembrar_productos_de_prueba()
    _abrir_caja_de_prueba()

    print(f"[servidor_pruebas] DB temporal: {ruta_db}")
    print(f"[servidor_pruebas] Sirviendo en http://127.0.0.1:{puerto}")

    import uvicorn
    from interfaces.web.app import app

    uvicorn.run(app, host="127.0.0.1", port=puerto, log_level="warning")


if __name__ == "__main__":
    main()
