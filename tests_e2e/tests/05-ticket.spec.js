// @ts-check
// TEST 5 — TICKET (Fase 5E.2). Arma su propia venta (no reutiliza la de
// otro test) con el Producto 2, para no depender del orden de ejecución.
const { test, expect } = require('@playwright/test');
const { realizarVentaSimple, registrarListenersDeErrores, PRODUCTO_2 } = require('./helpers');

test('ticket: datos persistidos, estructura de impresión, sin auto-print', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);

  const { ventaId } = await realizarVentaSimple(page, {
    codigoBarras: PRODUCTO_2.codigoBarras,
    tipoPago: 'TARJETA',
  });

  // Interceptar window.print ANTES de navegar al ticket, para comprobar
  // que nunca se dispara solo por cargar la página (Fase 5D: "no
  // imprimir automáticamente"). No modifica producción: es una función
  // reemplazada dentro del contexto aislado del navegador de este test.
  await page.addInitScript(() => {
    // @ts-ignore
    window.__printLlamado = false;
    window.print = () => {
      // @ts-ignore
      window.__printLlamado = true;
    };
  });

  await page.goto(`/ventas/${ventaId}/ticket`);

  await expect(page.getByText(`Ticket #${ventaId}`)).toBeVisible();
  const filaTicket = page.locator('table tbody tr');
  await expect(filaTicket).toContainText(PRODUCTO_2.nombre);
  await expect(filaTicket).toContainText('5.00'); // precio unitario y subtotal (cantidad 1)
  await expect(page.locator('.total')).toContainText('5.00');
  await expect(page.getByText('Medio de pago: TARJETA')).toBeVisible();

  // Estructura preparada para impresión de ~80mm (Fase 5D): presente en
  // el <style> real de la página, no en un mock.
  const estilos = await page.locator('style').innerHTML();
  expect(estilos).toContain('@page');
  expect(estilos).toContain('80mm');
  expect(estilos).toContain('@media print');

  // No se llamó a print() por el solo hecho de cargar la página.
  const printLlamado = await page.evaluate(() => /** @type {any} */ (window).__printLlamado);
  expect(printLlamado).toBe(false);

  errores.afirmarSinErrores();
});
