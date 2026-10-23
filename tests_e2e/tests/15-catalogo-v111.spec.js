// @ts-check
// TEST 15 — V1.11-E: subcategorías, etiquetas y listado de productos en tabla.
// El @responsive comprueba que la tabla no genera scroll horizontal ni recorta el nombre en ningún
// viewport (las columnas secundarias se pliegan dentro de la celda del producto).
const { test, expect } = require('@playwright/test');
const { loginViaUI, registrarListenersDeErrores, PRODUCTO_1 } = require('./helpers');

test('subcategoría y etiqueta desde la UI, y filtros del listado', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/productos/categorias' });

  const sufijo = Date.now().toString().slice(-6);
  const principal = `Almacén ${sufijo}`;
  const sub = `Galletitas ${sufijo}`;

  await page.getByPlaceholder('Nombre de la categoría').fill(principal);
  await page.getByRole('button', { name: 'Crear', exact: true }).click();
  await expect(page.locator('#toast-container')).toContainText(principal);

  await page.getByPlaceholder('Nombre de la categoría').fill(sub);
  await page.getByLabel('Ubicación').selectOption({ label: `Dentro de: ${principal}` });
  await page.getByRole('button', { name: 'Crear', exact: true }).click();
  await expect(page.locator('#arbol-categorias [data-nivel="2"]', { hasText: sub })).toBeVisible();

  await page.getByPlaceholder('Nueva etiqueta').fill(`Oferta ${sufijo}`);
  await page.getByRole('button', { name: 'Crear etiqueta' }).click();
  await expect(page.locator(`#etiquetas input[name="nombre"][value="Oferta ${sufijo}"]`)).toBeVisible();

  await page.goto('/productos');
  await expect(page.locator('#tabla-productos')).toBeVisible();
  await page.getByLabel('Buscar por nombre o código').fill(PRODUCTO_1.codigoBarras);
  await page.getByRole('button', { name: 'Buscar' }).click();
  await expect(page.locator('#tabla-productos tbody tr')).toHaveCount(1);
  await expect(page.locator('#tabla-productos')).toContainText(PRODUCTO_1.nombre);

  errores.afirmarSinErrores();
});

test('@responsive la tabla de productos no desborda ni recorta nombres', async ({ page }) => {
  await loginViaUI(page, { irA: '/productos' });
  await expect(page.locator('#tabla-productos')).toBeVisible();

  const hayOverflowHorizontal = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
  );
  expect(hayOverflowHorizontal).toBe(false);

  const nombre = page.locator('#tabla-productos tbody p', { hasText: PRODUCTO_1.nombre }).first();
  await expect(nombre).toBeVisible();
  const recortado = await nombre.evaluate((el) => el.scrollWidth > el.clientWidth + 1);
  expect(recortado).toBe(false);
});

test('@responsive dashboard, categorías e inventario nuevo sin scroll horizontal', async ({ page }) => {
  await loginViaUI(page, { irA: '/' });
  for (const ruta of ['/', '/productos/categorias', '/inventario/nuevo']) {
    await page.goto(ruta);
    const desborda = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
    );
    expect(desborda, ruta).toBe(false);
  }
});
