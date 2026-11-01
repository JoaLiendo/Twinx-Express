// V1.16: estado fiscal que muestra el POS después de registrar una venta.
const { test, describe } = require('node:test');
const assert = require('node:assert/strict');

const { describirResultado } = require('../interfaces/web/static/js/fiscal_pos.js');

describe('describirResultado', () => {
  test('autorizada ofrece imprimir la factura', () => {
    const r = describirResultado(true, { estado: 'AUTORIZADO', autorizado: true, tipo: 'Factura B', numero: '00003-00000001', comprobante_id: 7 });
    assert.equal(r.imprimir, 7);
    assert.match(r.texto, /Factura B 00003-00000001 AUTORIZADA/);
  });

  test('pendiente, incierta y rechazada no ofrecen factura', () => {
    for (const estado of ['PENDIENTE', 'INCIERTO', 'RECHAZADO', 'ENVIANDO']) {
      const r = describirResultado(true, { estado, autorizado: false, etiqueta: estado, errores: [] });
      assert.equal(r.imprimir, null);
      assert.match(r.texto, /^Venta registrada/);
    }
  });

  test('rechazo muestra los motivos de ARCA', () => {
    const r = describirResultado(true, { estado: 'RECHAZADO', autorizado: false, errores: ['10013: Documento inválido'] });
    assert.deepEqual(r.motivos, ['10013: Documento inválido']);
  });

  test('bloqueo por datos faltantes muestra qué completar', () => {
    const r = describirResultado(false, { codigo: 'FISCALIZACION_BLOQUEADA', error: 'x', motivos: ['Clasificá el IVA de «Chicle».'] });
    assert.equal(r.imprimir, null);
    assert.deepEqual(r.motivos, ['Clasificá el IVA de «Chicle».']);
  });

  test('ARCA no disponible: la venta sigue registrada', () => {
    const r = describirResultado(false, { codigo: 'FISCALIZACION_NO_DISPONIBLE', error: 'caído' });
    assert.match(r.texto, /Venta registrada · Fiscalización no disponible/);
  });

  test('respuesta sin JSON', () => {
    const r = describirResultado(false, null);
    assert.equal(r.imprimir, null);
    assert.match(r.texto, /^Venta registrada/);
  });
});
