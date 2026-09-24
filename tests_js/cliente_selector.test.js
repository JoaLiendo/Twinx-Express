// 021: tests puros de node:test para las funciones sin DOM de
// interfaces/web/static/js/cliente_selector.js (el resto del módulo es lógica visual que cubre el E2E).
//
// Ejecutar desde stock_app/: node --test "tests_js/*.test.js"

const { test, describe } = require('node:test');
const assert = require('node:assert/strict');

const { textoDeCliente, saldoEstimadoCentavos } = require('../interfaces/web/static/js/cliente_selector.js');

describe('textoDeCliente', () => {
  test('nombre y teléfono', () => {
    assert.equal(textoDeCliente({ nombre: 'Ana', telefono: '1155551234' }), 'Ana · 1155551234');
  });

  test('sin teléfono muestra solo el nombre', () => {
    assert.equal(textoDeCliente({ nombre: 'Ana', telefono: null }), 'Ana');
    assert.equal(textoDeCliente({ nombre: 'Ana', telefono: '' }), 'Ana');
  });

  test('no interpreta el nombre: devuelve texto plano, nunca marcado', () => {
    const nombre = '<img src=x onerror=alert(1)>';
    assert.equal(textoDeCliente({ nombre, telefono: null }), nombre);
  });
});

describe('saldoEstimadoCentavos', () => {
  test('suma el total del carrito al saldo actual', () => {
    assert.equal(saldoEstimadoCentavos(5000, 2500), 7500);
    assert.equal(saldoEstimadoCentavos(0, 199), 199);
  });

  test('un total negativo o cero no reduce el saldo', () => {
    assert.equal(saldoEstimadoCentavos(5000, 0), 5000);
    assert.equal(saldoEstimadoCentavos(5000, -100), 5000);
  });
});

// --- Estado del selector (F1 de la auditoría de 021): elegir, reiniciar y volver -----------------------
//
// El selector es lógica visual sobre el DOM. Node no trae DOM, así que se usa uno simulado mínimo
// (solo lo que cliente_selector.js toca). La integración con pos.js -- cambiar el medio de pago a
// TARJETA y volver, y el cuerpo real de POST /api/ventas -- la cubre el E2E (11-cuenta-corriente).

const { iniciar } = require('../interfaces/web/static/js/cliente_selector.js');

function crearElemento() {
  const elemento = {
    children: [],
    listeners: {},
    value: '',
    type: '',
    className: '',
    _texto: '',
    _clases: new Set(),
    classList: {
      add: (...c) => c.forEach((x) => elemento._clases.add(x)),
      remove: (...c) => c.forEach((x) => elemento._clases.delete(x)),
      contains: (c) => elemento._clases.has(c),
    },
    get textContent() {
      return elemento._texto;
    },
    set textContent(valor) {
      elemento._texto = valor;
      if (valor === '') elemento.children = []; // igual que el DOM real: asignar '' vacía los hijos
    },
    appendChild(hijo) {
      elemento.children.push(hijo);
      return hijo;
    },
    addEventListener(evento, funcion) {
      elemento.listeners[evento] = funcion;
    },
    focus() {},
  };
  return elemento;
}

function crearContenedorFalso() {
  const piezas = {
    '[data-cliente-busqueda]': crearElemento(),
    '[data-cliente-resultados]': crearElemento(),
    '[data-cliente-mensaje]': crearElemento(),
    '[data-cliente-seleccionado]': crearElemento(),
    '[data-cliente-nombre]': crearElemento(),
    '[data-cliente-telefono]': crearElemento(),
    '[data-cliente-saldo]': crearElemento(),
    '[data-cliente-saldo-estimado]': crearElemento(),
    '[data-cliente-quitar]': crearElemento(),
    '[data-cliente-zona-busqueda]': crearElemento(),
  };
  piezas['[data-cliente-seleccionado]']._clases.add('hidden'); // como en pos.html: arranca oculto
  piezas['[data-cliente-resultados]']._clases.add('hidden');
  return { piezas, querySelector: (selector) => piezas[selector] };
}

const CLIENTES_DEL_SERVIDOR = [
  { id: 1, nombre: 'Ana', telefono: '1155550001', saldo_centavos: 5000 },
  { id: 2, nombre: 'Beto', telefono: null, saldo_centavos: 0 },
];

async function esperarBusqueda() {
  // `buscar` es async (fetch + json): dos vueltas del bucle de microtareas alcanzan con el fetch simulado.
  await new Promise((resolver) => setImmediate(resolver));
}

describe('estado del selector al elegir, reiniciar y volver', () => {
  let contenedor;
  let selector;
  let pedidos;
  const cambios = [];

  function prepararEntorno() {
    pedidos = [];
    cambios.length = 0;
    global.document = { createElement: () => crearElemento() };
    global.fetch = async (url) => {
      pedidos.push(url);
      return { ok: true, json: async () => CLIENTES_DEL_SERVIDOR };
    };
    contenedor = crearContenedorFalso();
    selector = iniciar({
      contenedor,
      endpoint: '/api/clientes/buscar',
      formatearCentavos: (c) => `$${(c / 100).toFixed(2)}`,
      alCambiar: (cliente) => cambios.push(cliente),
    });
  }

  async function elegirALaPrimeraClienta() {
    selector.alMostrar();
    await esperarBusqueda();
    const lista = contenedor.piezas['[data-cliente-resultados]'];
    assert.equal(lista.children.length, CLIENTES_DEL_SERVIDOR.length);
    lista.children[0].children[0].listeners.click(); // <li><button> de la primera clienta
  }

  test('a) elegir un cliente lo deja seleccionado y visible', async () => {
    prepararEntorno();

    await elegirALaPrimeraClienta();

    assert.equal(selector.obtener().id, 1);
    assert.equal(contenedor.piezas['[data-cliente-nombre]'].textContent, 'Ana');
    assert.equal(contenedor.piezas['[data-cliente-saldo]'].textContent, 'Saldo actual: $50.00');
    assert.equal(contenedor.piezas['[data-cliente-seleccionado]'].classList.contains('hidden'), false);
    assert.equal(contenedor.piezas['[data-cliente-zona-busqueda]'].classList.contains('hidden'), true);
  });

  test('c) reiniciar (al dejar CUENTA_CORRIENTE) deja el selector vacío, sin datos viejos', async () => {
    prepararEntorno();
    await elegirALaPrimeraClienta();

    selector.reiniciar();

    assert.equal(selector.obtener(), null);
    for (const gancho of ['[data-cliente-nombre]', '[data-cliente-telefono]', '[data-cliente-saldo]', '[data-cliente-saldo-estimado]']) {
      assert.equal(contenedor.piezas[gancho].textContent, '', gancho);
    }
    assert.equal(contenedor.piezas['[data-cliente-seleccionado]'].classList.contains('hidden'), true);
    assert.equal(contenedor.piezas['[data-cliente-zona-busqueda]'].classList.contains('hidden'), false);
    assert.equal(contenedor.piezas['[data-cliente-busqueda]'].value, '');
  });

  test('d/e) al volver no reaparece el cliente anterior: se vuelve a buscar y hay que elegir de nuevo', async () => {
    prepararEntorno();
    await elegirALaPrimeraClienta();
    assert.equal(pedidos.length, 1);
    selector.reiniciar();

    selector.alMostrar(); // pos.js lo llama al volver a CUENTA_CORRIENTE
    await esperarBusqueda();

    assert.equal(selector.obtener(), null);
    assert.equal(contenedor.piezas['[data-cliente-nombre]'].textContent, '');
    assert.equal(contenedor.piezas['[data-cliente-seleccionado]'].classList.contains('hidden'), true);
    assert.equal(pedidos.length, 2); // saldos frescos del servidor, no los que quedaron en memoria
    assert.equal(contenedor.piezas['[data-cliente-resultados]'].children.length, CLIENTES_DEL_SERVIDOR.length);
  });

  test('alMostrar no vuelve a buscar mientras hay un cliente elegido (no pisa la selección)', async () => {
    prepararEntorno();
    await elegirALaPrimeraClienta();

    selector.alMostrar();
    await esperarBusqueda();

    assert.equal(pedidos.length, 1);
    assert.equal(selector.obtener().id, 1);
  });

  test('reiniciar sin cliente elegido es inofensivo', () => {
    prepararEntorno();

    selector.reiniciar();

    assert.equal(selector.obtener(), null);
  });
});
