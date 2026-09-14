// @ts-check
// TEST 2 — AGREGAR PRODUCTOS AL CARRITO (Fase 5E.2).
//
// Todo este test es 100% client-side (nunca hace click en "Cobrar"):
// no toca la DB, no descuenta stock, no interfiere con ningún otro test
// que comparta la misma DB temporal dentro de esta corrida.
const { test, expect } = require('@playwright/test');
const {
  loginViaUI,
  agregarPorCodigoBarras,
  agregarPorTarjeta,
  filaDelCarrito,
  registrarListenersDeErrores,
  PRODUCTO_1,
  PRODUCTO_2,
} = require('./helpers');

test('carrito: agregar, cantidades, subtotal/total, incrementar/decrementar/eliminar', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page);

  // Producto 1 por el flujo real del lector de código de barras.
  await agregarPorCodigoBarras(page, PRODUCTO_1.codigoBarras);
  // Producto 2 por el flujo real de selección manual (click en la card).
  await agregarPorTarjeta(page, PRODUCTO_2.nombre);

  const filaProducto1 = filaDelCarrito(page, PRODUCTO_1.nombre);
  const filaProducto2 = filaDelCarrito(page, PRODUCTO_2.nombre);
  await expect(filaProducto1).toBeVisible();
  await expect(filaProducto2).toBeVisible();

  // Total = $2.50 + $5.00 = $7.50. Se verifica la cantidad indirectamente
  // vía el subtotal de cada línea (cantidad x precio unitario), que es
  // una prueba más precisa que leer el número de un <span> sin nombre
  // accesible ni atributo estable.
  await expect(page.locator('#carrito-total')).toHaveText('$7.50');
  await expect(filaProducto1).toContainText('$2.50'); // 1 x $2.50
  await expect(filaProducto2).toContainText('$5.00'); // 1 x $5.00
  await expect(filaProducto1.locator('span.w-5')).toHaveText('1');

  // Incrementar Producto 1 -> 2 x $2.50 = $5.00 de subtotal esa línea.
  await page.getByRole('button', { name: `Sumar unidad de ${PRODUCTO_1.nombre}` }).click();
  await expect(filaProducto1).toContainText('$5.00');
  await expect(page.locator('#carrito-total')).toHaveText('$10.00'); // $5.00 + $5.00
  await expect(filaProducto1.locator('span.w-5')).toHaveText('2');

  // Decrementar Producto 1 de vuelta a 1 unidad.
  await page.getByRole('button', { name: `Restar unidad de ${PRODUCTO_1.nombre}` }).click();
  await expect(filaProducto1).toContainText('$2.50');
  await expect(page.locator('#carrito-total')).toHaveText('$7.50');
  await expect(filaProducto1.locator('span.w-5')).toHaveText('1');

  // Eliminar Producto 2 -> solo queda Producto 1.
  await page.getByRole('button', { name: `Quitar ${PRODUCTO_2.nombre} del carrito` }).click();
  // renderizarCarrito() reconstruye #carrito-lista desde cero: la fila ya
  // no existe en el DOM (no es solo un ocultamiento por CSS).
  await expect(filaProducto2).toHaveCount(0);
  await expect(filaProducto1).toBeVisible();
  await expect(page.locator('#carrito-total')).toHaveText('$2.50');

  errores.afirmarSinErrores();
});
