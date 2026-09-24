// @ts-check
// TEST 11 — CLIENTES + CUENTA CORRIENTE DE PUNTA A PUNTA (021).
//
// Recorre, con un navegador real y la app real, el ciclo completo: crear un cliente, venderle a cuenta
// desde el POS, ver su cuenta, cobrarle (parcial y total) y verificar el Historial y que la venta a
// cuenta no ofrezca anulación. Las reglas de negocio ya están cubiertas exhaustivamente en pytest
// (tests/test_services/test_cobros_cuenta.py, test_venta_a_cuenta.py, tests/test_interfaces_web/*):
// esto solo confirma que el mecanismo funciona de extremo a extremo desde el navegador.
//
// Todos los datos salen del servidor de pruebas (tests_e2e/servidor_pruebas.py): OWNER, caja abierta y
// dos productos. Los clientes se crean por la propia UI, con nombres únicos por test.
const { test, expect } = require('@playwright/test');
const {
  loginViaUI,
  agregarPorCodigoBarras,
  seleccionarMedioDePago,
  registrarListenersDeErrores,
  PRODUCTO_1,
} = require('./helpers');

const ETIQUETA_A_CUENTA = 'A cuenta (CUENTA_CORRIENTE)';

/** Espera a estar en la ficha del cliente (la query del toast se limpia sola, así que es opcional). */
async function esperarFicha(page, clienteId) {
  await page.waitForURL(new RegExp(`/clientes/${clienteId}(\\?.*)?$`));
}

/** Crea un cliente por el formulario real y devuelve su id (sale de la URL de la ficha). */
async function crearClientePorUI(page, nombre, telefono) {
  await page.goto('/clientes/nuevo');
  await page.locator('input[name="nombre"]').fill(nombre);
  await page.locator('input[name="telefono"]').fill(telefono);
  await page.getByRole('button', { name: 'Crear cliente' }).click();
  await page.waitForURL(/\/clientes\/\d+/);
  const coincidencia = page.url().match(/\/clientes\/(\d+)/);
  expect(coincidencia).not.toBeNull();
  return Number(coincidencia[1]);
}

test('venta a cuenta, ficha, cobro parcial y total, historial y sin anulación', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  const nombre = 'Cliente E2E Cuenta';
  await loginViaUI(page, { irA: '/clientes/nuevo' });

  // 1) crear cliente
  const clienteId = await crearClientePorUI(page, nombre, '1155550001');
  await expect(page.getByRole('heading', { name: nombre })).toBeVisible();
  await expect(page.locator('#cliente-saldo')).toHaveText('$0,00');

  // 2) venta a cuenta desde el POS: 2 alfajores de $2.50 = $5.00
  await page.goto('/ventas');
  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);

  // EFECTIVO es el predeterminado y el selector de cliente no se ve
  await expect(page.locator('input[name="tipo_pago"]:checked')).toHaveValue('EFECTIVO');
  await expect(page.locator('#bloque-cliente')).toBeHidden();

  await page.getByText(ETIQUETA_A_CUENTA).click();
  await expect(page.locator('#bloque-cliente')).toBeVisible();
  await expect(page.locator('#boton-cobrar')).toBeDisabled(); // sin cliente no se puede cobrar

  await page.locator('#cliente-busqueda').fill('E2E Cuenta');
  const resultado = page.locator('[data-cliente-resultados] button', { hasText: nombre });
  await expect(resultado).toBeVisible();
  await resultado.click();

  const panel = page.locator('[data-cliente-seleccionado]');
  await expect(panel).toContainText(nombre);
  await expect(panel).toContainText('Saldo actual: $0.00');
  await expect(panel).toContainText('Saldo estimado tras esta venta: $5.00');
  await expect(page.locator('#boton-cobrar')).toBeEnabled();

  const [respuestaVenta] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/ventas') && r.request().method() === 'POST'),
    page.locator('#boton-cobrar').click(),
  ]);
  expect(respuestaVenta.status()).toBe(200);
  const venta = await respuestaVenta.json();
  expect(venta.tipo_pago).toBe('CUENTA_CORRIENTE');
  expect(venta.total_centavos).toBe(500);
  await expect(page.getByText(`a cuenta de ${nombre}`)).toBeVisible();

  // 3) visualizar la cuenta: saldo, cargos y el movimiento enlazado a la venta
  await page.goto(`/clientes/${clienteId}`);
  await expect(page.locator('#cliente-saldo')).toHaveText('$5,00');
  await expect(page.locator('#cliente-total-cargos')).toHaveText('$5,00');
  await expect(page.locator('#cliente-total-cobros')).toHaveText('$0,00');
  const enlaceVenta = page.locator('#tabla-movimientos-cuenta').getByRole('link', { name: `Venta #${venta.id}` });
  await expect(enlaceVenta).toHaveAttribute('href', `/ventas/${venta.id}`);

  // 4) cobro parcial de $2.00: la pantalla muestra el saldo resultante estimado
  await page.getByRole('link', { name: 'Registrar cobro' }).click();
  await expect(page.locator('#cobro-saldo-actual')).toHaveText('$5,00');
  await page.locator('#cobro-monto').fill('2.00');
  await expect(page.locator('#cobro-saldo-resultante')).toHaveText('$3.00');
  await page.locator('#cobro-monto').fill('9.00');
  await expect(page.locator('#cobro-saldo-resultante')).toContainText('supera el saldo');
  await page.locator('#cobro-monto').fill('2.00');
  await page.locator('#cobro-confirmar').click();
  await esperarFicha(page, clienteId);
  await expect(page.getByText('Cobro registrado correctamente.')).toBeVisible();

  // 5) verificar saldo tras el cobro parcial
  await expect(page.locator('#cliente-saldo')).toHaveText('$3,00');
  await expect(page.locator('#cliente-total-cobros')).toHaveText('$2,00');
  await expect(page.locator('#tabla-movimientos-cuenta tbody tr').first()).toContainText('Cobro'); // el más reciente primero

  // 6) cobro del total
  await page.getByRole('link', { name: 'Registrar cobro' }).click();
  await page.locator('#cobro-total').click();
  await expect(page.locator('#cobro-monto')).toHaveValue('3.00');
  await page.locator('#cobro-confirmar').click();
  await esperarFicha(page, clienteId);
  await expect(page.locator('#cliente-saldo')).toHaveText('$0,00');
  await expect(page.getByRole('link', { name: 'Registrar cobro' })).toHaveCount(0); // sin deuda no se ofrece cobrar

  // 7) historial: la venta a cuenta se identifica, muestra el cliente y se filtra por medio de pago
  await page.goto('/ventas/historial');
  await page.locator('select[name="tipo_pago"]').selectOption('CUENTA_CORRIENTE');
  await page.getByRole('button', { name: 'Filtrar' }).click();
  const fila = page.locator('tbody tr', { hasText: `#${venta.id}` });
  await expect(fila).toContainText('CUENTA_CORRIENTE');
  await expect(fila.getByRole('link', { name: nombre })).toHaveAttribute('href', `/clientes/${clienteId}`);

  // 8) la venta a cuenta no ofrece anulación (ni el botón ni el formulario)
  await page.goto(`/ventas/${venta.id}`);
  await expect(page.getByText('Una venta a cuenta no se puede anular.')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Anular venta' })).toHaveCount(0);
  await page.goto(`/ventas/${venta.id}/anular`);
  await expect(page).toHaveURL(new RegExp(`/ventas/${venta.id}(\\?.*)?$`));
  await expect(page.getByText('Una venta a cuenta corriente no puede anularse.')).toBeVisible();

  errores.afirmarSinErrores();
});

test('XSS: un nombre de cliente malicioso se ve como texto literal y no ejecuta nada', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  const nombreXss =
    '<img src=x onerror="window.__xssEjecutado = true">Cliente XSS" onmouseover="window.__xssEjecutado = true" & <b>x</b>';
  await loginViaUI(page, { irA: '/clientes/nuevo' });

  // alta por el formulario real y ficha
  const clienteId = await crearClientePorUI(page, nombreXss, '1155550002');
  await expect(page.locator('#cliente-saldo')).toBeVisible();
  expect(await page.evaluate(() => /** @type {any} */ (window).__xssEjecutado)).toBeUndefined();

  // lista de clientes
  await page.goto('/clientes?q=Cliente%20XSS');
  await expect(page.locator('tbody')).toContainText(nombreXss);
  expect(await page.locator('tbody img').count()).toBe(0);
  expect(await page.locator('tbody b').count()).toBe(0);

  // POS: el buscador pinta el nombre desde JS (textContent)
  await page.goto('/ventas');
  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await page.getByText(ETIQUETA_A_CUENTA).click();
  await page.locator('#cliente-busqueda').fill('Cliente XSS');
  const resultados = page.locator('[data-cliente-resultados]');
  await expect(resultados).toContainText(nombreXss);
  expect(await resultados.locator('img').count()).toBe(0);
  expect(await resultados.locator('b').count()).toBe(0);
  expect(await resultados.locator('script').count()).toBe(0);

  // y seleccionado también
  await resultados.locator('button').first().click();
  const panel = page.locator('[data-cliente-seleccionado]');
  await expect(panel).toContainText(nombreXss);
  expect(await panel.locator('img').count()).toBe(0);
  expect(await panel.locator('b').count()).toBe(0);
  expect(await page.evaluate(() => /** @type {any} */ (window).__xssEjecutado)).toBeUndefined();

  // vaciar el carrito para no disparar el aviso de "salir de la página" en tests posteriores
  await page.locator('#carrito-lista button[aria-label^="Quitar"]').first().click().catch(() => {});
  expect(clienteId).toBeGreaterThan(0);
  errores.afirmarSinErrores();
});

test('al dejar CUENTA_CORRIENTE el selector se limpia, al volver está vacío y TARJETA no envía cliente_id', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  const nombre = 'Cliente E2E Selector';
  await loginViaUI(page, { irA: '/clientes/nuevo' });
  await crearClientePorUI(page, nombre, '1155550003');

  await page.goto('/ventas');
  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);

  // a) elegir un cliente estando en CUENTA_CORRIENTE
  await page.getByText(ETIQUETA_A_CUENTA).click();
  await page.locator('#cliente-busqueda').fill('E2E Selector');
  await page.locator('[data-cliente-resultados] button', { hasText: nombre }).click();
  const panel = page.locator('[data-cliente-seleccionado]');
  await expect(panel).toContainText(nombre);
  await expect(page.locator('#boton-cobrar')).toBeEnabled();

  // b) cambiar a TARJETA: el bloque se oculta y el cliente se descarta al instante
  await seleccionarMedioDePago(page, 'TARJETA');
  await expect(page.locator('#bloque-cliente')).toBeHidden();
  await expect(panel).toHaveClass(/hidden/);
  await expect(page.locator('[data-cliente-nombre]')).toHaveText('');
  await expect(page.locator('[data-cliente-zona-busqueda]')).not.toHaveClass(/hidden/);
  await expect(page.locator('#boton-cobrar')).toBeEnabled(); // TARJETA no necesita cliente

  // d/e) volver a CUENTA_CORRIENTE: el selector está vacío (no reaparece el cliente anterior)
  await page.getByText(ETIQUETA_A_CUENTA).click();
  await expect(page.locator('#bloque-cliente')).toBeVisible();
  await expect(panel).toHaveClass(/hidden/);
  await expect(page.locator('[data-cliente-nombre]')).toHaveText('');
  await expect(page.locator('[data-cliente-zona-busqueda]')).toBeVisible();
  await expect(page.locator('#boton-cobrar')).toBeDisabled(); // sin cliente no se puede cobrar a cuenta
  await expect(page.locator('[data-cliente-resultados] button', { hasText: nombre })).toBeVisible(); // hay que elegir de nuevo

  // f) cobrar con TARJETA: el cuerpo real de POST /api/ventas no lleva cliente_id
  await seleccionarMedioDePago(page, 'TARJETA');
  const [peticion, respuesta] = await Promise.all([
    page.waitForRequest((r) => r.url().includes('/api/ventas') && r.method() === 'POST'),
    page.waitForResponse((r) => r.url().includes('/api/ventas') && r.request().method() === 'POST'),
    page.locator('#boton-cobrar').click(),
  ]);
  expect(respuesta.status()).toBe(200);
  const cuerpo = JSON.parse(peticion.postData());
  expect(cuerpo.tipo_pago).toBe('TARJETA');
  expect(Object.keys(cuerpo)).not.toContain('cliente_id');
  expect((await respuesta.json()).tipo_pago).toBe('TARJETA');

  errores.afirmarSinErrores();
});
