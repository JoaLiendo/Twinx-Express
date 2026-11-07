// @ts-check
// TEST 20 — POS: selección manual de productos en lista (búsqueda y filtros en el navegador).
//
// Crea por la UI real una categoría con subcategoría y dos productos propios (uno sin stock), para no
// depender del stock que dejan los tests anteriores sobre los productos sembrados.
const { test, expect } = require('@playwright/test');
const {
  loginViaUI,
  agregarPorCodigoBarras,
  agregarDesdeLista,
  filaDelListado,
  filaDelCarrito,
  registrarListenersDeErrores,
  PRODUCTO_1,
  PRODUCTO_2,
} = require('./helpers');

const SUFIJO = Date.now().toString().slice(-6);
const PRINCIPAL = `Bebidas ${SUFIJO}`;
const SUB = `Gaseosas ${SUFIJO}`;
const EN_SUB = { nombre: `Pomelo Listado ${SUFIJO}`, codigo: `55${SUFIJO}01`, stock: '7' };
const AGOTADO = { nombre: `Tónica Agotada ${SUFIJO}`, codigo: `55${SUFIJO}02`, stock: '0' };

async function crearProducto(page, producto, categoria) {
  await page.goto('/productos/nuevo');
  await page.locator('input[name="codigo_barras"]').fill(producto.codigo);
  await page.locator('input[name="nombre"]').fill(producto.nombre);
  await page.locator('input[name="precio_costo"]').fill('1.00');
  await page.locator('input[name="precio_venta"]').fill('3.00');
  await page.locator('select[name="categoria_id"]').selectOption({ label: categoria });
  await page.locator('input[name="stock_actual"]').fill(producto.stock);
  await page.getByRole('button', { name: 'Crear producto' }).click();
  await expect(page.locator('#toast-container')).toContainText(producto.nombre);
}

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage();
  await loginViaUI(page, { irA: '/productos/categorias' });
  await page.getByPlaceholder('Nombre de la categoría').fill(PRINCIPAL);
  await page.getByRole('button', { name: 'Crear', exact: true }).click();
  await expect(page.locator('#toast-container')).toContainText(PRINCIPAL);
  await page.getByPlaceholder('Nombre de la categoría').fill(SUB);
  await page.getByLabel('Ubicación').selectOption({ label: `Dentro de: ${PRINCIPAL}` });
  await page.getByRole('button', { name: 'Crear', exact: true }).click();
  await expect(page.locator('#arbol-categorias [data-nivel="2"]', { hasText: SUB })).toBeVisible();

  await crearProducto(page, EN_SUB, `${PRINCIPAL} › ${SUB}`);
  await crearProducto(page, AGOTADO, PRINCIPAL);
  await page.close();
});

const busqueda = (page) => page.getByLabel('Buscar producto');
const filasVisibles = (page) => page.locator('#listado-productos tr[data-producto-fila]:visible');
const contador = (page) => page.locator('#listado-contador');

test('la selección manual es una lista con columnas, no una grilla de tarjetas', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page);

  await expect(page.locator('[data-producto-card]')).toHaveCount(0);
  const cabecera = page.locator('#listado-productos thead th');
  await expect(cabecera).toHaveText(['Código', 'Producto', 'Categoría', 'Stock', 'Precio', 'Acción']);
  const total = await page.locator('#listado-productos tr[data-producto-fila]').count();
  await expect(filasVisibles(page)).toHaveCount(total);
  await expect(contador(page)).toHaveText(`${total} productos encontrados`);
  // El lector sigue teniendo el foco al entrar.
  await expect(page.getByLabel('Código de barras')).toBeFocused();

  errores.afirmarSinErrores();
});

test('búsqueda por nombre, código parcial y código de barras', async ({ page }) => {
  await loginViaUI(page);

  await busqueda(page).fill('pomelo listado');
  await expect(filasVisibles(page)).toHaveCount(1);
  await expect(filaDelListado(page, EN_SUB.nombre)).toBeVisible();

  await busqueda(page).fill(EN_SUB.codigo.slice(0, 8));
  await expect(filasVisibles(page)).toHaveCount(2);

  await busqueda(page).fill(PRODUCTO_2.codigoBarras);
  await expect(filasVisibles(page)).toHaveCount(1);
  await expect(filaDelListado(page, PRODUCTO_2.nombre)).toBeVisible();
  await expect(contador(page)).toContainText('1 producto encontrado');
});

test('filtro por categoría incluye subcategorías y se combina con la búsqueda', async ({ page }) => {
  await loginViaUI(page);
  const categoria = page.getByLabel('Categoría', { exact: true });

  await categoria.selectOption({ label: PRINCIPAL });
  await expect(filasVisibles(page)).toHaveCount(2);

  await categoria.selectOption({ label: `— ${SUB}` });
  await expect(filasVisibles(page)).toHaveCount(1);
  await expect(filaDelListado(page, EN_SUB.nombre)).toBeVisible();

  await categoria.selectOption({ label: PRINCIPAL });
  await busqueda(page).fill('tonica');
  await expect(filasVisibles(page)).toHaveCount(1);
  await expect(filaDelListado(page, AGOTADO.nombre)).toBeVisible();
});

test('filtros de stock: con stock y sin stock; el agotado no se puede agregar', async ({ page }) => {
  await loginViaUI(page);
  const stock = page.getByLabel('Stock', { exact: true });
  await page.getByLabel('Categoría', { exact: true }).selectOption({ label: PRINCIPAL });

  await stock.selectOption('con_stock');
  await expect(filasVisibles(page)).toHaveCount(1);
  await expect(filaDelListado(page, EN_SUB.nombre)).toBeVisible();

  await stock.selectOption('sin_stock');
  await expect(filasVisibles(page)).toHaveCount(1);
  const agotado = filaDelListado(page, AGOTADO.nombre);
  await expect(agotado).toContainText('Sin stock');
  await expect(agotado.locator('[data-agregar]')).toBeDisabled();
  // `force`: Playwright no clickea un aria-disabled; un cajero con el mouse sí puede.
  await agotado.locator('td').nth(1).click({ force: true });
  await expect(page.locator('#carrito-lista > div')).toHaveCount(0);
});

test('sin coincidencias muestra el mensaje y limpiar restablece todo', async ({ page }) => {
  await loginViaUI(page);
  const total = await page.locator('#listado-productos tr[data-producto-fila]').count();

  await busqueda(page).fill('no-existe-ningun-producto');
  await page.getByLabel('Stock', { exact: true }).selectOption('con_stock');
  await expect(page.getByText('No encontramos productos con esos filtros.')).toBeVisible();
  await expect(filasVisibles(page)).toHaveCount(0);

  await page.getByRole('button', { name: 'Limpiar filtros' }).click();
  await expect(busqueda(page)).toHaveValue('');
  await expect(page.getByLabel('Stock', { exact: true })).toHaveValue('');
  await expect(filasVisibles(page)).toHaveCount(total);
  await expect(page.getByText('No encontramos productos con esos filtros.')).toBeHidden();

  await busqueda(page).fill('alfajor');
  await page.locator('#limpiar-filtros').click();
  await expect(filasVisibles(page)).toHaveCount(total);
});

test('agregar desde la lista: botón, fila completa y Enter con resultado único', async ({ page }) => {
  await loginViaUI(page);

  await agregarDesdeLista(page, PRODUCTO_1.nombre);
  await expect(filaDelCarrito(page, PRODUCTO_1.nombre)).toBeVisible();
  // Tras el clic el foco vuelve al lector, para seguir escaneando.
  await expect(page.getByLabel('Código de barras')).toBeFocused();

  await filaDelListado(page, PRODUCTO_1.nombre).locator('td').nth(1).click();
  await expect(filaDelCarrito(page, PRODUCTO_1.nombre)).toContainText('2');

  await busqueda(page).fill('pomelo listado');
  await expect(contador(page)).toContainText('Enter');
  await busqueda(page).press('Enter');
  await expect(filaDelCarrito(page, EN_SUB.nombre)).toBeVisible();
  await expect(busqueda(page)).toBeFocused();

  // Ambiguo: Enter no agrega nada y avisa.
  await busqueda(page).fill('E2E');
  await busqueda(page).press('Enter');
  await expect(page.locator('#toast-container')).toContainText('usá ↑ ↓ para elegir uno');
  await expect(page.locator('#carrito-lista > div')).toHaveCount(2);

  // Esc limpia la búsqueda; un segundo Esc devuelve el foco al lector.
  await busqueda(page).press('Escape');
  await expect(busqueda(page)).toHaveValue('');
  await busqueda(page).press('Escape');
  await expect(page.getByLabel('Código de barras')).toBeFocused();
});

test('el lector sigue funcionando igual, también con filtros activos', async ({ page }) => {
  await loginViaUI(page);

  await busqueda(page).fill('pomelo');
  await page.getByLabel('Código de barras').click();
  await agregarPorCodigoBarras(page, PRODUCTO_2.codigoBarras);
  await expect(filaDelCarrito(page, PRODUCTO_2.nombre)).toBeVisible();
  await expect(page.getByLabel('Código de barras')).toHaveValue('');
  // El filtro de la lista no se tocó.
  await expect(busqueda(page)).toHaveValue('pomelo');
});
