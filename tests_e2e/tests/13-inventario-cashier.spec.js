// @ts-check
// TEST 13 — INVENTARIO FÍSICO COMO CASHIER, DE PUNTA A PUNTA (V1.5-G).
//
// Valida la política vigente, sin redefinirla: el CASHIER cuenta (a ciegas) pero no crea, revisa, confirma
// ni cancela inventarios ni entra a rutas OWNER-only. Un botón oculto no alcanza: se prueba el acceso directo
// por URL y por POST con la sesión real del CASHIER. Las reglas de negocio están en pytest
// (test_inventario.py, test_inventario_permisos.py); esto confirma el comportamiento desde el navegador.
const { test, expect } = require('@playwright/test');
const { loginViaUI, leerStockDeCard, registrarListenersDeErrores, USUARIO_CASHIER, PRODUCTO_2 } = require('./helpers');

/** El OWNER inicia un inventario manual con un solo producto; devuelve el id del inventario. */
async function iniciarInventarioComoOwner(page, nombreProducto) {
  await page.goto('/inventario/nuevo');
  await page.getByText('Elegir productos').click();
  await page
    .locator('label[data-fila-filtrable]', { hasText: nombreProducto })
    .locator('input[type="checkbox"]')
    .check();
  await page.getByRole('button', { name: 'Iniciar inventario' }).click();
  await page.waitForURL(/\/inventario\/\d+/);
  return Number(page.url().match(/\/inventario\/(\d+)/)[1]);
}

/** Cierra la sesión actual (cookies) y entra como CASHIER. */
async function cambiarACashier(page) {
  await page.context().clearCookies();
  await loginViaUI(page, { irA: '/inventario', usuario: USUARIO_CASHIER });
}

test('inventario CASHIER: cuenta a ciegas y no puede confirmar, cancelar ni entrar a rutas OWNER', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  page.on('dialog', (dialogo) => dialogo.accept());
  await loginViaUI(page, { irA: '/ventas' });
  const stockAntes = await leerStockDeCard(page, PRODUCTO_2.nombre);
  const idInventario = await iniciarInventarioComoOwner(page, PRODUCTO_2.nombre);
  await page.goto('/ventas');
  const idProducto = Number(
    await page.locator(`[data-producto-card][data-nombre="${PRODUCTO_2.nombre}"]`).getAttribute('data-id'),
  );

  // --- Navegación y conteo permitidos ---
  await cambiarACashier(page);
  await page.waitForURL(/\/inventario\/conteo$/); // /inventario redirige al conteo para el CASHIER
  const fila = page.locator('#lista-conteo li', { hasText: PRODUCTO_2.nombre });
  await expect(fila).toBeVisible();
  await expect(page.locator('#lista-conteo')).not.toContainText('Esperado'); // conteo a ciegas
  await expect(page.getByRole('link', { name: /Revisar y confirmar/ })).toHaveCount(0);
  await expect(page.locator('#progreso-conteo')).toHaveText('0 de 1 contados');

  await fila.locator('input[name="cantidad"]').fill('7');
  await fila.getByRole('button', { name: 'Guardar' }).click();
  await expect(fila.locator('[data-estado]')).toHaveText('Contado: 7');
  await expect(page.locator('#progreso-conteo')).toHaveText('1 de 1 contados');
  await fila.locator('input[name="cantidad"]').fill('9'); // recontar
  await fila.getByRole('button', { name: 'Recontar' }).click();
  await expect(fila.locator('[data-estado]')).toHaveText('Contado: 9');

  // --- Entrada hostil en el flujo permitido: error controlado (toast), nunca 500 ---
  for (const hostil of ['1e30', '-3', '99999999999999999999']) {
    await fila.locator('input[name="cantidad"]').fill(hostil);
    await fila.getByRole('button', { name: 'Recontar' }).click();
    await expect(page.locator('#toast-container')).toContainText(/cantidad|válid|rango|número/i);
    await expect(fila.locator('[data-estado]')).toHaveText('Contado: 9'); // el conteo válido no se pisó
  }
  const posteoHostil = await page.request.post(`/inventario/conteo/${idProducto}`, {
    form: { cantidad: 'abc' },
    maxRedirects: 0,
  });
  expect(posteoHostil.status()).toBe(303); // redirige con un toast de error, no 500
  const apiHostil = await page.request.post(`/api/inventario/conteo/${idProducto}`, { data: { cantidad: '5' } });
  expect(apiHostil.status()).toBe(422);

  // --- Acceso directo prohibido: 403 por URL y por POST, no solo botones ocultos ---
  for (const ruta of ['/inventario/nuevo', `/inventario/${idInventario}`, '/auditoria', '/empleados', '/reportes']) {
    const respuesta = await page.request.get(ruta, { maxRedirects: 0 });
    expect(respuesta.status(), `GET ${ruta}`).toBe(403);
  }
  for (const ruta of [
    `/inventario/${idInventario}/confirmar`,
    `/inventario/${idInventario}/cancelar`,
    '/inventario/nuevo',
  ]) {
    const respuesta = await page.request.post(ruta, { form: { alcance: 'todos' }, maxRedirects: 0 });
    expect(respuesta.status(), `POST ${ruta}`).toBe(403);
  }
  const navegacion = await page.goto(`/inventario/${idInventario}`);
  expect(navegacion && navegacion.status()).toBe(403);

  // --- Nada cambió: el inventario sigue abierto, con el conteo del CASHIER, y el stock intacto ---
  await page.context().clearCookies();
  await loginViaUI(page, { irA: `/inventario/${idInventario}` });
  await expect(page.locator('#form-confirmar-inventario')).toBeVisible();
  const lineaRevision = page.locator('#tabla-lineas-inventario tr', { hasText: PRODUCTO_2.nombre });
  await expect(lineaRevision.locator('[data-contado]')).toHaveText('9');
  await page.goto('/ventas');
  expect(await leerStockDeCard(page, PRODUCTO_2.nombre)).toBe(stockAntes);

  // Limpieza: el OWNER cancela para no dejar un inventario abierto a los demás specs.
  await page.goto(`/inventario/${idInventario}`);
  await page.getByRole('button', { name: 'Cancelar inventario' }).click();
  await page.waitForURL(/\/inventario(\?.*)?$/);

  // Los únicos 4xx del navegador son los esperados (rechazo controlado del valor enorme y 403 de la URL directa).
  expect(errores.respuestasHttpInesperadas.map((r) => r.replace(/^(\d+) https?:\/\/[^/]+/, '$1 '))).toEqual([
    `422 /api/inventario/conteo/${idProducto}`,
    `403 /inventario/${idInventario}`,
  ]);
  // Chrome informa esos mismos dos 4xx como console.error de recurso; no hay ningún otro error. El texto
  // de la frase de estado (p. ej. «Unprocessable Entity» / «Unprocessable Content») depende de la versión
  // del servidor, así que se compara solo el código.
  expect(errores.erroresDeNavegador.map((e) => e.replace(/(status of \d+).*$/, '$1'))).toEqual([
    'console.error: Failed to load resource: the server responded with a status of 422',
    'console.error: Failed to load resource: the server responded with a status of 403',
  ]);
  errores.respuestasHttpInesperadas.length = 0;
  errores.erroresDeNavegador.length = 0;
  errores.afirmarSinErrores();
});

test('inventario CASHIER: sin inventario abierto ve el estado vacío y no puede iniciar uno', async ({ page }) => {
  await loginViaUI(page, { irA: '/inventario', usuario: USUARIO_CASHIER });
  await page.waitForURL(/\/inventario\/conteo$/);
  await expect(page.getByText('No hay ningún inventario abierto.')).toBeVisible();

  const respuesta = await page.request.post('/inventario/nuevo', { form: { alcance: 'todos' }, maxRedirects: 0 });
  expect(respuesta.status()).toBe(403);
});
