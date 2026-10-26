// V1.14-F: tests puros de node:test para las funciones sin DOM de
// interfaces/web/static/js/credito_pos.js (la parte visual la cubre el E2E).
//
// Ejecutar desde la raíz: node --test "tests_js/*.test.js"

const { test, describe } = require('node:test');
const assert = require('node:assert/strict');

const { evaluarVentaACredito, datosComprador } = require('../interfaces/web/static/js/credito_pos.js');

const SITUACION = { bloqueo: null, credito_habilitado: true, disponible_centavos: 3000, es_empresa: false };

describe('evaluarVentaACredito', () => {
  test('todo a cuenta dentro del disponible', () => {
    assert.deepEqual(evaluarVentaACredito(SITUACION, 3000, 0), { ok: true, mensaje: '', creditoCentavos: 3000 });
  });

  test('venta mixta: solo el resto cuenta contra el disponible', () => {
    const resultado = evaluarVentaACredito(SITUACION, 5000, 2000);
    assert.equal(resultado.ok, true);
    assert.equal(resultado.creditoCentavos, 3000);
  });

  test('un centavo por encima del disponible se avisa', () => {
    assert.equal(evaluarVentaACredito(SITUACION, 3001, 0).ok, false);
  });

  test('si paga todo en efectivo no es una venta a cuenta', () => {
    assert.match(evaluarVentaACredito(SITUACION, 5000, 5000).mensaje, /Efectivo/);
    assert.equal(evaluarVentaACredito(SITUACION, 5000, 6000).ok, false);
  });

  test('el bloqueo del servidor gana sobre todo lo demás', () => {
    const bloqueado = { ...SITUACION, bloqueo: 'Crédito cerrado por deuda crítica.' };
    assert.deepEqual(evaluarVentaACredito(bloqueado, 1, 0).mensaje, 'Crédito cerrado por deuda crítica.');
  });

  test('sin crédito habilitado o sin datos todavía no se confirma', () => {
    assert.equal(evaluarVentaACredito({ ...SITUACION, credito_habilitado: false }, 1, 0).ok, false);
    assert.equal(evaluarVentaACredito(null, 1, 0).ok, false);
  });
});

describe('datosComprador', () => {
  test('una persona no manda comprador', () => {
    assert.deepEqual(datosComprador(false, null), {});
  });

  test('autorizado habitual', () => {
    assert.deepEqual(datosComprador(true, { autorizadoId: 7 }), { comprador_autorizado_id: 7 });
  });

  test('excepcional completo, recortado', () => {
    assert.deepEqual(datosComprador(true, { nombre: ' Pedro ', metodo: 'WHATSAPP', detalle: ' msj ' }), {
      comprador_excepcional_nombre: 'Pedro',
      comprador_excepcional_metodo: 'WHATSAPP',
      comprador_excepcional_detalle: 'msj',
    });
  });

  test('empresa sin elegir o excepcional incompleto: falta el comprador', () => {
    assert.equal(datosComprador(true, null), null);
    assert.equal(datosComprador(true, { nombre: 'Pedro', metodo: '', detalle: 'x' }), null);
    assert.equal(datosComprador(true, { nombre: '', metodo: 'EMAIL', detalle: 'x' }), null);
  });
});
