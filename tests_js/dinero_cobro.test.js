// Fase 5C: tests puros de node:test + node:assert para
// interfaces/web/static/js/dinero_cobro.js -- sin Jest/Vitest/bundlers,
// solo el runner de pruebas nativo de Node (ver diseño auditado de 5C).
//
// Ejecutar desde stock_app/: node --test tests_js/

const { test, describe } = require('node:test');
const assert = require('node:assert/strict');

const { textoACentavos, calcularResultadoCobroEfectivo } = require(
  '../interfaces/web/static/js/dinero_cobro.js'
);

describe('textoACentavos', () => {
  test('vacío', () => {
    assert.deepEqual(textoACentavos(''), { ok: false, motivo: 'vacio' });
    assert.deepEqual(textoACentavos('   '), { ok: false, motivo: 'vacio' });
  });

  test('inválido (letras, símbolos)', () => {
    assert.equal(textoACentavos('abc').ok, false);
    assert.equal(textoACentavos('abc').motivo, 'formato');
    assert.equal(textoACentavos('$100').ok, false);
    assert.equal(textoACentavos('10a0').ok, false);
  });

  test('0 (cero exacto)', () => {
    assert.deepEqual(textoACentavos('0'), { ok: true, centavos: 0 });
    assert.deepEqual(textoACentavos('0.00'), { ok: true, centavos: 0 });
  });

  test('entero sin decimales', () => {
    assert.deepEqual(textoACentavos('1000'), { ok: true, centavos: 100000 });
  });

  test('1 decimal', () => {
    assert.deepEqual(textoACentavos('1000.5'), { ok: true, centavos: 100050 });
  });

  test('2 decimales', () => {
    assert.deepEqual(textoACentavos('1000.50'), { ok: true, centavos: 100050 });
  });

  test('punto decimal', () => {
    assert.deepEqual(textoACentavos('99.99'), { ok: true, centavos: 9999 });
  });

  test('coma decimal', () => {
    assert.deepEqual(textoACentavos('99,99'), { ok: true, centavos: 9999 });
  });

  test('coma y punto dan el mismo resultado', () => {
    assert.deepEqual(textoACentavos('150,5'), textoACentavos('150.5'));
  });

  test('más de 2 decimales se rechaza (no se redondea)', () => {
    const resultado = textoACentavos('100.999');
    assert.equal(resultado.ok, false);
    assert.equal(resultado.motivo, 'formato');
  });

  test('monto negativo se rechaza', () => {
    const resultado = textoACentavos('-50');
    assert.equal(resultado.ok, false);
    assert.equal(resultado.motivo, 'formato');
  });

  test('separador de miles se rechaza explícitamente (sin miles en esta fase)', () => {
    const resultado = textoACentavos('1.000,00');
    assert.equal(resultado.ok, false);
    assert.equal(resultado.motivo, 'formato');
  });

  test('separador de miles estilo "1,000.00" también se rechaza', () => {
    const resultado = textoACentavos('1,000.00');
    assert.equal(resultado.ok, false);
    assert.equal(resultado.motivo, 'formato');
  });

  test('monto demasiado grande (fuera de rango entero seguro) se rechaza', () => {
    // 900719925474099 * 100 excede Number.MAX_SAFE_INTEGER.
    const resultado = textoACentavos('900719925474099');
    assert.equal(resultado.ok, false);
    assert.equal(resultado.motivo, 'demasiado_grande');
  });

  test('monto extremadamente largo (colapsa a notación exponencial) se rechaza', () => {
    const resultado = textoACentavos('9'.repeat(40));
    assert.equal(resultado.ok, false);
    assert.equal(resultado.motivo, 'demasiado_grande');
  });

  test('monto en el borde del rango seguro se acepta', () => {
    // 90071992547409 * 100 == 9007199254740900 <= Number.MAX_SAFE_INTEGER.
    const resultado = textoACentavos('90071992547409');
    assert.equal(resultado.ok, true);
    assert.equal(Number.isSafeInteger(resultado.centavos), true);
  });
});

describe('calcularResultadoCobroEfectivo', () => {
  test('monto menor al total: insuficiente', () => {
    const resultado = calcularResultadoCobroEfectivo(1000, 500);
    assert.equal(resultado.ok, true);
    assert.equal(resultado.estado, 'insuficiente');
    assert.equal(resultado.diferenciaCentavos, -500);
  });

  test('monto igual al total: exacto', () => {
    const resultado = calcularResultadoCobroEfectivo(1000, 1000);
    assert.equal(resultado.ok, true);
    assert.equal(resultado.estado, 'exacto');
    assert.equal(resultado.diferenciaCentavos, 0);
  });

  test('monto mayor al total: con vuelto', () => {
    const resultado = calcularResultadoCobroEfectivo(1000, 1500);
    assert.equal(resultado.ok, true);
    assert.equal(resultado.estado, 'con_vuelto');
    assert.equal(resultado.diferenciaCentavos, 500);
  });
});

describe('calcularResultadoCobroEfectivo - validación de argumentos', () => {
  test('rechaza total no entero seguro (NaN)', () => {
    assert.deepEqual(calcularResultadoCobroEfectivo(NaN, 500), { ok: false });
  });

  test('rechaza monto recibido no entero seguro (NaN)', () => {
    assert.deepEqual(calcularResultadoCobroEfectivo(1000, NaN), { ok: false });
  });

  test('rechaza valores infinitos', () => {
    assert.deepEqual(calcularResultadoCobroEfectivo(Infinity, 500), { ok: false });
    assert.deepEqual(calcularResultadoCobroEfectivo(1000, Infinity), { ok: false });
  });

  test('rechaza montos fuera del rango entero seguro', () => {
    const masAlla = Number.MAX_SAFE_INTEGER + 10;
    assert.deepEqual(calcularResultadoCobroEfectivo(1000, masAlla), { ok: false });
  });
});
