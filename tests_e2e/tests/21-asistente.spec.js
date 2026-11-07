// @ts-check
// TEST 21 — TWINX ASISTENTE (V1.18): panel lateral de chat de solo lectura.
//
// Sin Ollama en la máquina de pruebas: el estado tiene que decir "Ayuda integrada" y las preguntas
// objetivo se responden igual con datos reales. Se prueba la pregunta, la respuesta con origen visible,
// los accesos a reportes, el cierre, el estado del motor cuando está configurado pero apagado y que el
// CASHIER no obtiene información del dueño aunque la pida por el chat.
const { test, expect } = require('@playwright/test');
const { loginViaUI, realizarVentaSimple, registrarListenersDeErrores, USUARIO_CASHIER } = require('./helpers');

const abrirAsistente = async (page) => {
  await page.getByRole('button', { name: 'Abrir asistente' }).click();
  await expect(page.locator('#asistente-panel')).toBeVisible();
  // El estado y las sugeridas llegan por fetch al abrir: se espera a que estén para no leer una lista vacía.
  await expect(page.locator('#asistente-sugeridas button').first()).toBeVisible();
};

const preguntar = async (page, texto) => {
  await page.locator('#asistente-input').fill(texto);
  await page.locator('#asistente-input').press('Enter');
  const respuesta = page.locator('#asistente-mensajes article[data-rol="asistente"]').last();
  await expect(respuesta).toBeVisible();
  await expect(page.locator('#asistente-mensajes [data-pensando]')).toHaveCount(0);
  return respuesta;
};

test.describe.configure({ mode: 'serial' });

test('el dueño pregunta cuánto vendió hoy y recibe cifras reales con acceso al reporte', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  // realizarVentaSimple inicia sesión por su cuenta (helpers.js).
  await realizarVentaSimple(page);
  await page.goto('/');

  await abrirAsistente(page);
  await expect(page.locator('#asistente-estado')).toHaveText('Ayuda integrada');
  await expect(page.locator('#asistente-estado')).toHaveAttribute('data-estado', 'integrado');
  await expect(page.locator('#asistente-sugeridas button')).toHaveCount(4);

  const respuesta = await preguntar(page, '¿Cuánto vendí hoy?');
  await expect(respuesta).toContainText('Ventas de hoy');
  await expect(respuesta).toContainText('$');
  await expect(respuesta.locator('.chat-origen')).toHaveText('Ayuda integrada');
  const enlace = respuesta.getByRole('link', { name: 'Reporte de ventas' });
  await expect(enlace).toHaveAttribute('href', /^\/reportes\?fecha_desde=/);

  // Historial corto en la misma pestaña: al navegar al reporte, el panel conserva la conversación.
  await enlace.click();
  await expect(page).toHaveURL(/\/reportes\?fecha_desde=/);
  await abrirAsistente(page);
  await expect(page.locator('#asistente-mensajes article')).toHaveCount(2);

  // Cerrar con el botón y con Esc.
  await page.getByRole('button', { name: 'Cerrar asistente' }).click();
  await expect(page.locator('#asistente-panel')).toBeHidden();
  await abrirAsistente(page);
  await page.keyboard.press('Escape');
  await expect(page.locator('#asistente-panel')).toBeHidden();
  errores.afirmarSinErrores();
});

test('una pregunta sugerida y una guía de uso responden sin IA y con enlaces a la Ayuda', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/compras' });
  await abrirAsistente(page);
  await page.locator('#asistente-sugeridas button', { hasText: '¿Cómo registro una compra?' }).click();
  const respuesta = page.locator('#asistente-mensajes article[data-rol="asistente"]').last();
  await expect(respuesta).toContainText('Registrar una compra');
  await expect(respuesta.locator('ol li')).toHaveCount(4);
  await expect(respuesta.getByRole('link', { name: /Ayuda/ })).toHaveAttribute('href', '/ayuda#compras');
  await expect(respuesta.getByRole('link', { name: 'Nueva compra' })).toHaveAttribute('href', '/compras/nueva');

  const sinRespuesta = await preguntar(page, '¿Qué opinás del clima?');
  await expect(sinRespuesta).toContainText('No reconocí la pregunta');
  errores.afirmarSinErrores();
});

test('el cajero no obtiene márgenes ni reposición por el chat y ve solo sus sugeridas', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/productos', usuario: USUARIO_CASHIER });
  await abrirAsistente(page);
  const sugeridas = await page.locator('#asistente-sugeridas button').allTextContents();
  expect(sugeridas).not.toContain('¿Qué tengo que reponer?');
  expect(sugeridas).toContain('¿Cuáles están por vencer?');

  const margen = await preguntar(page, '¿Qué productos tienen margen negativo?');
  await expect(margen).toHaveText(/Esa información es solo para el dueño/);
  await expect(margen).not.toContainText('$');

  const ventas = await preguntar(page, '¿Cuánto vendí hoy?');
  await expect(ventas).toContainText('caja');
  await expect(ventas).not.toContainText('margen');
  errores.afirmarSinErrores();
});

test('con IA local configurada pero apagada, el panel lo dice y la ayuda integrada sigue respondiendo', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/configuracion' });
  const seccion = page.locator('#configuracion-asistente');
  await seccion.locator('select[name="motor"]').selectOption('ollama');
  await seccion.locator('input[name="url"]').fill('http://127.0.0.1:9');
  await seccion.locator('input[name="modelo"]').fill('llama3.2:3b');
  await seccion.getByRole('button', { name: 'Guardar' }).click();
  await expect(page.locator('#toast-container')).toContainText('Asistente');

  await abrirAsistente(page);
  await expect(page.locator('#asistente-estado')).toHaveText('IA local no disponible · ayuda integrada');
  await expect(page.locator('#asistente-estado')).toHaveAttribute('data-estado', 'sin-ia');
  const respuesta = await preguntar(page, '¿Cuáles están por vencer?');
  await expect(respuesta.locator('.chat-origen')).toHaveText('Ayuda integrada');

  // Una pregunta libre no inventa nada: avisa que la IA no respondió.
  const libre = await preguntar(page, 'contame un chiste');
  await expect(libre).toContainText('No reconocí la pregunta');
  await expect(libre.locator('.chat-aviso')).toContainText('IA local no disponible');

  // Vuelta a la ayuda integrada para no condicionar otros tests.
  await page.goto('/configuracion');
  await seccion.locator('select[name="motor"]').selectOption('integrado');
  await seccion.getByRole('button', { name: 'Guardar' }).click();
  await expect(page.locator('#toast-container')).toContainText('Asistente');
  errores.afirmarSinErrores();
});

test('@responsive el panel del asistente se usa en cualquier ancho sin desbordar', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page);
  await abrirAsistente(page);
  const ancho = page.viewportSize()?.width ?? 0;
  const caja = await page.locator('#asistente-panel').boundingBox();
  expect(caja).not.toBeNull();
  if (ancho < 768) {
    expect(Math.round(caja?.width ?? 0)).toBeGreaterThanOrEqual(ancho - 2);
  } else {
    expect(caja?.width ?? 0).toBeLessThan(ancho);
  }
  const hayOverflowHorizontal = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  );
  expect(hayOverflowHorizontal).toBe(false);
  const respuesta = await preguntar(page, '¿Cuánto me deben los clientes?');
  await expect(respuesta).toContainText(/cliente/i);
  await page.getByRole('button', { name: 'Cerrar asistente' }).click();
  await expect(page.locator('#asistente-panel')).toBeHidden();
  errores.afirmarSinErrores();
});
