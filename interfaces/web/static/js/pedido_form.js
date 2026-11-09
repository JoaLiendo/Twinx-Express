/**
 * Formulario de nuevo pedido: agregar/quitar líneas y recordar que sin cliente hace falta un contacto.
 * Solo UX: el servidor valida todo de nuevo (services.servicio_pedidos.registrar_pedido).
 */
(function () {
  const contenedor = document.getElementById('lineas-pedido');
  const plantilla = document.getElementById('plantilla-linea-pedido');
  const botonAgregar = document.getElementById('boton-agregar-linea');
  const selectCliente = document.getElementById('select-cliente');
  const inputContacto = document.getElementById('input-contacto');
  const avisoContacto = document.getElementById('contacto-requerido');
  if (!contenedor || !plantilla) return;

  function actualizarBotonesQuitar() {
    const lineas = contenedor.querySelectorAll('.linea-pedido');
    lineas.forEach((linea) => {
      const boton = linea.querySelector('.boton-quitar-linea');
      boton.disabled = lineas.length <= 1;
      boton.classList.toggle('opacity-30', lineas.length <= 1);
    });
  }

  function sincronizarContacto() {
    if (!selectCliente || !inputContacto) return;
    const sinCliente = !selectCliente.value;
    inputContacto.required = sinCliente;
    if (avisoContacto) avisoContacto.classList.toggle('hidden', !sinCliente);
  }

  if (botonAgregar) {
    botonAgregar.addEventListener('click', () => {
      contenedor.appendChild(plantilla.content.cloneNode(true));
      actualizarBotonesQuitar();
    });
  }
  contenedor.addEventListener('click', (evento) => {
    const boton = evento.target.closest('.boton-quitar-linea');
    if (!boton || boton.disabled) return;
    boton.closest('.linea-pedido').remove();
    actualizarBotonesQuitar();
  });
  if (selectCliente) selectCliente.addEventListener('change', sincronizarContacto);
  actualizarBotonesQuitar();
  sincronizarContacto();
})();
