// @ts-check
// V1.16: facturación fiscal ARCA de punta a punta contra el ARCA FALSO del servidor E2E
// (tests_e2e/servidor_pruebas.py::_preparar_fiscal). Nunca toca ARCA real.
//
// Serial: el primer test configura el emisor, el certificado y el IVA que usan los demás.
const path = require('path');
const { test, expect } = require('@playwright/test');
const { loginViaUI, agregarPorCodigoBarras, seleccionarMedioDePago, USUARIO_CASHIER, registrarListenersDeErrores } = require('./helpers');

const CERTIFICADOS = path.join('tests_e2e', '.tmp', 'certificados_fiscales');
const FACTURA = { codigo: '7790000000501', nombre: 'Factura E2E' };
const RECHAZO = { codigo: '7790000000502', nombre: 'Rechazo Fiscal E2E' };
const INCIERTO = { codigo: '7790000000503', nombre: 'Incierto Fiscal E2E' };

async function vender(page, codigo) {
  await agregarPorCodigoBarras(page, codigo);
  await seleccionarMedioDePago(page, 'TARJETA');
  const [venta, fiscal] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/api/ventas') && r.request().method() === 'POST'),
    page.waitForResponse((r) => r.url().includes('/api/fiscal/ventas/') && r.request().method() === 'POST'),
    page.locator('#boton-cobrar').click(),
  ]);
  return { ventaId: (await venta.json()).id, fiscal: await fiscal.json() };
}

test.describe.serial('V1.16 facturación fiscal', () => {
  test('sin configurar, el POS emite solo comprobante interno', async ({ page }) => {
    await loginViaUI(page, { irA: '/fiscal' });
    await expect(page.locator('#fiscal-emisor')).toContainText('Todavía no hay perfil fiscal');
    await page.goto('/ventas');
    await expect(page.locator('#post-venta-fiscal')).toHaveAttribute('data-fiscal-activo', 'false');
  });

  test('configurar emisor, validar punto de venta, certificado e IVA', async ({ page }) => {
    const errores = registrarListenersDeErrores(page);
    await loginViaUI(page, { irA: '/fiscal/configuracion' });
    const formulario = page.locator('#form-perfil-fiscal');
    await formulario.locator('#cuit').fill('20-11111111-2');
    await formulario.locator('#condicion_iva').selectOption('RESPONSABLE_INSCRIPTO');
    await formulario.locator('#razon_social').fill('Kiosco E2E SRL');
    await formulario.locator('#domicilio').fill('Av. Siempreviva 742');
    await formulario.locator('#punto_venta_cae').fill('3');
    await formulario.getByRole('button', { name: 'Guardar perfil fiscal' }).click();
    await page.getByRole('button', { name: 'Validar punto de venta', exact: true }).click();
    await expect(page.locator('#estado-pv-cae')).toContainText('Validado con ARCA');
    await expect(page.locator('#perfil-ambiente')).toContainText('Homologación');

    await page.goto('/fiscal/certificados');
    await page.locator('#ambiente').selectOption('HOMOLOGACION');
    await page.locator('#certificado').setInputFiles(path.join(CERTIFICADOS, 'homologacion.crt'));
    await page.locator('#clave').setInputFiles(path.join(CERTIFICADOS, 'homologacion.key'));
    await page.getByRole('button', { name: 'Importar certificado' }).click();
    await expect(page.locator('[data-certificado="HOMOLOGACION"]').first()).toContainText('vigente');
    await expect(page.locator('body')).not.toContainText('PRIVATE KEY');

    await page.goto('/fiscal/productos');
    for (const producto of [FACTURA, RECHAZO, INCIERTO]) {
      await page.getByLabel(`Tratamiento de ${producto.nombre}`).selectOption('GRAVADO:5');
    }
    await page.getByRole('button', { name: 'Guardar clasificación' }).click();
    await page.goto('/fiscal');
    await expect(page.locator('#fiscal-listo')).toContainText('activa: el POS fiscaliza');
    errores.afirmarSinErrores();
  });

  test('venta autorizada: factura 80 mm con CAE y QR, A4 y PDF', async ({ page }) => {
    await loginViaUI(page);
    const { ventaId, fiscal } = await vender(page, FACTURA.codigo);
    expect(fiscal.autorizado).toBe(true);
    await expect(page.locator('[data-fiscal-estado]')).toContainText('AUTORIZADA');
    const imprimir = page.locator('[data-fiscal-imprimir]');
    await expect(imprimir).toBeVisible();

    await page.goto(`/fiscal/comprobantes/${fiscal.comprobante_id}/ticket`);
    await expect(page.locator('#comprobante-fiscal-80mm')).toContainText(fiscal.codigo_autorizacion);
    await expect(page.locator('#comprobante-fiscal-80mm')).toContainText('SIN VALIDEZ FISCAL');
    await expect(page.locator('#comprobante-qr svg')).toBeVisible();
    expect(await page.locator('#comprobante-qr').getAttribute('data-qr-url')).toMatch(/^https:\/\/www\.arca\.gob\.ar\/fe\/qr\/\?p=/);

    await page.goto(`/fiscal/comprobantes/${fiscal.comprobante_id}`);
    await expect(page.locator('#comprobante-transparencia')).toContainText('IVA Contenido');
    const pdf = await page.request.get(`/fiscal/comprobantes/${fiscal.comprobante_id}/pdf`);
    expect(pdf.headers()['content-type']).toBe('application/pdf');
    expect((await pdf.body()).subarray(0, 8).toString()).toBe('%PDF-1.4');

    await page.goto(`/ventas/${ventaId}/ticket`);
    await expect(page.locator('#ticket-no-fiscal')).toContainText('NO VÁLIDO COMO FACTURA');
  });

  test('rechazo de ARCA: la venta queda registrada, sin CAE ni factura', async ({ page }) => {
    await loginViaUI(page);
    const { ventaId, fiscal } = await vender(page, RECHAZO.codigo);
    expect(fiscal.estado).toBe('RECHAZADO');
    await expect(page.locator('[data-fiscal-estado]')).toContainText('rechazada');
    await expect(page.locator('[data-fiscal-imprimir]')).toBeHidden();
    await page.goto(`/ventas/${ventaId}`);
    await expect(page.locator('#venta-fiscal')).toContainText('Fiscalización rechazada');
    const pdf = await page.request.get(`/fiscal/comprobantes/${fiscal.comprobante_id}/pdf`, { maxRedirects: 0 });
    expect(pdf.status()).toBe(303);
  });

  test('resultado incierto y conciliación manual del dueño', async ({ page }) => {
    await loginViaUI(page);
    const { ventaId, fiscal } = await vender(page, INCIERTO.codigo);
    expect(fiscal.estado).toBe('INCIERTO');
    await expect(page.locator('[data-fiscal-estado]')).toContainText('Resultado incierto');
    await page.goto(`/ventas/${ventaId}`);
    await expect(page.locator('#venta-fiscal-no-anulable')).toBeVisible();
    await page.getByRole('button', { name: 'Reintentar conciliación' }).click();
    await expect(page.locator(`[data-comprobante-fiscal="${fiscal.comprobante_id}"]`)).toHaveAttribute('data-estado', 'AUTORIZADO');
  });

  test('nota de crédito total: anula la venta solo con la NC autorizada', async ({ page }) => {
    await loginViaUI(page);
    const { ventaId } = await vender(page, FACTURA.codigo);
    await page.goto(`/ventas/${ventaId}`);
    await page.locator('#venta-nota-credito').click();
    await page.locator('#motivo').selectOption('ARREPENTIMIENTO_CLIENTE');
    await page.getByRole('button', { name: 'Emitir nota de crédito' }).click();
    await expect(page.locator('[data-cancelacion-fiscal="APLICADA"]')).toBeVisible();
    await expect(page.locator('#venta-fiscal')).toContainText('Nota de Crédito B');
  });

  test('el cajero factura pero no configura nada fiscal', async ({ page }) => {
    await loginViaUI(page, { usuario: USUARIO_CASHIER });
    const { fiscal } = await vender(page, FACTURA.codigo);
    expect(fiscal.autorizado).toBe(true);
    for (const ruta of ['/fiscal', '/fiscal/configuracion', '/fiscal/certificados', '/fiscal/productos']) {
      const respuesta = await page.goto(ruta);
      expect(respuesta?.status()).toBe(403);
    }
    const manipulado = await page.request.post('/fiscal/configuracion', { form: { cuit: '20111111112', ambiente: 'PRODUCCION' } });
    expect(manipulado.status()).toBe(403);
  });
});

test('panel fiscal sin desborde horizontal @responsive', async ({ page }) => {
  await loginViaUI(page, { irA: '/fiscal' });
  for (const ruta of ['/fiscal', '/fiscal/configuracion', '/fiscal/certificados']) {
    await page.goto(ruta);
    const desborda = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    expect(desborda, ruta).toBe(false);
  }
});
