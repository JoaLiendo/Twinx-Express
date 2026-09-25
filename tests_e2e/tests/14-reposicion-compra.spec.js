// @ts-check
// TEST 14 — REPOSICIÓN → COMPRA PRECARGADA E IMPRIMIBLE (V1.6-B).
//
// Con un navegador real: la reposición agrupa por proveedor principal, "Imprimir lista" abre la lista
// imprimible sin registrar nada, "Pasar a compra" abre el formulario de compra ya cargado (sin línea vacía
// extra, con el total en vivo) y solo al confirmar se registra la compra por el flujo normal. Las reglas
// (agrupación, validaciones, costos, idempotencia, que la precarga no escribe) están en pytest
// (test_reposicion_compra.py).
const { test, expect } = require('@playwright/test');
const { loginViaUI, leerStockDeCard, registrarListenersDeErrores } = require('./helpers');

const PRODUCTO = 'Bajo Stock E2E'; // stock 1, mínimo 5, costo $3,00, sin proveedor principal (ver servidor_pruebas.py)
const PROVEEDOR = 'Proveedor Reposicion E2E';

test('reposición: imprimir y pasar a compra un grupo, y confirmar registra la compra una sola vez', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/ventas' });
  const stockAntes = await leerStockDeCard(page, PRODUCTO);

  // El proveedor y su vínculo como principal se crean por los flujos normales de la app.
  await page.goto('/reposicion');
  const idProducto = await page
    .locator('[data-fila-reposicion]', { hasText: PRODUCTO })
    .locator('input[name="p"]')
    .getAttribute('value');
  await page.request.post('/proveedores/nuevo', { form: { nombre: PROVEEDOR } });
  await page.goto('/proveedores');
  const enlace = await page.locator('a', { hasText: PROVEEDOR }).first().getAttribute('href');
  const idProveedor = /\/proveedores\/(\d+)/.exec(enlace || '')?.[1];
  expect(idProveedor).toBeTruthy();
  await page.request.post(`/proveedores/${idProveedor}/productos`, { form: { producto_id: String(idProducto) } });

  // Agrupada por su proveedor principal (ya no en "Sin proveedor principal").
  await page.goto('/reposicion');
  const grupo = page.locator('[data-grupo-reposicion]', { hasText: PROVEEDOR });
  const fila = grupo.locator('[data-fila-reposicion]', { hasText: PRODUCTO });
  await expect(fila).toBeVisible();
  await fila.locator('[data-cantidad]').fill('10');
  await expect(fila.locator('[data-subtotal]')).toContainText('30,00');

  // Imprimir: abre la lista con la cantidad editada y no registra nada.
  const [impresion] = await Promise.all([page.context().waitForEvent('page'), grupo.locator('[data-accion="imprimir"]').click()]);
  await expect(impresion.locator('#reposicion-proveedor')).toHaveText(PROVEEDOR);
  await expect(impresion.locator('#reposicion-total')).toContainText('30,00');
  await impresion.close();
  await page.goto('/ventas');
  expect(await leerStockDeCard(page, PRODUCTO)).toBe(stockAntes);

  // Pasar a compra: formulario precargado (proveedor, cantidad, costo, total), todavía sin registrar.
  await page.goto('/reposicion');
  const grupo2 = page.locator('[data-grupo-reposicion]', { hasText: PROVEEDOR });
  await grupo2.locator('[data-cantidad]').fill('10');
  await grupo2.locator('[data-accion="comprar"]').click();
  await page.waitForURL(/\/reposicion\/comprar/);
  await expect(page.locator('select[name="proveedor_id"]')).toHaveValue(String(idProveedor));
  await expect(page.locator('#lineas-compra .linea-compra')).toHaveCount(1);
  const linea = page.locator('#lineas-compra .linea-compra');
  await expect(linea.locator('.linea-cantidad')).toHaveValue('10');
  await expect(linea.locator('.linea-costo')).toHaveValue('3.00');
  await expect(page.locator('#total-compra')).toHaveText('$30.00');
  await page.goto('/ventas');
  expect(await leerStockDeCard(page, PRODUCTO)).toBe(stockAntes);

  // Confirmar por el flujo de compra normal: el stock sube una sola vez.
  await page.goto('/reposicion');
  await page.locator('[data-grupo-reposicion]', { hasText: PROVEEDOR }).locator('[data-cantidad]').fill('10');
  await page.locator('[data-grupo-reposicion]', { hasText: PROVEEDOR }).locator('[data-accion="comprar"]').click();
  await page.waitForURL(/\/reposicion\/comprar/);
  await page.getByRole('button', { name: 'Registrar compra' }).click();
  await page.waitForURL(/\/compras\/\d+$/);
  await page.goto('/ventas');
  expect(await leerStockDeCard(page, PRODUCTO)).toBe(stockAntes + 10);

  errores.afirmarSinErrores();
});
