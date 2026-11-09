// @ts-check
// TEST 22 — CIERRE OPERATIVO: gastos con egreso de caja, pedido vendido desde el POS precargado, estado de
// preparación fiscal y backup programado.
//
// Con un navegador real y el servidor E2E aislado. Las reglas (atomicidad, idempotencia, triggers, permisos)
// están en pytest; acá se verifica que las pantallas nuevas funcionan de punta a punta sin errores de consola.
const { test, expect } = require('@playwright/test');
const { loginViaUI, registrarListenersDeErrores, PRODUCTO_1 } = require('./helpers');

test('gastos: registrar un gasto pagado con la caja genera el egreso y el resultado operativo lo muestra', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/gastos' });
  await expect(page.locator('#boton-nuevo-gasto')).toBeVisible();

  await page.goto('/gastos/nuevo');
  await page.selectOption('select[name="categoria"]', 'ELECTRICIDAD');
  await page.fill('input[name="descripcion"]', 'Luz E2E');
  await page.fill('#input-importe', '12,50');
  await page.selectOption('#select-medio-pago', 'EFECTIVO_CAJA');
  await expect(page.locator('#aviso-caja')).toBeVisible();
  await page.getByRole('button', { name: 'Registrar gasto' }).click();
  await expect(page).toHaveURL(/\/gastos\/\d+/);
  await expect(page.locator('#gasto-importe')).toContainText('12,50');
  await expect(page.getByText('egreso de caja #')).toBeVisible();

  // El egreso figura en la caja con la descripción del gasto.
  await page.goto('/caja');
  await expect(page.getByText('Gasto operativo: Electricidad – Luz E2E')).toBeVisible();

  // El resultado operativo del período incluye el gasto.
  await page.goto('/analitica/resultado');
  await expect(page.locator('#resultado-operativo')).toBeVisible();
  await expect(page.locator('#resultado-gastos')).toContainText('12,50');
  await expect(page.locator('#tabla-gastos-categoria [data-categoria="ELECTRICIDAD"]')).toBeVisible();

  // Corregir lo descriptivo y anular con reintegro desde las pantallas.
  await page.goto('/gastos');
  await page.locator('#tabla-gastos tr[data-gasto]').first().click();
  await page.locator('#boton-editar-gasto').click();
  await page.fill('input[name="responsable"]', 'Ana E2E');
  await page.getByRole('button', { name: 'Guardar corrección' }).click();
  await expect(page.getByText('Ana E2E')).toBeVisible();
  await page.locator('#boton-anular-gasto').click();
  await page.fill('textarea[name="motivo"]', 'Cargado por error E2E');
  await page.locator('input[name="reintegro_a_caja"][value="SI"]').check();
  page.once('dialog', (dialogo) => dialogo.accept());
  await page.getByRole('button', { name: 'Anular gasto' }).click();
  await expect(page.locator('#aviso-anulado')).toContainText('volvió a la caja');
  errores.afirmarSinErrores();
});

test('pedidos: tomar un pedido, venderlo desde el POS precargado y verlo convertido', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/pedidos' });
  await expect(page.locator('#boton-nuevo-pedido')).toBeVisible();

  await page.goto('/pedidos/nuevo');
  await page.fill('#input-contacto', 'Cliente de mostrador E2E');
  const selectProducto = page.locator('select[name="producto_id"]').first();
  await selectProducto.selectOption({ label: await selectProducto.locator('option', { hasText: PRODUCTO_1.nombre }).first().textContent() ?? '' });
  await page.locator('input[name="cantidad"]').first().fill('2');
  await page.fill('textarea[name="notas"]', 'pasa a la tarde');
  await page.getByRole('button', { name: 'Tomar pedido' }).click();
  await expect(page).toHaveURL(/\/pedidos\/\d+/);
  await expect(page.getByText('El stock no está reservado')).toBeVisible();
  const urlPedido = page.url();

  // Vender en el POS: el carrito aparece precargado con lo pedido y al cobrar el pedido queda convertido.
  await page.locator('#boton-vender-pedido').click();
  await expect(page.locator('#pedido-precarga')).toBeVisible();
  await expect(page.locator('#carrito-lista')).toContainText(PRODUCTO_1.nombre);
  await page.getByText('TARJETA', { exact: true }).click();
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/ventas') && r.request().method() === 'POST' && r.status() === 200),
    page.locator('#boton-cobrar').click(),
  ]);
  await expect(page.locator('#pedido-precarga')).toHaveCount(0);

  await page.goto(urlPedido);
  await expect(page.locator('#aviso-convertido')).toBeVisible();
  await expect(page.locator('#enlace-venta-pedido')).toBeVisible();
  await expect(page.locator('#boton-vender-pedido')).toHaveCount(0);
  // Si el spec 19 dejó la facturación activa, el POS intenta fiscalizar la venta y ARCA falso puede
  // rechazarla (422 esperado, ajeno al pedido): el resto de las respuestas y la consola deben estar limpias.
  const inesperadas = errores.respuestasHttpInesperadas.filter((r) => !r.includes('/api/fiscal/'));
  expect(inesperadas, inesperadas.join('\n')).toEqual([]);
  expect(errores.erroresDeNavegador, errores.erroresDeNavegador.join('\n')).toEqual([]);
});

test('fiscal y backup: el estado de preparación explica qué falta y el backup programado se configura', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/fiscal' });
  await expect(page.locator('#fiscal-preparacion')).toBeVisible();
  await expect(page.locator('#fiscal-situacion')).toBeVisible();
  await expect(page.locator('#fiscal-pasos [data-paso="homologacion"]')).toHaveAttribute('data-estado', 'EXTERNO');
  await page.locator('#enlace-listado-fiscal').click();
  await expect(page.locator('#aviso-no-libro-iva')).toContainText('No es el Libro IVA Ventas');

  await page.goto('/backup');
  await page.fill('#hora', '21:30');
  await page.locator('#backup-programado button[type="submit"]').click();
  await expect(page.locator('#backup-programado-estado')).toContainText('21:30');
  await expect(page.locator('#backup-programado')).toContainText('Solo funciona con Twinx abierto');
  errores.afirmarSinErrores();
});
