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

  // V1.12-B: varias fechas de vencimiento en una misma línea. Solo arma el texto "6@AAAA-MM-DD;4@AAAA-MM-DD"
  // que el servidor valida (lo que no se asigne queda sin vencimiento conocido); siempre hay un único campo
  // `vencimiento_producto` habilitado por línea.
  const CLASE_CAMPO = 'rounded-lg border border-slate-300 px-3 py-2 text-sm bg-white text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100';

  function agregarFilaDeFecha(linea, cantidad = '', fecha = '') {
    const fila = document.createElement('div');
    fila.className = 'linea-varias-fila flex flex-wrap items-center gap-2';
    const inputCantidad = document.createElement('input');
    Object.assign(inputCantidad, { type: 'number', min: '1', step: '1', value: cantidad, placeholder: 'Cant.' });
    inputCantidad.className = `varias-cantidad w-24 tabular-nums ${CLASE_CAMPO}`;
    inputCantidad.setAttribute('aria-label', 'Unidades con este vencimiento');
    const inputFecha = document.createElement('input');
    Object.assign(inputFecha, { type: 'date', value: fecha });
    inputFecha.className = `varias-fecha ${CLASE_CAMPO}`;
    inputFecha.setAttribute('aria-label', 'Vencimiento');
    const quitar = document.createElement('button');
    quitar.type = 'button';
    quitar.className = 'boton-quitar-fecha text-xs text-slate-400 hover:text-error';
    quitar.textContent = 'Quitar';
    fila.append(inputCantidad, inputFecha, quitar);
    linea.querySelector('.linea-varias-filas').appendChild(fila);
  }

  function serializarFechas(linea) {
    const partes = [];
    let asignado = 0;
    for (const fila of linea.querySelectorAll('.linea-varias-fila')) {
      const cantidad = parseInt(fila.querySelector('.varias-cantidad').value, 10);
      const fecha = fila.querySelector('.varias-fecha').value;
      if (Number.isFinite(cantidad) && cantidad > 0 && fecha) {
        partes.push(`${cantidad}@${fecha}`);
        asignado += cantidad;
      }
    }
    linea.querySelector('.linea-vence-serializado').value = partes.join(';');
    const total = parseInt(linea.querySelector('.linea-cantidad').value, 10);
    const resto = linea.querySelector('.linea-varias-resto');
    if (!Number.isFinite(total)) resto.textContent = '';
    else if (asignado > total) resto.textContent = `Las fechas suman ${asignado}, más que la cantidad (${total}).`;
    else if (asignado < total) resto.textContent = `${total - asignado} sin vencimiento conocido.`;
    else resto.textContent = '';
  }

  function modoVariasFechas(linea, activar) {
    linea.querySelector('.linea-varias').classList.toggle('hidden', !activar);
    linea.querySelector('.boton-varias-fechas').classList.toggle('hidden', activar);
    const unico = linea.querySelector('.linea-vence-unico');
    unico.disabled = activar;
    unico.closest('div').classList.toggle('hidden', activar);
    linea.querySelector('.linea-vence-serializado').disabled = !activar;
    if (activar && !linea.querySelector('.linea-varias-fila')) {
      agregarFilaDeFecha(linea, '', unico.value);
      agregarFilaDeFecha(linea);
    }
    serializarFechas(linea);
  }

  contenedorLineas.addEventListener('click', (evento) => {
    const linea = evento.target.closest('.linea-compra');
    if (!linea) return;
    if (evento.target.closest('.boton-varias-fechas')) modoVariasFechas(linea, true);
    else if (evento.target.closest('.boton-una-fecha')) modoVariasFechas(linea, false);
    else if (evento.target.closest('.boton-agregar-fecha')) agregarFilaDeFecha(linea);
    else if (evento.target.closest('.boton-quitar-fecha')) {
      evento.target.closest('.linea-varias-fila').remove();
      serializarFechas(linea);
    }
  });

  contenedorLineas.addEventListener('input', (evento) => {
    const linea = evento.target.closest('.linea-compra');
    if (linea && evento.target.matches('.varias-cantidad, .varias-fecha, .linea-cantidad')) serializarFechas(linea);
  });

  // Con una precarga (reposición) las líneas ya vienen renderizadas; solo se recalculan los subtotales.
  if (contenedorLineas.querySelector('.linea-compra')) actualizarTotal();
  else agregarLinea();
})();
