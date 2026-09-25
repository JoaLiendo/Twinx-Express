/**
 * Revisión de la lista de reposición (V1.2): el dueño ajusta cantidades y
 * destilda lo que no quiere comprar. Solo recalcula los subtotales en pantalla:
 * lo que se envía (productos tildados y cantidades) lo arma el formulario de cada grupo
 * y lo valida el servidor.
 */
(function () {
  const filas = Array.from(document.querySelectorAll('[data-fila-reposicion]'));
  const totalEl = document.getElementById('total-reposicion');
  if (!filas.length || !totalEl) return;

  function formatear(centavos) {
    const unidades = Math.floor(centavos / 100);
    const resto = String(centavos % 100).padStart(2, '0');
    return `$${String(unidades).replace(/\B(?=(\d{3})+(?!\d))/g, '.')},${resto}`;
  }

  function cantidadDe(fila) {
    const valor = parseInt(fila.querySelector('[data-cantidad]').value, 10);
    return Number.isFinite(valor) && valor > 0 ? valor : 0;
  }

  function recalcular() {
    let total = 0;
    for (const fila of filas) {
      const incluida = fila.querySelector('[data-incluir]').checked;
      const subtotal = cantidadDe(fila) * Number(fila.dataset.costo);
      fila.querySelector('[data-subtotal]').textContent = formatear(subtotal);
      fila.classList.toggle('excluida', !incluida);
      fila.classList.toggle('opacity-50', !incluida);
      if (incluida) total += subtotal;
    }
    totalEl.textContent = formatear(total);
  }

  for (const fila of filas) {
    fila.querySelector('[data-cantidad]').addEventListener('input', recalcular);
    fila.querySelector('[data-incluir]').addEventListener('change', recalcular);
  }
  recalcular();
})();
