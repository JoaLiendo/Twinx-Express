"""Tests de `services/servicio_catalogo_inicial.py`: validación del JSON,
carga única y transaccional, y el catálogo real distribuido."""

import copy
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

import db.conexion as modulo_conexion
import db.repositorios.catalogo_inicial as modulo_repositorio
import services.servicio_backup as modulo_backup
import services.servicio_catalogo_inicial as servicio
import services.servicio_restore as modulo_restore
from config import RUTA_CATALOGO_INICIAL
from db.conexion import obtener_conexion
from db.repositorios import usuarios as repositorio_usuarios
from domain.usuario import Usuario
from excepciones import ErrorBaseDatos, ErrorCatalogoInicial
from services.control_escrituras import ControlEscrituras

_CATALOGO_VALIDO = {
    "version": 1,
    "categorias": ["Bebidas", "Snacks"],
    "productos": [
        {"codigo": "TX-T-001", "nombre": "Uno", "categoria": "Bebidas", "unidad": "UNIDAD"},
        {"codigo": "TX-T-002", "nombre": "Dos", "categoria": "Snacks", "unidad": "UNIDAD"},
    ],
}


def _escribir(tmp_path: Path, contenido) -> Path:
    ruta = tmp_path / "catalogo.json"
    ruta.write_text(contenido if isinstance(contenido, str) else json.dumps(contenido), encoding="utf-8")
    return ruta


def _estado():
    """`(categorías, productos, usuarios, user_version)` actuales."""
    with obtener_conexion() as conexion:
        return (
            conexion.execute("SELECT COUNT(*) FROM categorias").fetchone()[0],
            conexion.execute("SELECT COUNT(*) FROM productos").fetchone()[0],
            conexion.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0],
            conexion.execute("PRAGMA user_version").fetchone()[0],
        )


def _con(cambio):
    """Copia del catálogo válido con `cambio(doc)` aplicado."""
    doc = copy.deepcopy(_CATALOGO_VALIDO)
    cambio(doc)
    return doc


def _producto_con(**campos):
    return _con(lambda doc: doc["productos"][0].update(campos))


def _producto_sin_unidad():
    return _con(lambda doc: doc["productos"][0].pop("unidad"))


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------

_CATALOGOS_INVALIDOS = {
    "version distinta de la soportada": _con(lambda d: d.update(version=2)),
    "version booleana": _con(lambda d: d.update(version=True)),
    "falta una clave": {k: v for k, v in _CATALOGO_VALIDO.items() if k != "categorias"},
    "clave extra": _con(lambda d: d.update(extra=1)),
    "sin categorias": _con(lambda d: d.update(categorias=[])),
    "categoria repetida": _con(lambda d: d.update(categorias=["Bebidas", "Bebidas", "Snacks"])),
    "categoria con nombre vacio": _con(lambda d: d.update(categorias=["Bebidas", "  ", "Snacks"])),
    "categoria sin productos": _con(lambda d: d["categorias"].append("Huérfana")),
    "categoria inexistente": _producto_con(categoria="No existe"),
    "codigo duplicado": _con(lambda d: d["productos"][1].update(codigo="TX-T-001")),
    "codigo vacio": _producto_con(codigo=""),
    "nombre vacio": _producto_con(nombre="  "),
    "unidad invalida": _producto_con(unidad="KILO"),
    "codigo no es texto": _producto_con(codigo=123),
    "precio de venta": _producto_con(precio_venta_centavos=100),
    "precio de costo": _producto_con(precio_costo_centavos=50),
    "stock": _producto_con(stock_actual=5),
    "imagen": _producto_con(imagen_archivo="x.jpg"),
    "producto sin una clave": _producto_sin_unidad(),
    "sin productos": _con(lambda d: d.update(productos=[])),
    "demasiados productos": _con(
        lambda d: d.update(
            productos=[
                {"codigo": f"TX-T-{i:03d}", "nombre": f"P{i}", "categoria": "Bebidas", "unidad": "UNIDAD"}
                for i in range(servicio.MAXIMO_PRODUCTOS + 1)
            ]
            + [{"codigo": "TX-S-001", "nombre": "S", "categoria": "Snacks", "unidad": "UNIDAD"}]
        )
    ),
    "no es un objeto": [],
}


def test_catalogo_valido_se_lee_correctamente(tmp_path):
    version, categorias, productos = servicio.leer_catalogo(_escribir(tmp_path, _CATALOGO_VALIDO))

    assert version == 1
    assert [c.nombre for c in categorias] == ["Bebidas", "Snacks"]
    assert [(p.codigo_barras, cat) for p, cat in productos] == [("TX-T-001", "Bebidas"), ("TX-T-002", "Snacks")]


@pytest.mark.parametrize("descripcion", list(_CATALOGOS_INVALIDOS))
def test_catalogo_invalido_se_rechaza(tmp_path, descripcion):
    with pytest.raises(ErrorCatalogoInicial):
        servicio.leer_catalogo(_escribir(tmp_path, _CATALOGOS_INVALIDOS[descripcion]))


def test_json_mal_formado_se_rechaza(tmp_path):
    with pytest.raises(ErrorCatalogoInicial, match="JSON válido"):
        servicio.leer_catalogo(_escribir(tmp_path, "{ esto no es json"))


def test_archivo_inexistente_se_rechaza(tmp_path):
    with pytest.raises(ErrorCatalogoInicial):
        servicio.leer_catalogo(tmp_path / "no_existe.json")


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------


def test_siembra_una_instalacion_nueva(base_datos_temporal, tmp_path):
    assert servicio.sembrar_si_corresponde(_escribir(tmp_path, _CATALOGO_VALIDO)) is True

    assert _estado() == (2, 2, 0, 1)


def test_segunda_ejecucion_no_duplica(base_datos_temporal, tmp_path):
    ruta = _escribir(tmp_path, _CATALOGO_VALIDO)
    servicio.sembrar_si_corresponde(ruta)

    assert servicio.sembrar_si_corresponde(ruta) is False

    assert _estado() == (2, 2, 0, 1)


def test_instalacion_existente_no_lee_ni_valida_el_archivo(base_datos_temporal, tmp_path):
    repositorio_usuarios.crear_usuario(
        Usuario(nombre_usuario="ana", nombre_completo="Ana", password_hash="hash", rol="OWNER")
    )

    # El archivo ni siquiera existe: no se toca, y no hay ningún error.
    assert servicio.sembrar_si_corresponde(tmp_path / "no_existe.json") is False

    assert _estado() == (0, 0, 1, 0)


@pytest.mark.parametrize("descripcion", ["categoria inexistente", "codigo duplicado", "unidad invalida"])
def test_catalogo_invalido_no_deja_datos_parciales_y_registra_error(
    base_datos_temporal, tmp_path, caplog, descripcion
):
    with caplog.at_level("INFO"):
        with pytest.raises(ErrorCatalogoInicial):
            servicio.sembrar_si_corresponde(_escribir(tmp_path, _CATALOGOS_INVALIDOS[descripcion]))

    assert _estado() == (0, 0, 0, 0)
    errores = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errores and "catálogo inicial" in errores[0].getMessage()


def test_un_fallo_de_base_de_datos_se_traduce_y_no_deja_datos_parciales(
    base_datos_temporal, tmp_path, monkeypatch, caplog
):
    conexion_real = modulo_repositorio.obtener_conexion

    @contextmanager
    def conexion_que_falla_antes_del_commit(*, inmediata=False):
        with conexion_real(inmediata=inmediata) as conexion:
            yield conexion
            raise ErrorBaseDatos("fallo simulado")

    monkeypatch.setattr(modulo_repositorio, "obtener_conexion", conexion_que_falla_antes_del_commit)

    with caplog.at_level("ERROR"):
        with pytest.raises(ErrorCatalogoInicial):
            servicio.sembrar_si_corresponde(_escribir(tmp_path, _CATALOGO_VALIDO))

    assert _estado() == (0, 0, 0, 0)
    assert any(r.levelname == "ERROR" for r in caplog.records)


# ---------------------------------------------------------------------------
# Catálogo real distribuido
# ---------------------------------------------------------------------------

_CATEGORIAS_ESPERADAS = [
    "Gaseosas",
    "Aguas y sodas",
    "Jugos y energizantes",
    "Golosinas",
    "Chocolates y alfajores",
    "Snacks",
    "Galletitas",
    "Almacén",
    "Higiene y cuidado",
    "Varios",
]


def test_el_catalogo_real_es_valido_y_tiene_el_tamano_esperado():
    version, categorias, productos = servicio.leer_catalogo(RUTA_CATALOGO_INICIAL)

    assert version == 1
    assert [c.nombre for c in categorias] == _CATEGORIAS_ESPERADAS
    assert len(productos) == 41


def test_el_catalogo_real_no_trae_precios_stock_imagenes_ni_ean():
    _version, _categorias, productos = servicio.leer_catalogo(RUTA_CATALOGO_INICIAL)

    codigos = [p.codigo_barras for p, _cat in productos]
    assert len(set(codigos)) == len(codigos)
    for producto, _categoria in productos:
        assert producto.codigo_barras.startswith("TX-")
        assert not producto.codigo_barras.isdigit()  # nunca parece un EAN
        assert (producto.precio_costo_centavos, producto.precio_venta_centavos) == (0, 0)
        assert (producto.stock_actual, producto.stock_minimo) == (0, 0)
        assert producto.unidad_medida == "UNIDAD"
        assert producto.imagen_archivo is None


def test_el_catalogo_real_no_incluye_cigarrillos_ni_categorias_vacias():
    _version, categorias, productos = servicio.leer_catalogo(RUTA_CATALOGO_INICIAL)

    nombres = " ".join(c.nombre.lower() for c in categorias)
    assert "cigarrillo" not in nombres and "tabaco" not in nombres
    usadas = {cat for _p, cat in productos}
    assert all(c.nombre in usadas for c in categorias)


def test_el_catalogo_real_se_siembra_en_una_instalacion_nueva(base_datos_temporal):
    assert servicio.sembrar_si_corresponde() is True

    assert _estado() == (10, 41, 0, 1)
    assert servicio.sembrar_si_corresponde() is False
    assert _estado() == (10, 41, 0, 1)


# ---------------------------------------------------------------------------
# Backup / restore conservan el marcador
# ---------------------------------------------------------------------------


def test_backup_y_restore_conservan_el_marcador_y_no_se_vuelve_a_sembrar(
    base_datos_temporal, tmp_path, monkeypatch
):
    servicio.sembrar_si_corresponde()
    dir_imagenes = tmp_path / "imagenes"
    dir_imagenes.mkdir()
    monkeypatch.setattr(modulo_backup, "RUTA_BASE_DATOS", base_datos_temporal)
    monkeypatch.setattr(modulo_backup, "DIRECTORIO_IMAGENES_PRODUCTOS", dir_imagenes)

    ruta_zip = modulo_backup.crear_backup(tmp_path / "backups", ControlEscrituras())
    extraido = modulo_restore.validar_y_extraer_backup(ruta_zip, tmp_path / "restore")

    conexion = sqlite3.connect(extraido / "kiosco.db")
    try:
        assert conexion.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conexion.execute("SELECT COUNT(*) FROM productos").fetchone()[0] == 41
    finally:
        conexion.close()

    # La base restaurada pasa a ser la activa: no se vuelve a sembrar.
    monkeypatch.setattr(modulo_conexion, "RUTA_BASE_DATOS", extraido / "kiosco.db")
    assert servicio.sembrar_si_corresponde() is False
    assert _estado() == (10, 41, 0, 1)
