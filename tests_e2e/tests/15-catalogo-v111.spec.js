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
  for (const ruta of ['/', '/productos/categorias', '/inventario/nuevo', '/compras/nueva']) {
    await page.goto(ruta);
    const desborda = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
    );
    expect(desborda, ruta).toBe(false);
  }
});

test('V1.12-B: compra con varias fechas de vencimiento en una línea', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/proveedores' });
  const proveedor = `Lácteos ${Date.now().toString().slice(-6)}`;
  await page.request.post('/proveedores/nuevo', { form: { nombre: proveedor } });

  await page.goto('/compras/nueva');
  await page.locator('select[name="proveedor_id"]').selectOption({ label: proveedor });
  const linea = page.locator('.linea-compra').first();
  await linea.locator('select[name="producto_id"]').selectOption({ label: PRODUCTO_1.nombre });
  await linea.locator('.linea-cantidad').fill('10');
  await linea.locator('.linea-costo').fill('1.50');
  await linea.getByRole('button', { name: 'Varias fechas' }).click();
  const filas = linea.locator('.linea-varias-fila');
  await filas.nth(0).locator('.varias-cantidad').fill('6');
  await filas.nth(0).locator('.varias-fecha').fill('2099-11-20');
  await filas.nth(1).locator('.varias-cantidad').fill('3');
  await filas.nth(1).locator('.varias-fecha').fill('2099-12-15');
  await expect(linea.locator('.linea-varias-resto')).toHaveText('1 sin vencimiento conocido.');
  await page.getByRole('button', { name: /Registrar/ }).click();

  await expect(page.locator('#toast-container')).toContainText('registrada correctamente');
  await expect(page.locator('[data-vencimientos-linea]')).toContainText('20/11/2099 (6)');
  await expect(page.locator('[data-vencimientos-linea]')).toContainText('15/12/2099 (3)');
  await expect(page.locator('[data-vencimientos-linea]')).toContainText('sin fecha (1)');
  errores.afirmarSinErrores();
});

test('V1.12-D: el cajero no vende mercadería vencida sin la autorización del dueño', async ({ page }) => {
  const { USUARIO, USUARIO_CASHIER, PASSWORD, agregarPorCodigoBarras, seleccionarMedioDePago } = require('./helpers');
  await loginViaUI(page, { usuario: USUARIO_CASHIER });
  await agregarPorCodigoBarras(page, '7790000000078');
  await seleccionarMedioDePago(page, 'TARJETA');

  const [primera] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/ventas') && r.request().method() === 'POST'),
    page.locator('#boton-cobrar').click(),
  ]);
  expect(primera.status()).toBe(422);
  const dialogo = page.locator('#dialogo-vencidos');
  await expect(dialogo).toBeVisible();
  await expect(dialogo).toContainText('VENCIDA');

  await page.getByLabel('Usuario del dueño').fill(USUARIO);
  await page.getByLabel('Contraseña del dueño').fill(PASSWORD);
  const [autorizada] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/ventas') && r.request().method() === 'POST'),
    page.getByRole('button', { name: 'Autorizar y cobrar' }).click(),
  ]);
  expect(autorizada.status()).toBe(200);
  await expect(page.locator('#toast-container')).toContainText('registrada');
});
