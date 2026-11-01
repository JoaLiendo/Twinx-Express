// V1.16: estado fiscal en el POS después de registrar una venta.
//
// La venta ya quedó registrada (POST /api/ventas); la fiscalización es un pedido aparte
// (POST /api/fiscal/ventas/{id}/fiscalizar). Si ARCA no responde, la venta no desaparece: queda
// "Fiscalización pendiente". Solo con estado AUTORIZADO se ofrece "Imprimir factura"; un ticket
// interno nunca se presenta como factura.
(function (raiz) {
  'use strict';

  const VARIANTES = {
    AUTORIZADO: 'text-success',
    CAEA_PENDIENTE_INFORMAR: 'text-success',
    PENDIENTE: 'text-warning',
    ENVIANDO: 'text-info',
    INCIERTO: 'text-error',
    RECHAZADO: 'text-error',
  };

  // Pura (testeable en Node): qué mostrar a partir de la respuesta del servidor.
  function describirResultado(ok, datos) {
    if (ok && datos && datos.estado) {
      const numero = datos.numero ? ` ${datos.numero}` : '';
      if (datos.autorizado) {
        return { texto: `${datos.tipo}${numero} AUTORIZADA por ARCA.`, clase: 'text-success', imprimir: datos.comprobante_id, motivos: [] };
      }
      const textos = {
        PENDIENTE: 'Venta registrada · Fiscalización pendiente.',
        ENVIANDO: 'Venta registrada · Enviando a ARCA.',
        INCIERTO: 'Venta registrada · Resultado incierto: el dueño debe conciliarlo desde el detalle de la venta.',
        RECHAZADO: 'Venta registrada · Fiscalización rechazada por ARCA.',
      };
      return {
        texto: textos[datos.estado] || `Venta registrada · ${datos.etiqueta}.`,
        clase: VARIANTES[datos.estado] || 'text-slate-600',
        imprimir: null,
        motivos: datos.errores || [],
      };
    }
    const codigo = datos && datos.codigo;
    const motivos = (datos && Array.isArray(datos.motivos)) ? datos.motivos : [];
    if (codigo === 'FISCALIZACION_BLOQUEADA') {
      return { texto: 'Venta registrada · No se fiscalizó: faltan datos fiscales.', clase: 'text-warning', imprimir: null, motivos };
    }
    if (codigo === 'FISCALIZACION_NO_DISPONIBLE') {
      return { texto: 'Venta registrada · Fiscalización no disponible (ARCA no responde): quedó pendiente.', clase: 'text-warning', imprimir: null, motivos: [] };
    }
    const mensaje = (datos && datos.error) || 'No se pudo fiscalizar.';
    return { texto: `Venta registrada · ${mensaje}`, clase: 'text-warning', imprimir: null, motivos };
  }

  function crear(documento) {
    const zona = documento.getElementById('post-venta-fiscal');
    if (!zona) return null;
    const estadoEl = zona.querySelector('[data-fiscal-estado]');
    const motivosEl = zona.querySelector('[data-fiscal-motivos]');
    const imprimirEl = zona.querySelector('[data-fiscal-imprimir]');
    const activo = zona.dataset.fiscalActivo === 'true';

    function mostrar(resultado) {
      estadoEl.textContent = resultado.texto;
      estadoEl.className = `font-medium ${resultado.clase}`;
      motivosEl.replaceChildren(...resultado.motivos.map((motivo) => {
        const item = documento.createElement('li');
        item.textContent = motivo;
        return item;
      }));
      if (resultado.imprimir) {
        imprimirEl.href = `/fiscal/comprobantes/${resultado.imprimir}/ticket`;
        imprimirEl.classList.remove('hidden');
      } else {
        imprimirEl.classList.add('hidden');
      }
    }

    async function fiscalizar(ventaId) {
      if (!activo) {
        mostrar({ texto: 'Comprobante interno · la facturación fiscal no está configurada.', clase: 'text-slate-500', imprimir: null, motivos: [] });
        return;
      }
      mostrar({ texto: 'Venta registrada · Fiscalizando con ARCA…', clase: 'text-info', imprimir: null, motivos: [] });
      try {
        const respuesta = await fetch(`/api/fiscal/ventas/${ventaId}/fiscalizar`, { method: 'POST' });
        let datos = null;
        try { datos = await respuesta.json(); } catch (_) { datos = null; }
        mostrar(describirResultado(respuesta.ok, datos));
      } catch (_) {
        mostrar({ texto: 'Venta registrada · No se pudo consultar el estado fiscal: revisá el detalle de la venta.', clase: 'text-warning', imprimir: null, motivos: [] });
      }
    }

    return { fiscalizar };
  }

  const api = { describirResultado, crear };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (raiz && raiz.document) {
    raiz.TwinxFiscalPOS = crear(raiz.document);
  }
})(typeof window !== 'undefined' ? window : undefined);
