/**
 * Inventario físico (V1.4). Solo experiencia de uso: filtra listas, alterna la selección manual y
 * envía cada conteo sin recargar la página. Ninguna regla de negocio vive acá: el servidor valida el
 * conteo, decide si el inventario está abierto y es quien nunca devuelve el stock esperado (conteo a
 * ciegas). Sin JS, cada fila sigue funcionando como un formulario normal.
 */
(function () {
  const filtro = document.querySelector('[data-filtro-inventario]');
  if (filtro) {
    filtro.addEventListener('input', () => {
      const texto = filtro.value.trim().toLowerCase();
      for (const fila of document.querySelectorAll('[data-fila-filtrable]')) {
        fila.classList.toggle('hidden', texto !== '' && !fila.dataset.texto.includes(texto));
      }
    });
  }

  const alcances = document.querySelectorAll('[data-alcance]');
  const seleccionManual = document.getElementById('seleccion-manual');
  if (alcances.length && seleccionManual) {
    const actualizar = () => {
      const manual = document.querySelector('[data-alcance]:checked').value === 'manual';
      seleccionManual.classList.toggle('hidden', !manual);
      for (const casilla of seleccionManual.querySelectorAll('[data-producto]')) casilla.disabled = !manual;
    };
    for (const radio of alcances) radio.addEventListener('change', actualizar);
    actualizar();
  }

  function actualizarProgreso() {
    const progreso = document.getElementById('progreso-conteo');
    if (!progreso) return;
    const filas = document.querySelectorAll('#lista-conteo [data-producto-id]');
    const contadas = document.querySelectorAll('#lista-conteo [data-contada="true"]').length;
    progreso.textContent = `${contadas} de ${filas.length} contados`;
  }

  for (const fila of document.querySelectorAll('#lista-conteo [data-producto-id]')) {
    if (fila.querySelector('[data-estado]').textContent.includes('Contado')) fila.dataset.contada = 'true';
  }

  for (const formulario of document.querySelectorAll('[data-form-conteo]')) {
    formulario.addEventListener('submit', async (evento) => {
      if (!window.fetch) return; // sin fetch: envío normal del formulario
      evento.preventDefault();
      const fila = formulario.closest('[data-producto-id]');
      const entrada = formulario.querySelector('input[name="cantidad"]');
      const boton = formulario.querySelector('button[type="submit"]');
      const texto = entrada.value.trim();
      if (!/^\d+$/.test(texto)) {
        mostrarToast('La cantidad contada debe ser un número entero de 0 en adelante.', 'error');
        return;
      }
      boton.disabled = true;
      try {
        const respuesta = await fetch(`/api/inventario/conteo/${fila.dataset.productoId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cantidad: parseInt(texto, 10) }),
        });
        const datos = await respuesta.json().catch(() => ({}));
        if (!respuesta.ok) {
          mostrarToast(datos.error || 'No se pudo registrar el conteo.', 'error');
          return;
        }
        const estado = fila.querySelector('[data-estado]');
        estado.textContent = `Contado: ${datos.cantidad_contada}`;
        estado.className = 'text-xs text-success font-semibold';
        fila.dataset.contada = 'true';
        boton.textContent = 'Recontar';
        actualizarProgreso();
      } catch (error) {
        mostrarToast('No se pudo conectar con el servidor. Reintentá.', 'error');
      } finally {
        boton.disabled = false;
      }
    });
  }
})();
