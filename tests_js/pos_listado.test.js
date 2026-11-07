// Selección manual del POS: búsqueda y filtros de la lista de productos.
const { test, describe } = require('node:test');
const assert = require('node:assert/strict');

const {
  normalizarTexto,
  estadoDeStock,
  coincide,
  elegirParaEnter,
  moverActivo,
  LIMITE_VISIBLE,
  textoContador,
} = require('../interfaces/web/static/js/pos_listado.js');

// Categorías: 1 = Bebidas, 2 = Bebidas › Gaseosas, 3 = Golosinas.
const COCA = { nombre: 'Coca-Cola 500 ml', codigo: '7790895000997', categorias: ['1', '2'], stock: 12, stockMinimo: 4 };
const AGUA = { nombre: 'Agua mineral 1,5 L', codigo: '7798062540017', categorias: ['1'], stock: 0, stockMinimo: 3 };
const ALFAJOR = { nombre: 'Alfajor Café Triple', codigo: '7790580123456', categorias: ['3'], stock: 2, stockMinimo: 5 };
const SUELTO = { nombre: 'Encendedor', codigo: 'ENC-01', categorias: [], stock: 8, stockMinimo: 0 };
const TODOS = [COCA, AGUA, ALFAJOR, SUELTO];

const SIN_FILTROS = { texto: '', categoriaId: '', stock: '' };

function filtrar(filtros) {
  return TODOS.filter((producto) => coincide(producto, { ...SIN_FILTROS, ...filtros }));
}

describe('normalizarTexto', () => {
  test('ignora mayúsculas, tildes y espacios de los extremos', () => {
    assert.equal(normalizarTexto('  Café ÁÉÍÓÚ ñ '), 'cafe aeiou n');
  });

  test('tolera valores vacíos', () => {
    assert.equal(normalizarTexto(undefined), '');
    assert.equal(normalizarTexto(null), '');
  });
});

describe('estadoDeStock (misma regla que Producto.tiene_stock_critico)', () => {
  test('sin stock, stock bajo y disponible', () => {
    assert.equal(estadoDeStock(0, 3), 'sin_stock');
    assert.equal(estadoDeStock(2, 5), 'bajo');
    assert.equal(estadoDeStock(5, 5), 'bajo');
    assert.equal(estadoDeStock(6, 5), 'disponible');
    assert.equal(estadoDeStock(8, 0), 'disponible');
  });
});

describe('búsqueda', () => {
  test('sin filtros muestra todo el catálogo', () => {
    assert.deepEqual(filtrar({}), TODOS);
  });

  test('por nombre parcial, sin distinguir mayúsculas ni tildes', () => {
    assert.deepEqual(filtrar({ texto: 'cafe' }), [ALFAJOR]);
    assert.deepEqual(filtrar({ texto: 'COCA' }), [COCA]);
  });

  test('por código (alfanumérico) parcial', () => {
    assert.deepEqual(filtrar({ texto: 'enc-0' }), [SUELTO]);
  });

  test('por código de barras completo o parcial', () => {
    assert.deepEqual(filtrar({ texto: '7798062540017' }), [AGUA]);
    assert.deepEqual(filtrar({ texto: '0895' }), [COCA]);
  });

  test('varias palabras: todas tienen que aparecer', () => {
    assert.deepEqual(filtrar({ texto: 'coca 500' }), [COCA]);
    assert.deepEqual(filtrar({ texto: 'coca 1,5' }), []);
  });

  test('sin coincidencias devuelve vacío', () => {
    assert.deepEqual(filtrar({ texto: 'inexistente' }), []);
  });
});

describe('filtros', () => {
  test('categoría principal incluye sus subcategorías', () => {
    assert.deepEqual(filtrar({ categoriaId: '1' }), [COCA, AGUA]);
  });

  test('subcategoría específica', () => {
    assert.deepEqual(filtrar({ categoriaId: '2' }), [COCA]);
  });

  test('con stock excluye solo los productos en 0', () => {
    assert.deepEqual(filtrar({ stock: 'con_stock' }), [COCA, ALFAJOR, SUELTO]);
  });

  test('sin stock', () => {
    assert.deepEqual(filtrar({ stock: 'sin_stock' }), [AGUA]);
  });

  test('stock bajo', () => {
    assert.deepEqual(filtrar({ stock: 'bajo' }), [ALFAJOR]);
  });

  test('búsqueda + categoría se combinan', () => {
    assert.deepEqual(filtrar({ texto: 'a', categoriaId: '1' }), [COCA, AGUA]);
    assert.deepEqual(filtrar({ texto: 'agua', categoriaId: '3' }), []);
  });

  test('búsqueda + categoría + stock se combinan', () => {
    assert.deepEqual(filtrar({ texto: 'a', categoriaId: '1', stock: 'con_stock' }), [COCA]);
  });
});

describe('elegirParaEnter', () => {
  test('un código de barras exacto se elige aunque otro filtro lo oculte', () => {
    assert.equal(elegirParaEnter(TODOS, [], '7790580123456'), ALFAJOR);
  });

  test('un único resultado visible se elige', () => {
    assert.equal(elegirParaEnter(TODOS, [COCA], 'coca'), COCA);
  });

  test('varios resultados son ambiguos: no elige', () => {
    assert.equal(elegirParaEnter(TODOS, [COCA, AGUA], 'a'), null);
  });

  test('sin resultados no elige', () => {
    assert.equal(elegirParaEnter(TODOS, [], 'nada'), null);
  });
});

describe('textoContador', () => {
  test('singular y plural', () => {
    assert.equal(textoContador(1), '1 producto encontrado');
    assert.equal(textoContador(0), '0 productos encontrados');
    assert.equal(textoContador(41), '41 productos encontrados');
  });
});

describe('moverActivo (flechas del teclado, V1.17-B)', () => {
  // COCA (12), AGUA (0: no vendible), ALFAJOR (2), SUELTO (8).
  test('desde ninguno, bajar elige el primero vendible y subir el último', () => {
    assert.equal(moverActivo(TODOS, -1, 1), 0);
    assert.equal(moverActivo(TODOS, -1, -1), 3);
  });

  test('salta los productos sin stock y se detiene en los extremos', () => {
    assert.equal(moverActivo(TODOS, 0, 1), 2);
    assert.equal(moverActivo(TODOS, 2, 1), 3);
    assert.equal(moverActivo(TODOS, 3, 1), 3);
    assert.equal(moverActivo(TODOS, 2, -1), 0);
    assert.equal(moverActivo(TODOS, 0, -1), 0);
  });

  test('sin filas vendibles no hay activo', () => {
    assert.equal(moverActivo([AGUA], -1, 1), -1);
    assert.equal(moverActivo([], -1, 1), -1);
  });

  test('no pasa de la última fila mostrada aunque haya más resultados ocultos', () => {
    const muchos = Array.from({ length: LIMITE_VISIBLE + 5 }, (_, i) => ({ ...COCA, codigo: String(i) }));
    assert.equal(moverActivo(muchos, LIMITE_VISIBLE - 1, 1), LIMITE_VISIBLE - 1);
    assert.equal(moverActivo(muchos, -1, -1), LIMITE_VISIBLE - 1);
  });
});
