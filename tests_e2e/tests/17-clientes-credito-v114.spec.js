// @ts-check
// TEST 17 — CLIENTES + CRÉDITO (V1.14) DE PUNTA A PUNTA.
//
// Con navegador real: alta de persona y empresa, comprador autorizado, política de crédito, venta mixta
// desde el POS, deuda, cobro parcial con FIFO, estado y bloqueo por límite, autorización temporal de un
// cliente DEMORADO y reapertura de uno en CRÍTICA. Los clientes "Demorado E2E" y "Critico E2E" los siembra
// tests_e2e/servidor_pruebas.py con deuda de hace 50 y 61 días (no se manipula el reloj del navegador).
// Las reglas de negocio están cubiertas en pytest; esto confirma el mecanismo desde el navegador.
const { test, expect } = require('@playwright/test');
const {
  loginViaUI,
  agregarPorCodigoBarras,
  configurarCreditoGeneral,
  registrarListenersDeErrores,
  PRODUCTO_1,
} = require('./helpers');

const ETIQUETA_A_CUENTA = 'A cuenta (CUENTA_CORRIENTE)';

async function idDeFicha(page) {
  await page.waitForURL(/\/clientes\/\d+/);
  return Number(page.url().match(/\/clientes\/(\d+)/)[1]);
}

async function elegirClienteEnPos(page, texto, nombre) {
  await page.getByText(ETIQUETA_A_CUENTA).click();
  await page.locator('#cliente-busqueda').fill(texto);
  const resultado = page.locator('[data-cliente-resultados] button', { hasText: nombre });
  await expect(resultado).toBeVisible();
  await resultado.click();
  await expect(page.locator('#bloque-credito')).toBeVisible();
}

async function cobrarEnPos(page) {
  const [respuesta] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/ventas') && r.request().method() === 'POST'),
    page.locator('#boton-cobrar').click(),
  ]);
  return respuesta;
}

test('persona: venta mixta, deuda, cobro parcial FIFO y bloqueo por límite', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/clientes/nuevo' });
  await configurarCreditoGeneral(page, '6');

  await page.goto('/clientes/nuevo');
  await page.locator('input[name="nombre"]').fill('Persona V114');
  await page.locator('select[name="condicion_fiscal"]').selectOption('MONOTRIBUTO');
  await page.getByRole('button', { name: 'Crear cliente' }).click();
  const clienteId = await idDeFicha(page);
  await expect(page.locator('#cliente-tipo')).toHaveText('Persona');
  await expect(page.locator('#cliente-condicion-fiscal')).toHaveText('Monotributo');
  await expect(page.locator('#credito-disponible')).toHaveText('$6,00');

  // Venta de 2 x $2.50 = $5.00: paga $1.00 en efectivo, $4.00 a cuenta.
  await page.goto('/ventas');
  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await elegirClienteEnPos(page, 'Persona V114', 'Persona V114');
  await expect(page.locator('[data-credito-estado]')).toContainText('Sin deuda');
  await page.locator('#pago-contado').fill('1');
  await expect(page.locator('[data-credito-a-cuenta]')).toContainText('$4.00');
  await expect(page.locator('#boton-cobrar')).toBeEnabled();
  const venta = await cobrarEnPos(page);
  expect(venta.status()).toBe(200);
  const datos = await venta.json();
  expect([datos.pago_contado_centavos, datos.credito_centavos]).toEqual([100, 400]);

  // Una segunda venta igual ya no entra en el límite: el POS lo avisa y no deja cobrar.
  await page.goto('/ventas');
  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await elegirClienteEnPos(page, 'Persona V114', 'Persona V114');
  await expect(page.locator('[data-credito-mensaje]')).toContainText('supera el crédito disponible');
  await expect(page.locator('#boton-cobrar')).toBeDisabled();

  // Ficha: saldo, obligación y estado; cobro parcial de $1.50 imputado FIFO.
  await page.goto(`/clientes/${clienteId}`);
  await expect(page.locator('#cliente-saldo')).toHaveText('$4,00');
  await expect(page.locator('#cliente-estado-credito')).toHaveAttribute('data-estado', 'NORMAL');
  await expect(page.locator('#tabla-obligaciones tbody tr')).toHaveCount(1);
  await page.goto(`/clientes/${clienteId}/cobro`);
  await expect(page.locator('#cobro-obligaciones')).toBeVisible();
  await page.locator('#cobro-monto').fill('1.50');
  await page.getByRole('button', { name: 'Confirmar cobro' }).click();
  await page.waitForURL(new RegExp(`/clientes/${clienteId}`));
  await expect(page.locator('#cliente-saldo')).toHaveText('$2,50');
  await expect(page.locator('#lista-imputaciones li')).toHaveCount(1);
  await expect(page.locator('#lista-imputaciones')).toContainText('$1,50');
  errores.afirmarSinErrores();
});

test('empresa: alta, comprador autorizado y venta a cuenta con quién compró', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/clientes/nuevo' });
  await configurarCreditoGeneral(page, '1000');

  await page.goto('/clientes/nuevo');
  await page.locator('input[name="tipo_cliente"][value="EMPRESA"]').check();
  await page.locator('input[name="nombre"]').fill('Empresa V114');
  await page.locator('select[name="condicion_fiscal"]').selectOption('RESPONSABLE_INSCRIPTO');
  await page.locator('input[name="cuit"]').fill('30-71234567-1');
  await page.locator('input[name="razon_social"]').fill('Empresa V114 SRL');
  await page.locator('input[name="contacto"]').fill('Marta');
  await page.locator('input[name="telefono"]').fill('4444');
  await page.locator('input[name="direccion"]').fill('Calle 1');
  await page.getByRole('button', { name: 'Crear cliente' }).click();
  const empresaId = await idDeFicha(page);
  await expect(page.locator('#cliente-tipo')).toHaveText('Empresa');

  await page.locator('#autorizado-nombre').fill('Juan Comprador');
  await page.getByRole('button', { name: 'Autorizar comprador' }).click();
  await page.waitForURL(new RegExp(`/clientes/${empresaId}`));
  await expect(page.locator('#lista-autorizados-activos')).toContainText('Juan Comprador');

  await page.goto('/ventas');
  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await elegirClienteEnPos(page, 'Empresa V114', 'Empresa V114');
  await expect(page.locator('[data-credito-comprador]')).toBeVisible();
  await expect(page.locator('#boton-cobrar')).toBeDisabled(); // falta quién compra
  await page.locator('#comprador-autorizado').selectOption({ label: 'Juan Comprador' });
  await expect(page.locator('#boton-cobrar')).toBeEnabled();
  const venta = await cobrarEnPos(page);
  expect(venta.status()).toBe(200);
  const { id } = await venta.json();
  await page.goto(`/ventas/${id}`);
  await expect(page.locator('#venta-comprador')).toContainText('Juan Comprador');
  errores.afirmarSinErrores();
});

test('demorado: bloqueado en el POS hasta que el dueño lo autoriza', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/clientes?q=Demorado%20E2E' });
  await configurarCreditoGeneral(page, '1000');
  await page.goto('/clientes?q=Demorado%20E2E');
  await page.locator('tr', { hasText: 'Demorado E2E' }).getByRole('link', { name: 'Ver' }).click();
  const clienteId = await idDeFicha(page);
  await expect(page.locator('#cliente-estado-credito')).toHaveAttribute('data-estado', 'DEMORADO');

  await page.goto('/ventas');
  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await elegirClienteEnPos(page, 'Demorado E2E', 'Demorado E2E');
  await expect(page.locator('[data-credito-mensaje]')).toContainText('autorización temporal');
  await expect(page.locator('#boton-cobrar')).toBeDisabled();

  await page.goto(`/clientes/${clienteId}`);
  const formulario = page.locator('#form-autorizacion-demorado');
  await formulario.locator('input[name="compromiso"]').fill('Paga el viernes');
  const minimo = await formulario.locator('input[name="fecha_limite"]').getAttribute('min');
  await formulario.locator('input[name="fecha_limite"]').fill(minimo || '');
  await formulario.getByRole('button', { name: 'Autorizar' }).click();
  await page.waitForURL(new RegExp(`/clientes/${clienteId}`));
  await expect(page.locator('#credito-bloqueo')).toContainText('autorización temporal hasta');

  await page.goto('/ventas');
  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  await elegirClienteEnPos(page, 'Demorado E2E', 'Demorado E2E');
  await expect(page.locator('#boton-cobrar')).toBeEnabled();
  expect((await cobrarEnPos(page)).status()).toBe(200);
  errores.afirmarSinErrores();
});

test('crítica: el crédito se cierra, se cobra todo y solo el dueño lo reabre', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/clientes?q=Critico%20E2E' });
  await page.locator('tr', { hasText: 'Critico E2E' }).getByRole('link', { name: 'Ver' }).click();
  const clienteId = await idDeFicha(page);
  await expect(page.locator('#cliente-estado-credito')).toHaveAttribute('data-estado', 'CRITICA');
  await expect(page.locator('#lista-episodios')).toContainText('Crédito cerrado');

  await page.goto(`/clientes/${clienteId}/cobro`);
  await page.locator('#cobro-total').click();
  await page.getByRole('button', { name: 'Confirmar cobro' }).click();
  await page.waitForURL(new RegExp(`/clientes/${clienteId}`));
  await expect(page.locator('#cliente-saldo')).toHaveText('$0,00');
  await expect(page.locator('#credito-bloqueo')).toContainText('Crédito cerrado'); // pagar no reabre

  await page.locator('#form-reapertura input[name="observacion"]').fill('Pagó todo');
  await page.getByRole('button', { name: 'Reabrir crédito' }).click();
  await page.waitForURL(new RegExp(`/clientes/${clienteId}`));
  await expect(page.locator('#form-reapertura')).toHaveCount(0);
  await expect(page.locator('#lista-episodios')).toContainText('Reabierto');
  errores.afirmarSinErrores();
});

test('responsive: la ficha y el bloque de crédito del POS no desbordan @responsive', async ({ page }) => {
  await loginViaUI(page, { irA: '/clientes?q=Demorado%20E2E' });
  await page.locator('tr', { hasText: 'Demorado E2E' }).getByRole('link', { name: 'Ver' }).click();
  await idDeFicha(page);
  const anchoFicha = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(anchoFicha).toBeLessThanOrEqual(1);
  await page.goto('/ventas');
  const anchoPos = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(anchoPos).toBeLessThanOrEqual(1);
});
