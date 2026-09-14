/**
 * Fase 5C: lógica pura de "efectivo recibido + vuelto" del POS.
 *
 * Módulo chico y puro a propósito: sin DOM, sin `fetch`, sin estado de
 * la aplicación (el carrito, `document`, etc. viven en `pos.js`). Esto
 * permite probarlo con el runner de pruebas nativo de Node
 * (`node:test` + `node:assert`), sin agregar Jest/Vitest ni ningún
 * bundler -- ver `tests_js/dinero_cobro.test.js`.
 *
 * Todo en centavos (`int`), nunca `float`: `textoACentavos` parsea por
 * string (entero y decimal por separado) en vez de `parseFloat(x) *
 * 100`, que sí podría perder precisión binaria. El resultado final se
 * valida con `Number.isSafeInteger` antes de aceptarlo, para no
 * aceptar en silencio un monto que ya perdió precisión por ser
 * absurdamente grande.
 *
 * Formato aceptado deliberadamente simple (sin separador de miles):
 * "1000", "1000.50", "1000,50". Cualquier otra cosa -- incluido
 * "1.000,00" -- se rechaza como formato inválido en vez de intentar
 * adivinar la intención del usuario.
 */
(function (raiz) {
  const PATRON_MONTO = /^\d+([.,]\d{1,2})?$/;

  /**
   * Convierte el texto de un input de monto a centavos.
   *
   * Devuelve `{ ok: true, centavos }` o `{ ok: false, motivo }`, con
   * `motivo` uno de:
   *   - 'vacio': el texto (recortado) está vacío.
   *   - 'formato': no matchea el formato simple aceptado (letras,
   *     signo negativo, más de 2 decimales, separador de miles, más
   *     de un separador decimal, etc.).
   *   - 'demasiado_grande': el formato es válido pero el resultado no
   *     es un entero seguro de JavaScript (`Number.isSafeInteger`).
   */
  function textoACentavos(texto) {
    const bruto = (texto == null ? '' : String(texto)).trim();
    if (bruto === '') {
      return { ok: false, motivo: 'vacio' };
    }
    if (!PATRON_MONTO.test(bruto)) {
      return { ok: false, motivo: 'formato' };
    }

    const separador = bruto.includes(',') ? ',' : bruto.includes('.') ? '.' : null;
    const [parteEntera, parteDecimalCruda] = separador ? bruto.split(separador) : [bruto, ''];
    const parteDecimal = parteDecimalCruda.padEnd(2, '0');

    const centavos = Number(parteEntera) * 100 + Number(parteDecimal);

    if (!Number.isSafeInteger(centavos)) {
      return { ok: false, motivo: 'demasiado_grande' };
    }
    return { ok: true, centavos };
  }

  /**
   * Compara el monto recibido contra el total de la venta.
   *
   * Devuelve `{ ok: false }` si alguno de los dos argumentos no es un
   * entero seguro (`Number.isSafeInteger`) -- ej. `NaN` o `Infinity`,
   * que de otro modo caerían en silencio en la rama `'con_vuelto'` con
   * una diferencia sin sentido. Hoy `pos.js` nunca llega a pasar un
   * valor así (`textoACentavos` ya garantiza un entero seguro antes de
   * llamar a esta función), pero el contrato de un módulo puro no debe
   * depender de que quien lo llame se porte bien.
   *
   * Si `ok` es `true`, `diferenciaCentavos` es
   * `montoRecibidoCentavos - totalCentavos` (puede ser negativo si
   * falta plata): quien llama decide cómo mostrarlo ("Vuelto: $X" o
   * "Falta $X") -- este módulo no conoce texto de UI ni formato de
   * moneda.
   */
  function calcularResultadoCobroEfectivo(totalCentavos, montoRecibidoCentavos) {
    if (!Number.isSafeInteger(totalCentavos) || !Number.isSafeInteger(montoRecibidoCentavos)) {
      return { ok: false };
    }
    const diferenciaCentavos = montoRecibidoCentavos - totalCentavos;
    const estado = diferenciaCentavos < 0 ? 'insuficiente' : diferenciaCentavos === 0 ? 'exacto' : 'con_vuelto';
    return { ok: true, estado, diferenciaCentavos };
  }

  const api = { textoACentavos, calcularResultadoCobroEfectivo };

  // Node (tests, ver tests_js/dinero_cobro.test.js): CommonJS clásico.
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  // Navegador (pos.html/pos.js): un único objeto de namespace, sin
  // ensuciar `window` con funciones sueltas.
  if (raiz) {
    raiz.DineroCobro = api;
  }
})(typeof window !== 'undefined' ? window : undefined);
