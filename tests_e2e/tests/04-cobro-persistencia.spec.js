// @ts-check
// TEST 4 — COBRO + PERSISTENCIA (Fase 5E.2).
//
// Este test SÍ toca la DB (registra una venta real y descuenta stock).
// No asume ningún valor absoluto de stock/ventas previas: lee el stock
// ANTES por su propia cuenta y compara contra DESPUÉS, así conviven sin
// fragilidad con otros tests que compartan la misma DB temporal dentro
// de esta corrida (ver playwright.config.js: `workers: 1` evita que dos
// tests lean/escriban stock al mismo tiempo).
const { test, expect } = require('@playwright/test');
const {
  loginViaUI,
  agregarPorCodigoBarras,
  seleccionarMedioDePago,
  leerStockDeCard,
  registrarListenersDeErrores,
  PRODUCTO_1,
} = require('./helpers');

test('cobro real desde la UI: acciones post-venta, ticket persistido y stock descontado', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page);

  const stockAntes = await leerStockDeCard(page, PRODUCTO_1.nombre);

  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await seleccionarMedioDePago(page, 'EFECTIVO');
  await page.getByLabel('Monto recibido').fill('2.50'); // exacto: vuelto $0.00
  await expect(page.locator('#boton-cobrar')).toBeEnabled();

  const [respuestaVenta] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/ventas') && r.request().method() === 'POST'),
    page.locator('#boton-cobrar').click(),
  ]);
  expect(respuestaVenta.status()).toBe(200);
  const datosVenta = await respuestaVenta.json();
  const ventaId = datosVenta.id;
  expect(ventaId).toBeGreaterThan(0);

  // Recibido/vuelto NUNCA viajaron en el POST -- Fase 5C: se verifica
  // directamente sobre la respuesta real de la API, no sobre un supuesto.
  expect(datosVenta).not.toHaveProperty('monto_recibido_centavos');
  expect(datosVenta).not.toHaveProperty('vuelto_centavos');

  // Acciones post-venta (Fase 5D) visibles en la UI real.
  const linkImprimir = page.getByRole('link', { name: 'Imprimir ticket' });
  await expect(linkImprimir).toBeVisible();
  await expect(page.getByRole('button', { name: 'Nueva venta' })).toBeVisible();

  // El id de venta se toma del DOM real (href del link), nunca de un
  // endpoint de test.
  const hrefTicket = await linkImprimir.getAttribute('href');
  expect(hrefTicket).toBe(`/ventas/${ventaId}/ticket`);

  await page.goto(hrefTicket);
  await expect(page.getByText(`Ticket #${ventaId}`)).toBeVisible();
  const filaTicket = page.locator('table tbody tr');
  await expect(filaTicket).toContainText(PRODUCTO_1.nombre);
  await expect(filaTicket).toContainText('2.50'); // precio unitario Y subtotal (cantidad 1)
  await expect(page.locator('.total')).toContainText('2.50');
  await expect(page.getByText('Medio de pago: EFECTIVO')).toBeVisible();

  // Recibido/vuelto tampoco aparecen en el ticket (Fase 5C/5D).
  const textoTicket = (await page.textContent('body')).toLowerCase();
  expect(textoTicket).not.toContain('recibido');
  expect(textoTicket).not.toContain('vuelto');

  // Stock descontado exactamente en 1 unidad: se relee desde /ventas
  // (server real, no un endpoint de test) y se compara contra el valor
  // leído al principio de este mismo test.
  await page.goto('/ventas');
  const stockDespues = await leerStockDeCard(page, PRODUCTO_1.nombre);
  expect(stockDespues).toBe(stockAntes - 1);

  errores.afirmarSinErrores();
});
