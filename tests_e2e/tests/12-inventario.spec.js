// @ts-check
// TEST 12 — INVENTARIO FÍSICO DE PUNTA A PUNTA (V1.4).
//
// Con un navegador real y la app real: crear un inventario, contar a ciegas, revisar, confirmar y ver el
// stock ajustado; y el rechazo cuando el stock cambió después de contar (una venta). Las reglas de
// negocio y la concurrencia están cubiertas exhaustivamente en pytest (test_inventario.py,
// test_concurrencia_inventario.py, test_migracion_022.py); esto confirma que el mecanismo funciona
// desde el navegador. El servidor de pruebas solo tiene un OWNER, así que el rol CASHIER (contar y no
// confirmar) se cubre en pytest.
const { test, expect } = require('@playwright/test');
const { loginViaUI, leerStockDeCard, registrarListenersDeErrores, PRODUCTO_1, PRODUCTO_2 } = require('./helpers');

/** Crea un inventario de selección manual con un solo producto y espera a estar en su detalle. */
async function iniciarInventarioManual(page, nombreProducto) {
  await page.goto('/inventario/nuevo');
  await page.getByText('Elegir productos').click();
  await page
    .locator('label[data-fila-filtrable]', { hasText: nombreProducto })
    .locator('input[type="checkbox"]')
    .check();
  await page.getByRole('button', { name: 'Iniciar inventario' }).click();
  await page.waitForURL(/\/inventario\/\d+/);
}

test('inventario: crear, contar a ciegas, confirmar y ver el stock ajustado', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  page.on('dialog', (dialogo) => dialogo.accept());
  await loginViaUI(page, { irA: '/ventas' });
  const stockAntes = await leerStockDeCard(page, PRODUCTO_2.nombre);

  await iniciarInventarioManual(page, PRODUCTO_2.nombre);

  // Conteo a ciegas: la pantalla de conteo nunca muestra el stock del sistema.
  await page.goto('/inventario/conteo');
  const fila = page.locator('#lista-conteo li', { hasText: PRODUCTO_2.nombre });
  await expect(fila).toBeVisible();
  await expect(fila.locator('[data-estado]')).toHaveText('Sin contar');
  await expect(page.locator('#lista-conteo')).not.toContainText(`Esperado`);
  await expect(page.locator('#progreso-conteo')).toHaveText('0 de 1 contados');

  // Se cuenta una unidad más que el stock del sistema (diferencia +1), sin recargar la página.
  const contado = stockAntes + 1;
  await fila.locator('input[name="cantidad"]').fill(String(contado));
  await fila.getByRole('button', { name: 'Guardar' }).click();
  await expect(fila.locator('[data-estado]')).toHaveText(`Contado: ${contado}`);
  await expect(page.locator('#progreso-conteo')).toHaveText('1 de 1 contados');

  // Revisión (solo OWNER): esperado, contado y diferencia.
  await page.getByRole('link', { name: /Revisar y confirmar/ }).click();
  await page.waitForURL(/\/inventario\/\d+/);
  const lineaRevision = page.locator('#tabla-lineas-inventario tr', { hasText: PRODUCTO_2.nombre });
  await expect(lineaRevision.locator('[data-esperado]')).toHaveText(String(stockAntes));
  await expect(lineaRevision.locator('[data-contado]')).toHaveText(String(contado));
  await expect(lineaRevision.locator('[data-diferencia]')).toHaveText('+1');

  // Confirmación: el stock queda ajustado y el inventario cerrado.
  await page.getByRole('button', { name: 'Confirmar inventario' }).click();
  await expect(page.locator('#form-confirmar-inventario')).toHaveCount(0);
  await expect(page.getByText('Confirmado').first()).toBeVisible();
  await page.goto('/ventas');
  expect(await leerStockDeCard(page, PRODUCTO_2.nombre)).toBe(contado);

  errores.afirmarSinErrores();
});

test('inventario: si el stock cambió después de contar, no se confirma y se puede cancelar', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  page.on('dialog', (dialogo) => dialogo.accept());
  await loginViaUI(page, { irA: '/ventas' });
  const idProducto = Number(
    await page.locator(`[data-producto-card][data-nombre="${PRODUCTO_1.nombre}"]`).getAttribute('data-id'),
  );
  const stockAntes = await leerStockDeCard(page, PRODUCTO_1.nombre);

  await iniciarInventarioManual(page, PRODUCTO_1.nombre);
  await page.goto('/inventario/conteo');
  const fila = page.locator('#lista-conteo li', { hasText: PRODUCTO_1.nombre });
  await fila.locator('input[name="cantidad"]').fill(String(stockAntes));
  await fila.getByRole('button', { name: 'Guardar' }).click();
  await expect(fila.locator('[data-estado]')).toHaveText(`Contado: ${stockAntes}`);

  // Una venta después del conteo cambia el stock.
  const venta = await page.request.post('/api/ventas', {
    data: {
      items: [{ producto_id: idProducto, cantidad: 1 }],
      tipo_pago: 'EFECTIVO',
      clave_idempotencia: `e2e-inventario-${Date.now()}`,
    },
  });
  expect(venta.ok()).toBeTruthy();

  await page.getByRole('link', { name: /Revisar y confirmar/ }).click();
  await page.waitForURL(/\/inventario\/\d+/);
  await expect(page.locator('#aviso-desactualizadas')).toContainText(PRODUCTO_1.nombre);
  await page.getByRole('button', { name: 'Confirmar inventario' }).click();
  await expect(page.locator('#toast-container')).toContainText('cambió después del conteo');
  await expect(page.locator('#form-confirmar-inventario')).toBeVisible(); // sigue abierto

  // Se cancela: el stock refleja solo la venta.
  await page.getByRole('button', { name: 'Cancelar inventario' }).click();
  await page.waitForURL(/\/inventario(\?.*)?$/);
  await page.goto('/ventas');
  expect(await leerStockDeCard(page, PRODUCTO_1.nombre)).toBe(stockAntes - 1);

  errores.afirmarSinErrores();
});
