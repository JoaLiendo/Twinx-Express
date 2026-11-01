// @ts-check
// TEST 18 — BACKUPS (V1.15) DESDE EL NAVEGADOR.
//
// OWNER: estado sin redundancia externa, backup manual, segundo destino (válido e inválido) y backup
// automático al cerrar la caja. Los backups van a la carpeta temporal de la corrida (ver
// tests_e2e/servidor_pruebas.py). Validación, retención, restore y rollback están cubiertos en pytest.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { test, expect } = require('@playwright/test');
const { loginViaUI, registrarListenersDeErrores } = require('./helpers');

function toast(page) {
  return page.locator('#toast-container');
}

test('OWNER ve el estado, crea un backup manual y configura el segundo destino', async ({ page }) => {
  const errores = registrarListenersDeErrores(page);
  await loginViaUI(page, { irA: '/backup' });
  await expect(page.locator('#backup-estado')).toBeVisible();
  await expect(page.locator('#backup-sin-redundancia')).toBeVisible();
  await expect(page.locator('#backup-retencion')).toContainText('14 diarios');

  await page.getByRole('button', { name: 'Crear backup ahora' }).click();
  await expect(toast(page)).toContainText('Backup creado');
  await expect(page.locator('#backup-ultimo')).not.toContainText('Todavía no hay');

  await page.locator('#destino').fill('Z:\\carpeta\\que\\no\\existe');
  await page.getByRole('button', { name: 'Guardar' }).click();
  await expect(toast(page)).toContainText('no existe');
  await expect(page.locator('#backup-sin-redundancia')).toBeVisible();

  const externo = fs.mkdtempSync(path.join(os.tmpdir(), 'twinx-e2e-externo-'));
  await page.locator('#destino').fill(externo);
  await page.getByRole('button', { name: 'Guardar' }).click();
  await expect(toast(page)).toContainText('Segundo destino configurado');
  await expect(page.locator('#backup-secundario')).toContainText(externo);

  await page.getByRole('button', { name: 'Crear backup ahora' }).click();
  await expect(toast(page)).toContainText('Backup creado');
  await expect.poll(() => fs.readdirSync(externo).filter((n) => n.endsWith('.zip')).length).toBe(1);
  await page.reload();
  await expect(page.locator('#backup-secundario-ultima')).not.toContainText('ninguna');

  await page.locator('#destino').fill('');
  await page.getByRole('button', { name: 'Guardar' }).click();
  await expect(toast(page)).toContainText('desactivado');
  fs.rmSync(externo, { recursive: true, force: true });
  errores.afirmarSinErrores();
});

test('cerrar la caja genera el backup automático y la caja sigue operable', async ({ page }) => {
  await loginViaUI(page, { irA: '/caja' });
  const cerrar = page.locator('form[action="/caja/cerrar"]');
  await cerrar.locator('input[name="monto_final"]').fill('1000');
  await cerrar.locator('button[type="submit"]').click();
  await expect(toast(page)).toContainText('Backup guardado');
  await expect(page.locator('#alerta-backup')).toHaveCount(0);

  const abrir = page.locator('form[action="/caja/abrir"]');
  await abrir.locator('input[name="monto_inicial"]').fill('1000');
  await abrir.locator('button[type="submit"]').click();
  await expect(toast(page)).toContainText('Caja abierta');
});

test('@responsive la página de Backup no desborda en ningún ancho', async ({ page }) => {
  await loginViaUI(page, { irA: '/backup' });
  await expect(page.locator('#backup-estado')).toBeVisible();
  const desborda = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
  );
  expect(desborda).toBe(false);
  await expect(page.getByRole('button', { name: 'Crear backup ahora' })).toBeInViewport();
});
