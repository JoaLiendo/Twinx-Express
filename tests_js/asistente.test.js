// Twinx Asistente: helpers puros del panel de chat (V1.18).
const { test, describe } = require('node:test');
const assert = require('node:assert/strict');

const { esHrefSeguro, historialParaEnviar, clasificarEstado, bloquesDeTexto, etiquetaOrigen } = require('../interfaces/web/static/js/asistente.js');

describe('esHrefSeguro', () => {
  test('acepta solo rutas internas', () => {
    assert.equal(esHrefSeguro('/reportes?fecha_desde=2026-01-01'), true);
    assert.equal(esHrefSeguro('/ayuda#compras'), true);
    assert.equal(esHrefSeguro('//evil.example/x'), false);
    assert.equal(esHrefSeguro('https://evil.example'), false);
    assert.equal(esHrefSeguro('javascript:alert(1)'), false);
    assert.equal(esHrefSeguro(''), false);
    assert.equal(esHrefSeguro(null), false);
  });
});

describe('historialParaEnviar', () => {
  test('manda solo rol y texto de los últimos 10 mensajes válidos', () => {
    const mensajes = [];
    for (let i = 0; i < 15; i += 1) mensajes.push({ rol: i % 2 ? 'asistente' : 'usuario', texto: `m${i}`, acciones: [{ href: '/x' }] });
    mensajes.push({ rol: 'sistema', texto: 'no va' });
    mensajes.push({ rol: 'usuario', texto: '   ' });
    const enviado = historialParaEnviar(mensajes);
    assert.equal(enviado.length, 10);
    assert.deepEqual(Object.keys(enviado[0]), ['rol', 'texto']);
    assert.equal(enviado[enviado.length - 1].texto, 'm14');
  });

  test('recorta cada texto a 500 caracteres', () => {
    const [unico] = historialParaEnviar([{ rol: 'usuario', texto: 'a'.repeat(900) }]);
    assert.equal(unico.texto.length, 500);
  });
});

describe('clasificarEstado', () => {
  test('distingue IA conectada, IA configurada pero apagada y ayuda integrada', () => {
    assert.equal(clasificarEstado({ modo: 'ollama', disponible: true }), 'ia');
    assert.equal(clasificarEstado({ modo: 'ollama', disponible: false }), 'sin-ia');
    assert.equal(clasificarEstado({ modo: 'integrado', disponible: false }), 'integrado');
    assert.equal(clasificarEstado(null), 'desconocido');
  });
});

describe('bloquesDeTexto', () => {
  test('separa párrafos y listas numeradas o con guiones', () => {
    const bloques = bloquesDeTexto('Hay 2 productos:\n- Agua: 3 u.\n- Coca: 1 u.\nPasos:\n1. Abrí Caja\n2. Confirmá');
    assert.deepEqual(bloques, [
      { tipo: 'p', texto: 'Hay 2 productos:' },
      { tipo: 'ul', items: ['Agua: 3 u.', 'Coca: 1 u.'] },
      { tipo: 'p', texto: 'Pasos:' },
      { tipo: 'ol', items: ['Abrí Caja', 'Confirmá'] },
    ]);
  });

  test('no interpreta HTML: el texto queda tal cual', () => {
    assert.deepEqual(bloquesDeTexto('<b>x</b>'), [{ tipo: 'p', texto: '<b>x</b>' }]);
  });
});

describe('etiquetaOrigen', () => {
  test('nunca presenta la ayuda integrada como IA', () => {
    assert.equal(etiquetaOrigen({ origen: 'integrado' }), 'Ayuda integrada');
    assert.equal(etiquetaOrigen({ origen: 'ia_local', modelo: 'llama3.2:3b' }), 'IA local · llama3.2:3b');
  });
});
