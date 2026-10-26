/**
 * Crédito del cliente en el POS (V1.14-F): al elegir un cliente para vender a cuenta muestra su saldo,
 * disponible, estado y bloqueo (GET /api/clientes/{id}/credito), permite cobrar una parte en efectivo
 * (venta mixta) y, si es una empresa, indicar quién compra (autorizado vigente o autorización excepcional).
 *
 * Solo UX: avisa antes de confirmar para que el cajero no choque contra un rechazo. Toda regla (límite,
 * estado, bloqueo, autorizaciones, comprador) la decide el servidor en POST /api/ventas.
 *
 * SEGURIDAD: los nombres vienen del servidor (los escribió un usuario): se insertan SIEMPRE con
 * `textContent` / `new Option(texto, valor)`, nunca interpretados como HTML.
 */
(function (raiz) {
  /**
   * ¿Se puede pedir esta venta a cuenta? Devuelve `{ ok, mensaje, creditoCentavos }`.
   * `contadoCentavos` es lo que el cliente paga ahora en efectivo (0 = todo a cuenta).
   */
  function evaluarVentaACredito(situacion, totalCentavos, contadoCentavos) {
    const credito = totalCentavos - contadoCentavos;
    if (!situacion) return { ok: false, mensaje: 'Cargando el crédito del cliente…', creditoCentavos: credito };
    if (situacion.bloqueo) return { ok: false, mensaje: situacion.bloqueo, creditoCentavos: credito };
    if (!situacion.credito_habilitado) {
      return { ok: false, mensaje: 'Este cliente no tiene crédito habilitado.', creditoCentavos: credito };
    }
    if (contadoCentavos < 0) return { ok: false, mensaje: 'El pago en efectivo no puede ser negativo.', creditoCentavos: credito };
    if (totalCentavos > 0 && contadoCentavos >= totalCentavos) {
      return { ok: false, mensaje: 'Si paga todo, elegí Efectivo como medio de pago.', creditoCentavos: credito };
    }
    if (credito > situacion.disponible_centavos) {
      return { ok: false, mensaje: 'Lo que queda a cuenta supera el crédito disponible.', creditoCentavos: credito };
    }
    return { ok: true, mensaje: '', creditoCentavos: credito };
  }

  /** Campos del comprador para POST /api/ventas, o `null` si falta (o no corresponde: no es empresa). */
  function datosComprador(esEmpresa, eleccion) {
    if (!esEmpresa) return {};
    if (!eleccion) return null;
    if (eleccion.autorizadoId) return { comprador_autorizado_id: eleccion.autorizadoId };
    const nombre = (eleccion.nombre || '').trim();
    const detalle = (eleccion.detalle || '').trim();
    if (!nombre || !eleccion.metodo || !detalle) return null;
    return {
      comprador_excepcional_nombre: nombre,
      comprador_excepcional_metodo: eleccion.metodo,
      comprador_excepcional_detalle: detalle,
    };
  }

  function iniciar(opciones) {
    const contenedor = opciones.contenedor;
    const formatearCentavos = opciones.formatearCentavos;
    const textoACentavos = opciones.textoACentavos;
    const alCambiar = opciones.alCambiar || function () {};

    const estadoEl = contenedor.querySelector('[data-credito-estado]');
    const disponibleEl = contenedor.querySelector('[data-credito-disponible]');
    const mensajeEl = contenedor.querySelector('[data-credito-mensaje]');
    const aCuentaEl = contenedor.querySelector('[data-credito-a-cuenta]');
    const contadoInput = contenedor.querySelector('[data-credito-contado]');
    const zonaComprador = contenedor.querySelector('[data-credito-comprador]');
    const selectAutorizado = contenedor.querySelector('[data-credito-autorizado]');
    const zonaExcepcional = contenedor.querySelector('[data-credito-excepcional]');
    const nombreExcepcional = contenedor.querySelector('[data-credito-excepcional-nombre]');
    const metodoExcepcional = contenedor.querySelector('[data-credito-excepcional-metodo]');
    const detalleExcepcional = contenedor.querySelector('[data-credito-excepcional-detalle]');

    let situacion = null;
    let totalCentavos = 0;
    let numeroDeCarga = 0;

    function contadoCentavos() {
      const texto = contadoInput.value;
      if (!texto.trim()) return 0;
      const resultado = textoACentavos(texto);
      return resultado.ok ? resultado.centavos : NaN;
    }

    function eleccionComprador() {
      if (!situacion || !situacion.es_empresa) return null;
      if (selectAutorizado.value && selectAutorizado.value !== 'OTRO') {
        return { autorizadoId: Number(selectAutorizado.value) };
      }
      if (selectAutorizado.value !== 'OTRO') return null;
      return { nombre: nombreExcepcional.value, metodo: metodoExcepcional.value, detalle: detalleExcepcional.value };
    }

    function motivoBloqueo() {
      const contado = contadoCentavos();
      if (Number.isNaN(contado)) return 'Ingresá un monto en efectivo válido.';
      const evaluacion = evaluarVentaACredito(situacion, totalCentavos, contado);
      if (!evaluacion.ok) return evaluacion.mensaje;
      if (situacion.es_empresa && datosComprador(true, eleccionComprador()) === null) {
        return 'Indicá quién compra a nombre de la empresa.';
      }
      return null;
    }

    function pintar() {
      if (!situacion) {
        contenedor.classList.add('hidden');
        return;
      }
      contenedor.classList.remove('hidden');
      estadoEl.textContent = `Estado: ${situacion.estado_etiqueta}` +
        (situacion.dias_antiguedad !== null ? ` (deuda más antigua: ${situacion.dias_antiguedad} días)` : '') +
        (situacion.strikes ? ` · ${situacion.strikes} de 3 strikes` : '');
      estadoEl.dataset.estado = situacion.estado;
      disponibleEl.textContent = 'Disponible: ' + formatearCentavos(situacion.bloqueo ? 0 : situacion.disponible_centavos) +
        (situacion.autorizacion_hasta ? ` · autorizado hasta el ${situacion.autorizacion_hasta}` : '');
      const contado = contadoCentavos();
      aCuentaEl.textContent = Number.isNaN(contado)
        ? ''
        : 'Queda a cuenta: ' + formatearCentavos(Math.max(0, totalCentavos - contado));
      const motivo = motivoBloqueo();
      mensajeEl.textContent = motivo || '';
      zonaComprador.classList.toggle('hidden', !situacion.es_empresa);
      zonaExcepcional.classList.toggle('hidden', selectAutorizado.value !== 'OTRO');
    }

    function cargarAutorizados(autorizados) {
      selectAutorizado.textContent = '';
      selectAutorizado.appendChild(new Option('Elegí quién compra…', ''));
      autorizados.forEach(function (autorizado) {
        selectAutorizado.appendChild(new Option(autorizado.nombre, String(autorizado.id)));
      });
      selectAutorizado.appendChild(new Option('Otra persona (autorización excepcional)', 'OTRO'));
    }

    async function cargar(clienteId) {
      const numero = ++numeroDeCarga;
      situacion = null;
      contadoInput.value = '';
      if (clienteId === null) {
        pintar();
        alCambiar();
        return;
      }
      contenedor.classList.remove('hidden');
      mensajeEl.textContent = 'Cargando el crédito del cliente…';
      try {
        const respuesta = await fetch(`/api/clientes/${encodeURIComponent(clienteId)}/credito`);
        if (!respuesta.ok) throw new Error('respuesta ' + respuesta.status);
        const datos = await respuesta.json();
        if (numero !== numeroDeCarga) return;
        situacion = datos;
        cargarAutorizados(datos.autorizados || []);
      } catch (error) {
        if (numero !== numeroDeCarga) return;
        situacion = null;
        mensajeEl.textContent = 'No se pudo consultar el crédito del cliente. Reintentá.';
        alCambiar();
        return;
      }
      pintar();
      alCambiar();
    }

    [contadoInput, selectAutorizado, nombreExcepcional, metodoExcepcional, detalleExcepcional].forEach(function (el) {
      el.addEventListener('input', function () {
        pintar();
        alCambiar();
      });
    });

    return {
      cargar: cargar,
      /** Motivo por el que no conviene confirmar, o `null`. Sin cliente cargado devuelve un aviso. */
      motivoBloqueo: function () {
        return situacion ? motivoBloqueo() : 'Cargando el crédito del cliente…';
      },
      establecerTotal: function (centavos) {
        totalCentavos = centavos;
        if (situacion) pintar();
      },
      /** Campos extra de POST /api/ventas para esta venta a cuenta. */
      datosVenta: function () {
        const contado = contadoCentavos();
        return {
          ...(contado > 0 ? { pago_contado_centavos: contado } : {}),
          ...(situacion ? datosComprador(situacion.es_empresa, eleccionComprador()) || {} : {}),
        };
      },
      reiniciar: function () {
        numeroDeCarga++;
        situacion = null;
        contadoInput.value = '';
        pintar();
      },
    };
  }

  const api = { iniciar: iniciar, evaluarVentaACredito: evaluarVentaACredito, datosComprador: datosComprador };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (raiz) {
    raiz.CreditoPos = api;
  }
})(typeof window !== 'undefined' ? window : undefined);
