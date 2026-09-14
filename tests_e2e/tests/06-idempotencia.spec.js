// @ts-check
// TEST 6 — IDEMPOTENCIA DESDE UI / REINTENTO (Fase 5E.2).
//
// No se inventa ninguna API paralela: se captura el cuerpo REAL que
// pos.js envía al hacer click real en "Cobrar" (items + tipo_pago +
// clave_idempotencia generada por la app), y se reenvía un POST idéntico
// con `page.request` (comparte cookies con la página real) simulando un
// reintento de red -- exactamente lo que haría el propio pos.js si la
// respuesta del primer intento se hubiera perdido (ver
// static/js/pos.js::cobrar(), rama `catch`). La cobertura exhaustiva de
// idempotencia (carreras concurrentes, claves reutilizadas con datos
// distintos, etc.) ya existe en pytest
// (tests/test_interfaces_web/test_ventas.py) y no se duplica acá: esto
// solo confirma que el mismo mecanismo funciona de punta a punta desde
// un navegador real.
const { test, expect } = require('@playwright/test');
const { loginViaUI, agregarPorCodigoBarras, seleccionarMedioDePago, leerStockDeCard, registrarListenersDeErrores, PRODUCTO_1 } = require('./helpers');

test('reintento de red real (misma clave de idempotencia) no duplica la venta', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page);

  const stockAntes = await leerStockDeCard(page, PRODUCTO_1.nombre);

  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await seleccionarMedioDePago(page, 'TARJETA'); // no requiere monto recibido

  const [requestOriginal, respuestaOriginal] = await Promise.all([
    page.waitForRequest((r) => r.url().includes('/api/ventas') && r.method() === 'POST'),
    page.waitForResponse((r) => r.url().includes('/api/ventas') && r.request().method() === 'POST'),
    page.locator('#boton-cobrar').click(),
  ]);
  expect(respuestaOriginal.status()).toBe(200);
  const cuerpoOriginal = requestOriginal.postData();
  const datosOriginal = await respuestaOriginal.json();

  // Reintento real: mismo cuerpo exacto (misma clave_idempotencia real
  // generada por pos.js), misma sesión.
  const respuestaRetry = await page.request.post('/api/ventas', {
    data: JSON.parse(cuerpoOriginal),
    headers: { 'Content-Type': 'application/json' },
  });
  expect(respuestaRetry.status()).toBe(200);
  const datosRetry = await respuestaRetry.json();

  // Misma venta devuelta -- no una nueva.
  expect(datosRetry.id).toBe(datosOriginal.id);

  // Stock descontado una sola vez, no dos.
  await page.goto('/ventas');
  const stockDespues = await leerStockDeCard(page, PRODUCTO_1.nombre);
  expect(stockDespues).toBe(stockAntes - 1);

  errores.afirmarSinErrores();
});
