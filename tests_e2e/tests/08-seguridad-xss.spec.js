// @ts-check
// TEST 8 — SEGURIDAD: XSS ALMACENADO EN EL CARRITO (Fase 5E.3,
// corrección de auditoría).
//
// Reproduce el vector exacto que existía en pos.js::renderizarCarrito()
// antes de la corrección: un nombre de producto que combina una comilla
// doble seguida de un atributo nuevo (rompía `aria-label="..."`, el
// vector más grave) y un `<img onerror=...>` (se ejecuta igual insertado
// vía `innerHTML`, a diferencia de `<script>`). No construye un
// "exploit" contra la app real: corre contra el código YA corregido y
// verifica que ninguno de esos vectores sigue funcionando.
const { test, expect } = require('@playwright/test');
const { loginViaUI, agregarPorCodigoBarras, registrarListenersDeErrores, PRODUCTO_XSS } = require('./helpers');

test('nombre de producto malicioso: texto literal, sin ejecución de JS, sin romper atributos', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page);

  await agregarPorCodigoBarras(page, PRODUCTO_XSS.codigoBarras);

  // Único ítem en un carrito recién iniciado.
  const fila = page.locator('#carrito-lista > div').first();
  await expect(fila).toBeVisible();

  // 1) Ni el <img onerror=...> ni el onmouseover inyectado se
  //    ejecutaron -- la variable que ambos payloads intentan setear
  //    debe seguir sin existir.
  const xssEjecutado = await page.evaluate(() => /** @type {any} */ (window).__xssEjecutado);
  expect(xssEjecutado).toBeUndefined();

  // 2) El nombre se ve como TEXTO LITERAL completo (comillas, "<", ">"
  //    y "&" incluidos), no interpretado como marcado.
  await expect(fila).toContainText(PRODUCTO_XSS.nombre);

  // 3) No se creó ningún elemento HTML real a partir del nombre: si el
  //    navegador hubiera parseado el nombre como markup, existirían.
  expect(await fila.locator('img').count()).toBe(0);
  expect(await fila.locator('script').count()).toBe(0);
  expect(await fila.locator('b').count()).toBe(0);

  // 4) Los tres botones de acción existen exactamente una vez cada uno
  //    (una comilla suelta rompiendo el atributo podría duplicar/cortar
  //    elementos) y su aria-label sigue conteniendo el nombre completo
  //    como valor de atributo literal -- nunca "escapó" de las comillas.
  const botonRestar = fila.locator('[data-accion="restar"]');
  const botonSumar = fila.locator('[data-accion="sumar"]');
  const botonQuitar = fila.locator('[data-accion="quitar"]');
  await expect(botonRestar).toHaveCount(1);
  await expect(botonSumar).toHaveCount(1);
  await expect(botonQuitar).toHaveCount(1);

  expect(await botonRestar.getAttribute('aria-label')).toContain(PRODUCTO_XSS.nombre);
  expect(await botonSumar.getAttribute('aria-label')).toContain(PRODUCTO_XSS.nombre);
  expect(await botonQuitar.getAttribute('aria-label')).toContain(PRODUCTO_XSS.nombre);

  // 5) El carrito sigue siendo completamente funcional con este
  //    producto: sumar/restar/quitar funcionan igual que con cualquier
  //    nombre normal (Playwright re-resuelve los locators contra el DOM
  //    vigente en cada acción, así que siguen siendo válidos aunque
  //    renderizarCarrito() reconstruya la fila en cada click).
  await expect(page.locator('#carrito-total')).toHaveText('$1.00');
  await botonSumar.click();
  await expect(page.locator('#carrito-total')).toHaveText('$2.00');
  await botonRestar.click();
  await expect(page.locator('#carrito-total')).toHaveText('$1.00');
  await botonQuitar.click();
  await expect(page.locator('#carrito-lista > div')).toHaveCount(0);
  await expect(page.locator('#carrito-total')).toHaveText('$0.00');

  errores.afirmarSinErrores();
});
