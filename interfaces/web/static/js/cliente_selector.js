/**
 * Selector de cliente del POS para la venta a cuenta (021).
 *
 * Solo lógica visual: busca clientes ACTIVOS en GET /api/clientes/buscar (el servidor devuelve como
 * máximo 20) y recuerda cuál eligió el cajero. Toda regla de negocio (cliente activo, caja abierta,
 * stock, permisos, el CARGO) la valida el servidor al confirmar la venta en POST /api/ventas.
 *
 * SEGURIDAD: los nombres y teléfonos vienen del servidor (los escribió un usuario). Se insertan
 * SIEMPRE con `textContent`, nunca interpretados como HTML (un test verifica que este archivo no
 * usa ninguna API que parsee marcado).
 *
 * El saldo que se muestra es el que tenía el cliente al buscarlo; el "saldo estimado" es solo una
 * ayuda para evitar errores (saldo + total del carrito), nunca un dato contable.
 */
(function (raiz) {
  const DEMORA_BUSQUEDA_MS = 250;

  /** Texto de una fila de resultado: "Nombre · teléfono" (el teléfono es opcional). */
  function textoDeCliente(cliente) {
    return cliente.telefono ? `${cliente.nombre} · ${cliente.telefono}` : cliente.nombre;
  }

  /** Saldo estimado tras la venta: saldo actual + total del carrito, en centavos enteros. */
  function saldoEstimadoCentavos(saldoCentavos, totalCentavos) {
    return saldoCentavos + Math.max(0, totalCentavos);
  }

  function iniciar(opciones) {
    const contenedor = opciones.contenedor;
    const endpoint = opciones.endpoint;
    const formatearCentavos = opciones.formatearCentavos;
    const alCambiar = opciones.alCambiar || function () {};

    const entrada = contenedor.querySelector('[data-cliente-busqueda]');
    const lista = contenedor.querySelector('[data-cliente-resultados]');
    const mensaje = contenedor.querySelector('[data-cliente-mensaje]');
    const panel = contenedor.querySelector('[data-cliente-seleccionado]');
    const nombreEl = contenedor.querySelector('[data-cliente-nombre]');
    const telefonoEl = contenedor.querySelector('[data-cliente-telefono]');
    const saldoEl = contenedor.querySelector('[data-cliente-saldo]');
    const estimadoEl = contenedor.querySelector('[data-cliente-saldo-estimado]');
    const botonQuitar = contenedor.querySelector('[data-cliente-quitar]');
    const zonaBusqueda = contenedor.querySelector('[data-cliente-zona-busqueda]');

    let seleccionado = null; // { id, nombre, telefono, saldo_centavos }
    let totalCentavos = 0;
    let temporizador = null;
    let numeroDeBusqueda = 0; // descarta respuestas viejas que llegan después de una más nueva
    let busquedaInicialHecha = false;

    function decirMensaje(texto) {
      mensaje.textContent = texto;
    }

    function vaciarResultados() {
      lista.textContent = '';
      lista.classList.add('hidden');
    }

    function pintarResultados(clientes) {
      vaciarResultados();
      if (clientes.length === 0) {
        decirMensaje('No se encontraron clientes activos.');
        return;
      }
      decirMensaje('');
      clientes.forEach(function (cliente) {
        const item = document.createElement('li');
        const boton = document.createElement('button');
        boton.type = 'button';
        boton.className =
          'w-full text-left px-3 py-2 text-sm rounded-md hover:bg-slate-100 dark:hover:bg-slate-800 ' +
          'flex items-center justify-between gap-2';
        const nombre = document.createElement('span');
        nombre.className = 'truncate text-slate-800 dark:text-slate-100';
        nombre.textContent = textoDeCliente(cliente);
        const saldo = document.createElement('span');
        saldo.className = 'shrink-0 text-xs tabular-nums text-slate-500 dark:text-slate-400';
        saldo.textContent = formatearCentavos(cliente.saldo_centavos);
        boton.appendChild(nombre);
        boton.appendChild(saldo);
        boton.addEventListener('click', function () {
          seleccionar(cliente);
        });
        item.appendChild(boton);
        lista.appendChild(item);
      });
      lista.classList.remove('hidden');
    }

    async function buscar(texto) {
      const numero = ++numeroDeBusqueda;
      try {
        const respuesta = await fetch(endpoint + '?q=' + encodeURIComponent(texto));
        if (!respuesta.ok) throw new Error('respuesta ' + respuesta.status);
        const clientes = await respuesta.json();
        if (numero !== numeroDeBusqueda) return;
        pintarResultados(clientes);
      } catch (error) {
        if (numero !== numeroDeBusqueda) return;
        vaciarResultados();
        decirMensaje('No se pudo buscar clientes. Reintentá.');
      }
    }

    function actualizarEstimado() {
      if (!seleccionado) return;
      estimadoEl.textContent =
        'Saldo estimado tras esta venta: ' + formatearCentavos(saldoEstimadoCentavos(seleccionado.saldo_centavos, totalCentavos));
    }

    function seleccionar(cliente) {
      seleccionado = cliente;
      vaciarResultados();
      decirMensaje('');
      entrada.value = '';
      nombreEl.textContent = cliente.nombre;
      telefonoEl.textContent = cliente.telefono || '';
      saldoEl.textContent = 'Saldo actual: ' + formatearCentavos(cliente.saldo_centavos);
      actualizarEstimado();
      panel.classList.remove('hidden');
      zonaBusqueda.classList.add('hidden');
      alCambiar(seleccionado);
    }

    function quitar() {
      seleccionado = null;
      panel.classList.add('hidden');
      zonaBusqueda.classList.remove('hidden');
      entrada.focus();
      buscar(entrada.value);
      alCambiar(null);
    }

    entrada.addEventListener('input', function () {
      clearTimeout(temporizador);
      temporizador = setTimeout(function () {
        buscar(entrada.value);
      }, DEMORA_BUSQUEDA_MS);
    });
    botonQuitar.addEventListener('click', quitar);

    return {
      /** Cliente elegido (`{id, nombre, telefono, saldo_centavos}`) o `null`. */
      obtener: function () {
        return seleccionado;
      },
      /** Total del carrito, para el saldo estimado. */
      establecerTotal: function (centavos) {
        totalCentavos = centavos;
        actualizarEstimado();
      },
      /** Se llama al mostrar el bloque: la primera vez carga los primeros clientes. */
      alMostrar: function () {
        if (busquedaInicialHecha || seleccionado) return;
        busquedaInicialHecha = true;
        buscar('');
      },
      /** Descarta la selección (tras una venta confirmada o al dejar CUENTA_CORRIENTE). */
      reiniciar: function () {
        seleccionado = null;
        nombreEl.textContent = '';
        telefonoEl.textContent = '';
        saldoEl.textContent = '';
        estimadoEl.textContent = '';
        panel.classList.add('hidden');
        zonaBusqueda.classList.remove('hidden');
        entrada.value = '';
        vaciarResultados();
        decirMensaje('');
        busquedaInicialHecha = false;
      },
    };
  }

  const api = { iniciar: iniciar, textoDeCliente: textoDeCliente, saldoEstimadoCentavos: saldoEstimadoCentavos };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (raiz) {
    raiz.ClienteSelector = api;
  }
})(typeof window !== 'undefined' ? window : undefined);
