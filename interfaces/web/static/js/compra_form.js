/**
 * Formulario de nueva compra: agregar/quitar líneas de producto y
 * mostrar subtotales/total en vivo. Es solo UX -- el servidor siempre
 * recalcula subtotales y total al recibir el POST
 * (services.servicio_compras.registrar_compra), nunca confía en estos
 * valores (ver interfaces/web/rutas/compras.py).
 */
(function () {
  const contenedorLineas = document.getElementById('lineas-compra');
  const plantillaLinea = document.getElementById('plantilla-linea-compra');
  const botonAgregar = document.getElementById('boton-agregar-linea');
  const totalEl = document.getElementById('total-compra');

  if (!contenedorLineas || !plantillaLinea) return;

  function formatearCentavos(centavos) {
    const absoluto = Math.max(0, Math.round(centavos));
    const unidades = Math.floor(absoluto / 100);
    const resto = String(absoluto % 100).padStart(2, '0');
    return `$${unidades}.${resto}`;
  }

  function centavosDesdeTexto(texto) {
    const normalizado = (texto || '').trim().replace(',', '.');
    const valor = parseFloat(normalizado);
    if (Number.isNaN(valor) || valor < 0) return null;
    return Math.round(valor * 100);
  }

  function actualizarLinea(linea) {
    const cantidad = parseInt(linea.querySelector('.linea-cantidad').value, 10);
    const costoCentavos = centavosDesdeTexto(linea.querySelector('.linea-costo').value);
    const subtotalEl = linea.querySelector('.linea-subtotal');

    if (!Number.isFinite(cantidad) || cantidad <= 0 || costoCentavos === null) {
      subtotalEl.textContent = '$0.00';
      return 0;
    }
    const subtotalCentavos = cantidad * costoCentavos;
    subtotalEl.textContent = formatearCentavos(subtotalCentavos);
    return subtotalCentavos;
  }

  function actualizarTotal() {
    let total = 0;
    contenedorLineas.querySelectorAll('.linea-compra').forEach((linea) => {
      total += actualizarLinea(linea);
    });
    if (totalEl) totalEl.textContent = formatearCentavos(total);
    actualizarBotonesQuitar();
  }

  function actualizarBotonesQuitar() {
    const lineas = contenedorLineas.querySelectorAll('.linea-compra');
    lineas.forEach((linea) => {
      const boton = linea.querySelector('.boton-quitar-linea');
      boton.disabled = lineas.length <= 1;
      boton.classList.toggle('opacity-30', lineas.length <= 1);
      boton.classList.toggle('cursor-not-allowed', lineas.length <= 1);
    });
  }

  function agregarLinea() {
    const fragmento = plantillaLinea.content.cloneNode(true);
    contenedorLineas.appendChild(fragmento);
    actualizarTotal();
  }

  contenedorLineas.addEventListener('input', (evento) => {
    if (evento.target.matches('.linea-cantidad, .linea-costo')) {
      actualizarTotal();
    }
  });

  contenedorLineas.addEventListener('click', (evento) => {
    const boton = evento.target.closest('.boton-quitar-linea');
    if (!boton || boton.disabled) return;
    boton.closest('.linea-compra').remove();
    actualizarTotal();
  });

  if (botonAgregar) {
    botonAgregar.addEventListener('click', agregarLinea);
  }

  agregarLinea();
})();
