// @ts-check
// TEST 16 — V1.13: proveedores con categorías, datos fiscales, evaluación, advertencia al comprar,
// reclamo con cambio de estado y documentación fiscal, todo desde la UI real.
// Las reglas (inmutabilidad, transiciones, proveedor identificable de un ajuste, idempotencia, permisos)
// están en pytest; acá se comprueba que el flujo funciona en un navegador y que la ficha no desborda.
const { test, expect } = require('@playwright/test');
const { loginViaUI, registrarListenersDeErrores } = require('./helpers');

const SUFIJO = Date.now().toString().slice(-6);
const PROVEEDOR = `Distribuidora V113 ${SUFIJO}`;

async function idDeProveedor(page, nombre) {
  await page.goto(`/proveedores?q=${encodeURIComponent(nombre)}`);
  const href = await page.locator('#lista-proveedores a', { hasText: nombre }).first().getAttribute('href');
  const id = /\/proveedores\/(\d+)/.exec(href || '')?.[1];
  expect(id).toBeTruthy();
  return id;
}

test('proveedor V1.13: categorías, CUIT, evaluación baja con advertencia, reclamo y documento', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/productos/categorias' });

  // Dos categorías del catálogo (las mismas que usan los productos).
  for (const nombre of [`Bebidas ${SUFIJO}`, `Limpieza ${SUFIJO}`]) {
    await page.getByPlaceholder('Nombre de la categoría').fill(nombre);
    await page.getByRole('button', { name: 'Crear', exact: true }).click();
    await expect(page.locator('#toast-container')).toContainText(nombre);
  }

  // Alta con varias categorías y datos fiscales.
  await page.goto('/proveedores/nuevo');
  await page.getByLabel('Nombre comercial').fill(PROVEEDOR);
  await page.locator('#campos-categorias label', { hasText: `Bebidas ${SUFIJO}` }).click();
  await page.locator('#campos-categorias label', { hasText: `Limpieza ${SUFIJO}` }).click();
  await page.getByLabel('Razón social').fill('Distribuidora V113 SRL');
  await page.getByLabel('CUIT').fill('30-71234567-1');
  await page.getByLabel('Condición fiscal').selectOption('RESPONSABLE_INSCRIPTO');
  await page.getByRole('button', { name: 'Crear proveedor' }).click();
  await expect(page.locator('#toast-container')).toContainText(PROVEEDOR);

  const id = await idDeProveedor(page, PROVEEDOR);
  await page.goto(`/proveedores/${id}`);
  await expect(page.locator('#categorias-proveedor')).toContainText(`Bebidas ${SUFIJO}`);
  await expect(page.locator('#categorias-proveedor')).toContainText(`Limpieza ${SUFIJO}`);
  await expect(page.locator('#cuit-proveedor')).toHaveText('30-71234567-1');
  await expect(page.locator('#sin-calificacion')).toBeVisible();

  // Evaluación de 2 estrellas con un criterio.
  await page.locator('#nueva-evaluacion summary').click();
  await page.locator('#form-evaluacion label', { hasText: '2 ★' }).click();
  await page.getByLabel('Comentario').fill('Entregas incompletas');
  await page.locator('#form-evaluacion select[name="entrega"]').selectOption('1');
  await page.getByRole('button', { name: 'Guardar evaluación' }).click();
  await expect(page.locator('#calificacion-actual [data-estrellas="2"]').first()).toBeVisible();
  await expect(page.locator('#calificacion-actual')).toContainText('Entregas incompletas');

  // Al cargar una compra, la advertencia aparece pero no bloquea nada.
  await page.goto('/compras/nueva');
  const aviso = page.locator('#advertencia-calificacion');
  await expect(aviso).toBeHidden();
  await page.locator('#proveedor-compra').selectOption({ label: PROVEEDOR });
  await expect(aviso).toBeVisible();
  await expect(aviso).toContainText('2 de 5');
  await expect(page.locator('#form-compra button[type="submit"]')).toBeEnabled();

  // Reclamo desde la ficha y cambio de estado.
  await page.goto(`/proveedores/${id}`);
  await page.locator('#boton-nuevo-reclamo').click();
  await page.getByLabel('Tipo').selectOption('ENTREGA');
  await page.getByLabel('Descripción').fill('Faltaron 3 cajas');
  await page.getByRole('button', { name: 'Registrar reclamo' }).click();
  await expect(page.locator('#estado-reclamo')).toHaveAttribute('data-estado', 'PENDIENTE');
  await page.locator('#form-estado-reclamo label', { hasText: 'Reconocido' }).click();
  await page.getByLabel('Observación / resolución').fill('Las repone en la próxima entrega');
  await page.getByRole('button', { name: 'Registrar cambio' }).click();
  await expect(page.locator('#estado-reclamo')).toHaveAttribute('data-estado', 'RECONOCIDO');
  await expect(page.locator('#historial-reclamo')).toContainText('Pendiente → Reconocido');

  // Documento fiscal declarado.
  await page.goto(`/proveedores/${id}`);
  await page.locator('#nuevo-documento summary').click();
  await page.locator('#form-documento select[name="tipo"]').selectOption('CONSTANCIA_FISCAL');
  await page.locator('#form-documento input[name="fecha_documento"]').fill('2026-01-15');
  await page.getByRole('button', { name: 'Guardar documento' }).click();
  await expect(page.locator('#documentos-proveedor [data-tipo="CONSTANCIA_FISCAL"]')).toContainText('2026-01-15');
  await expect(page.locator('#resumen-reclamos')).toContainText('1 abierto(s)');

  // El listado lo encuentra por las dos dimensiones, cada una por separado.
  await page.goto('/proveedores?filtro=baja_calificacion');
  await expect(page.locator('#lista-proveedores')).toContainText(PROVEEDOR);
  await page.goto('/proveedores?filtro=reclamos_abiertos');
  await expect(page.locator('#lista-proveedores')).toContainText(PROVEEDOR);

  errores.afirmarSinErrores();
});

test('@responsive ficha, listado y formularios de proveedor sin scroll horizontal', async ({ page }) => {
  await loginViaUI(page, { irA: '/proveedores' });
  await page.request.post('/proveedores/nuevo', { form: { nombre: `Responsive ${SUFIJO}` } });
  const id = await idDeProveedor(page, `Responsive ${SUFIJO}`);
  for (const ruta of ['/proveedores', `/proveedores/${id}`, '/proveedores/nuevo', `/proveedores/${id}/editar`,
    `/proveedores/reclamos/nuevo?proveedor_id=${id}`]) {
    await page.goto(ruta);
    const desborda = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
    );
    expect(desborda, ruta).toBe(false);
  }
});
