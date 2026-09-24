// @ts-check
// TEST 10 — V1.2: reposición, actualización masiva de precios, importación,
// datos del ticket y auditoría, en un navegador real.
const { test, expect } = require('@playwright/test');
const {
  loginViaUI,
  agregarPorCodigoBarras,
  seleccionarMedioDePago,
  registrarListenersDeErrores,
  PRODUCTO_1,
} = require('./helpers');

test('reposición: sugerencia editable, subtotal y total se recalculan, y se puede excluir una fila', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/reposicion' });

  const fila = page.locator('[data-fila-reposicion]', { hasText: 'Bajo Stock E2E' });
  await expect(fila).toBeVisible();
  await expect(fila.locator('[data-cantidad]')).toHaveValue('4'); // mínimo 5 - stock 1
  await expect(fila.locator('[data-subtotal]')).toContainText('12,00'); // 4 x $3,00

  await fila.locator('[data-cantidad]').fill('10');
  await expect(fila.locator('[data-subtotal]')).toContainText('30,00');
  await expect(page.locator('#total-reposicion')).toContainText('30,00');

  await fila.locator('[data-incluir]').uncheck();
  await expect(page.locator('#total-reposicion')).toContainText('0,00');
  errores.afirmarSinErrores();
});

test('precios masivos: vista previa, selección de un producto y confirmación con historial y auditoría', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/precios' });

  await page.locator('#valor').fill('10');
  await page.getByRole('button', { name: 'Ver vista previa' }).click();

  const filas = page.locator('tbody tr');
  await expect(filas.filter({ hasText: 'Precio Masivo E2E' })).toContainText('10,00');
  await expect(filas.filter({ hasText: 'Precio Masivo E2E' })).toContainText('11,00');

  // Solo el producto exclusivo del test: se destildan todos los demás.
  const casillas = page.locator('input[name="seleccion"]');
  for (let i = 0; i < (await casillas.count()); i++) await casillas.nth(i).uncheck();
  await filas.filter({ hasText: 'Precio Masivo E2E' }).locator('input[name="seleccion"]').check();

  await page.getByRole('button', { name: 'Confirmar actualización' }).click();
  await expect(page.locator('#toast-container')).toContainText('Precios actualizados: 1 producto(s)');
  await expect(page.locator('tbody')).toContainText('Aumentar');

  await page.goto('/auditoria?accion=PRECIOS_ACTUALIZACION_MASIVA');
  await expect(page.locator('tbody')).toContainText('1 producto(s)');
  errores.afirmarSinErrores();
});

test('importación: actualiza existentes, informa el reporte completo y "todo o nada" no aplica nada', async ({ page }) => {
  await loginViaUI(page, { irA: '/productos/importar' });
  const encabezado = 'codigo_barras,nombre,precio_costo,precio_venta,stock_actual,stock_minimo\n';

  // 1) Crear + actualizar: uno existente (cambia el nombre) y uno nuevo.
  await page.locator('#input-archivo-importar').setInputFiles({
    name: 'productos.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(encabezado + '7790000000066,Precio Masivo E2E renombrado,,,,\n7790000000555,Producto Importado E2E,1.00,2.00,3,0\n'),
  });
  await page.getByText('actualizar', { exact: false }).first().click();
  await page.getByRole('button', { name: 'Importar' }).click();
  await expect(page.getByText('Importación completa.')).toBeVisible();

  // 2) Todo o nada con una fila inválida: no se aplica nada y se listan todas.
  await page.goto('/productos/importar');
  await page.locator('#input-archivo-importar').setInputFiles({
    name: 'malo.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(encabezado + '7790000000888,Deberia no crearse,1,2,0,0\n7790000000889,,abc,2,0,0\n'),
  });
  await page.getByRole('button', { name: 'Importar' }).click();
  await expect(page.getByText('No se importó nada')).toBeVisible();
  await expect(page.locator('#filas-rechazadas li')).toHaveCount(1);

  await page.goto('/productos');
  await expect(page.getByText('Deberia no crearse')).toHaveCount(0);
  await expect(page.getByText('Producto Importado E2E')).toBeVisible();
});

test('configuración del ticket: se guarda, se recuerda y no ofrece campos fiscales', async ({ page }) => {
  await loginViaUI(page, { irA: '/configuracion' });

  await page.locator('#nombre').fill('Kiosco E2E');
  await page.locator('#direccion').fill('Calle Falsa 123');
  await page.locator('#pie').fill('Gracias por su compra');
  await page.getByRole('button', { name: 'Guardar' }).click();

  await expect(page.locator('#toast-container')).toContainText('Datos del ticket guardados');
  await expect(page.locator('#nombre')).toHaveValue('Kiosco E2E');
  await expect(page.locator('#direccion')).toHaveValue('Calle Falsa 123');
  await expect(page.locator('body')).not.toContainText('CUIT');
});

test('ticket: nombre largo sin espacios se parte y no desborda los 80 mm; sin leyenda fija de no factura', async ({ page }) => {
  await loginViaUI(page, { irA: '/configuracion' });
  await page.locator('#nombre').fill('K'.repeat(60));
  await page.locator('#direccion').fill('D'.repeat(100));
  await page.locator('#pie').fill('P'.repeat(120));
  await page.getByRole('button', { name: 'Guardar' }).click();
  await expect(page.locator('#toast-container')).toContainText('Datos del ticket guardados');

  await page.goto('/ventas');
  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await seleccionarMedioDePago(page, 'TARJETA');
  await page.locator('#boton-cobrar').click();
  const href = await page.getByRole('link', { name: 'Imprimir ticket' }).getAttribute('href');

  await page.goto(href);
  const medidas = await page.evaluate(() => {
    const ticket = document.querySelector('.ticket');
    const desbordados = Array.from(ticket.querySelectorAll('p, td, th, span')).filter((el) => el.scrollWidth > el.clientWidth + 1);
    return { ticketScroll: ticket.scrollWidth, ticketAncho: ticket.clientWidth, desbordados: desbordados.length };
  });
  expect(medidas.ticketScroll).toBeLessThanOrEqual(medidas.ticketAncho + 1);
  expect(medidas.desbordados).toBe(0);
  await expect(page.locator('body')).toContainText('K'.repeat(60));
  await expect(page.locator('body')).not.toContainText('No válido como factura');
  await expect(page.locator('body')).not.toContainText('Comprobante interno');
});

test('importación: por defecto es todo o nada y la parcial es una casilla explícita sin marcar', async ({ page }) => {
  await loginViaUI(page, { irA: '/productos/importar' });
  await expect(page.locator('input[name="permitir_parcial"]')).not.toBeChecked();
  await expect(page.locator('input[name="todo_o_nada"]')).toHaveCount(0);
});
