// @ts-check
// TEST 1 — LOGIN + POS (Fase 5E.2). Reemplaza al smoke test de 5E.1 con
// el mismo flujo, ahora usando los helpers compartidos de la suite.
const { test, expect } = require('@playwright/test');
const { USUARIO, PASSWORD, registrarListenersDeErrores } = require('./helpers');

test('login real -> POS: redirect, formulario, y carga sin errores', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);

  // Navegar a /ventas sin sesión: la app real redirige (303, nunca
  // considerado error) a /login?next=/ventas -- se ejercita ese
  // mecanismo real (interfaces/web/app.py::manejar_no_autenticado), no
  // un atajo directo a /login.
  await page.goto('/ventas');
  await expect(page).toHaveURL(/\/login\?next=\/ventas/);

  const campoUsuario = page.locator('input[name="nombre_usuario"]');
  const campoPassword = page.locator('input[name="password"]');
  const botonIngresar = page.getByRole('button', { name: 'Ingresar' });

  await expect(campoUsuario).toBeVisible();
  await expect(campoPassword).toBeVisible();
  await expect(botonIngresar).toBeVisible();

  await campoUsuario.fill(USUARIO);
  await campoPassword.fill(PASSWORD);
  await botonIngresar.click();

  await expect(page).toHaveURL(/\/ventas$/);
  await expect(page.getByRole('heading', { name: 'Punto de venta' })).toBeVisible();
  await expect(page.getByLabel('Código de barras')).toBeVisible();

  errores.afirmarSinErrores();
});
