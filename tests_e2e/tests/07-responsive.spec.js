// @ts-check
// TEST 7 — RESPONSIVE (Fase 5E.2). Un único test, ejecutado contra los
// 4 proyectos de viewport (`npm run test:e2e:responsive`, ver
// playwright.config.js): 375x812, 768x1024, 1024x768, 1440x900. No hace
// falta un archivo por ancho -- Playwright reutiliza el mismo test con
// el `page`/viewport que cada proyecto define.
//
// El corte <1024 / >=1024 coincide exactamente con el breakpoint `lg:`
// de Tailwind que ya usa pos.html (`min-width: 1024px`), así que
// `desktop-1024` (1024px de ancho) cae del lado ">=1024" a propósito:
// es el mismo comportamiento real de la app, no una decisión arbitraria
// del test.
const { test, expect } = require('@playwright/test');
const {
  loginViaUI,
  agregarPorCodigoBarras,
  seleccionarMedioDePago,
  registrarListenersDeErrores,
  PRODUCTO_1,
} = require('./helpers');

test('@responsive POS y carrito accesibles e interactuables según el viewport', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page);

  // Sin overflow horizontal inesperado.
  const hayOverflowHorizontal = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
  );
  expect(hayOverflowHorizontal).toBe(false);

  await expect(page.getByRole('heading', { name: 'Punto de venta' })).toBeVisible();
  await expect(page.getByLabel('Código de barras')).toBeVisible();

  const ancho = page.viewportSize()?.width ?? 0;

  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);

  if (ancho < 1024) {
    // Mobile/tablet: el bloque de tipo de pago/monto recibido vive
    // DENTRO de #carrito-sheet, que en este ancho arranca fuera de
    // pantalla (`translate-y-full`, ver pos.html) hasta que se abre
    // tocando la barra inferior -- hay que abrirlo antes de poder
    // seleccionar el medio de pago o tipear el monto (si no, Playwright
    // agota el timeout esperando un elemento "fuera del viewport": no es
    // un bug de la app, es el mismo orden que seguiría un usuario real).
    const barraInferior = page.locator('#carrito-bar');
    await expect(barraInferior).toBeVisible();
    await expect(page.locator('#carrito-bar-cantidad')).toHaveText('1');

    const toggle = page.locator('#carrito-bar-toggle');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#carrito-lista')).toBeVisible();

    await seleccionarMedioDePago(page, 'EFECTIVO');
    await page.getByLabel('Monto recibido').fill('5.00');

    // Una vez abierto el sheet, el botón real a tocar es el de adentro
    // (#boton-cobrar), igual que vería el cajero -- no el de la barra,
    // que queda detrás del sheet abierto.
    const botonCobrar = page.locator('#boton-cobrar');
    await expect(botonCobrar).toBeEnabled();

    const [respuesta] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/api/ventas') && r.request().method() === 'POST'),
      botonCobrar.click(),
    ]);
    expect(respuesta.status()).toBe(200);
  } else {
    // Desktop: sidebar del carrito siempre visible, la barra mobile no
    // debe estar en pantalla.
    await expect(page.locator('#carrito-sheet')).toBeVisible();
    await expect(page.locator('#carrito-bar')).toBeHidden();

    await seleccionarMedioDePago(page, 'EFECTIVO');
    await page.getByLabel('Monto recibido').fill('5.00');

    const botonCobrar = page.locator('#boton-cobrar');
    await expect(botonCobrar).toBeEnabled();

    const [respuesta] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/api/ventas') && r.request().method() === 'POST'),
      botonCobrar.click(),
    ]);
    expect(respuesta.status()).toBe(200);
  }

  errores.afirmarSinErrores();
});
