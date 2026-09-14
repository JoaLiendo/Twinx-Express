// @ts-check
// TEST 3 — EFECTIVO + VUELTO (Fase 5E.2). Nunca hace click en "Cobrar":
// solo verifica el estado del bloque de efectivo/vuelto (Fase 5C), sin
// tocar la DB.
const { test, expect } = require('@playwright/test');
const { loginViaUI, agregarPorCodigoBarras, seleccionarMedioDePago, registrarListenersDeErrores, PRODUCTO_1 } = require('./helpers');

test('efectivo/vuelto: habilita y deshabilita Cobrar según el monto', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page);

  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras); // total = $2.50

  const radioEfectivo = page.locator('input[name="tipo_pago"][value="EFECTIVO"]');
  const inputMonto = page.getByLabel('Monto recibido');
  const vueltoResultado = page.locator('#vuelto-resultado');
  const botonCobrar = page.locator('#boton-cobrar');
  const bloqueEfectivo = page.locator('#bloque-efectivo');

  // EFECTIVO ya viene seleccionado por defecto (es el primero en orden
  // alfabético, ver domain.venta.TIPOS_PAGO_VALIDOS) -- se confirma en
  // vez de asumirlo.
  await expect(radioEfectivo).toBeChecked();
  await expect(bloqueEfectivo).toBeVisible();

  // Importe superior al total -> vuelto correcto, Cobrar habilitado.
  await inputMonto.fill('5.00');
  await expect(vueltoResultado).toHaveText('Vuelto: $2.50');
  await expect(botonCobrar).toBeEnabled();

  // Importe insuficiente -> Cobrar deshabilitado.
  await inputMonto.fill('1.00');
  await expect(vueltoResultado).toContainText('Falta');
  await expect(botonCobrar).toBeDisabled();

  // Importe inválido (no numérico) -> tampoco permite cobrar.
  await inputMonto.fill('abc');
  await expect(botonCobrar).toBeDisabled();

  // Cambiar a TARJETA -> el bloque de efectivo se oculta, y como TARJETA
  // no depende de ningún monto, Cobrar vuelve a habilitarse.
  await seleccionarMedioDePago(page, 'TARJETA');
  await expect(bloqueEfectivo).toBeHidden();
  await expect(botonCobrar).toBeEnabled();

  // Volver a EFECTIVO -> se recalcula en el momento: el 'abc' inválido
  // sigue cargado en el input (nunca se limpia al cambiar de medio de
  // pago), así que Cobrar debe seguir deshabilitado, no quedar "pegado"
  // en el estado habilitado de TARJETA.
  await seleccionarMedioDePago(page, 'EFECTIVO');
  await expect(bloqueEfectivo).toBeVisible();
  await expect(inputMonto).toHaveValue('abc');
  await expect(botonCobrar).toBeDisabled();

  errores.afirmarSinErrores();
});
