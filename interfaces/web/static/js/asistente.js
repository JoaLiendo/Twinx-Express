// Twinx Asistente (V1.18): panel lateral de chat. Vanilla JS, sin dependencias.
//
// Seguridad de la vista: todo lo que viene del servidor o del usuario se inserta con `textContent`
// (nunca `innerHTML`); los enlaces solo se aceptan si son rutas internas (`esHrefSeguro`). El historial
// vive en `sessionStorage` de esta pestaña, atado al usuario de la sesión, y se borra al cambiar de usuario
// o con el botón "Borrar": nunca se guarda en el servidor.
(function (global) {
  'use strict';

  const MAX_HISTORIAL_PANTALLA = 20;
  const MAX_HISTORIAL_ENVIADO = 10;
  const CLAVE_STORAGE = 'twinx-asistente';

  /** Solo rutas internas absolutas (`/reportes?...`), nunca `//host`, `http:` ni `javascript:`. */
  function esHrefSeguro(href) {
    return typeof href === 'string' && /^\/(?!\/)[^\s]*$/.test(href);
  }

  /** Los últimos mensajes con la forma que espera el servidor: solo rol y texto. */
  function historialParaEnviar(mensajes) {
    return mensajes
      .filter((m) => (m.rol === 'usuario' || m.rol === 'asistente') && typeof m.texto === 'string' && m.texto.trim())
      .slice(-MAX_HISTORIAL_ENVIADO)
      .map((m) => ({ rol: m.rol, texto: m.texto.slice(0, 500) }));
  }

  /** Clase de estado para el badge del encabezado a partir de `motor` de /api/asistente/estado. */
  function clasificarEstado(motor) {
    if (!motor) return 'desconocido';
    if (motor.modo === 'ollama' && motor.disponible) return 'ia';
    if (motor.modo === 'ollama') return 'sin-ia';
    return 'integrado';
  }

  /** Separa la respuesta en párrafos y listas (líneas que empiezan con "- " o "1. "). */
  function bloquesDeTexto(texto) {
    const bloques = [];
    let lista = null;
    String(texto || '')
      .split('\n')
      .forEach((linea) => {
        const item = linea.match(/^\s*(?:[-•]|\d+[.)])\s+(.*)$/);
        if (item) {
          if (!lista) {
            lista = { tipo: /^\s*\d/.test(linea) ? 'ol' : 'ul', items: [] };
            bloques.push(lista);
          }
          lista.items.push(item[1]);
          return;
        }
        lista = null;
        if (linea.trim()) bloques.push({ tipo: 'p', texto: linea.trim() });
      });
    return bloques;
  }

  function etiquetaOrigen(respuesta) {
    if (respuesta.origen === 'ia_local') return 'IA local' + (respuesta.modelo ? ' · ' + respuesta.modelo : '');
    return 'Ayuda integrada';
  }

  function iniciar(documento) {
    const panel = documento.getElementById('asistente-panel');
    if (!panel) return null;
    const boton = documento.getElementById('boton-asistente');
    const backdrop = documento.getElementById('asistente-backdrop');
    const cerrar = documento.getElementById('asistente-cerrar');
    const borrar = documento.getElementById('asistente-borrar');
    const estado = documento.getElementById('asistente-estado');
    const mensajesEl = documento.getElementById('asistente-mensajes');
    const sugeridasEl = documento.getElementById('asistente-sugeridas');
    const errorEl = documento.getElementById('asistente-error');
    const form = documento.getElementById('asistente-form');
    const input = documento.getElementById('asistente-input');
    const enviarBtn = documento.getElementById('asistente-enviar');
    const usuario = panel.dataset.usuario || '';
    const clave = CLAVE_STORAGE + ':' + usuario;

    let mensajes = cargarHistorial();
    let abierto = false;
    let estadoCargado = false;
    let enviando = false;

    function cargarHistorial() {
      try {
        const crudo = global.sessionStorage.getItem(clave);
        const datos = crudo ? JSON.parse(crudo) : null;
        if (!datos || datos.usuario !== usuario || !Array.isArray(datos.mensajes)) return [];
        return datos.mensajes.slice(-MAX_HISTORIAL_PANTALLA);
      } catch (error) {
        return [];
      }
    }

    function guardarHistorial() {
      try {
        global.sessionStorage.setItem(clave, JSON.stringify({ usuario, mensajes: mensajes.slice(-MAX_HISTORIAL_PANTALLA) }));
      } catch (error) {
        /* sin storage (modo privado): el historial dura lo que la página */
      }
    }

    function mostrarError(texto) {
      errorEl.textContent = texto || '';
      errorEl.classList.toggle('hidden', !texto);
    }

    function elemento(tag, clase, texto) {
      const el = documento.createElement(tag);
      if (clase) el.className = clase;
      if (texto !== undefined) el.textContent = texto;
      return el;
    }

    function renderizarMensaje(mensaje) {
      const burbuja = elemento('article', 'chat-msg ' + (mensaje.rol === 'usuario' ? 'chat-msg-usuario' : 'chat-msg-asistente'));
      burbuja.dataset.rol = mensaje.rol;
      if (mensaje.rol === 'usuario') {
        burbuja.appendChild(elemento('p', null, mensaje.texto));
        return burbuja;
      }
      bloquesDeTexto(mensaje.texto).forEach((bloque) => {
        if (bloque.tipo === 'p') {
          burbuja.appendChild(elemento('p', null, bloque.texto));
          return;
        }
        const lista = elemento(bloque.tipo, 'chat-lista');
        bloque.items.forEach((item) => lista.appendChild(elemento('li', null, item)));
        burbuja.appendChild(lista);
      });
      const acciones = (mensaje.acciones || []).filter((a) => a && esHrefSeguro(a.href) && a.etiqueta);
      if (acciones.length) {
        const fila = elemento('div', 'chat-acciones');
        acciones.forEach((accion) => {
          const enlace = elemento('a', 'btn btn-outline btn-sm', accion.etiqueta);
          enlace.href = accion.href;
          fila.appendChild(enlace);
        });
        burbuja.appendChild(fila);
      }
      (mensaje.advertencias || []).forEach((aviso) => burbuja.appendChild(elemento('p', 'chat-aviso', aviso)));
      const origen = elemento('p', 'chat-origen', etiquetaOrigen(mensaje));
      origen.dataset.origen = mensaje.origen || 'integrado';
      burbuja.appendChild(origen);
      return burbuja;
    }

    function renderizarTodo() {
      mensajesEl.textContent = '';
      if (!mensajes.length) {
        const vacio = elemento('div', 'chat-vacio');
        vacio.appendChild(elemento('p', 'font-medium', 'Hola. Preguntame por tus ventas, vencimientos, reposición o cómo hacer algo en Twinx.'));
        vacio.appendChild(elemento('p', 'muted text-xs mt-1', 'Solo consulto datos ya registrados: no invento cifras ni hago cambios.'));
        mensajesEl.appendChild(vacio);
      }
      mensajes.forEach((m) => mensajesEl.appendChild(renderizarMensaje(m)));
      mensajesEl.scrollTop = mensajesEl.scrollHeight;
    }

    function renderizarSugeridas(lista) {
      sugeridasEl.textContent = '';
      (lista || []).forEach((pregunta) => {
        const chip = elemento('button', 'chat-chip', pregunta);
        chip.type = 'button';
        chip.addEventListener('click', () => enviar(pregunta));
        sugeridasEl.appendChild(chip);
      });
    }

    function pintarEstado(motor) {
      const clase = clasificarEstado(motor);
      estado.dataset.estado = clase;
      estado.textContent = motor && motor.etiqueta ? motor.etiqueta : 'Estado desconocido';
      estado.title = motor && motor.detalle ? motor.detalle : '';
    }

    async function cargarEstado() {
      try {
        const respuesta = await global.fetch('/api/asistente/estado?pantalla=' + encodeURIComponent(global.location.pathname), {
          headers: { Accept: 'application/json' },
          credentials: 'same-origin',
        });
        if (!respuesta.ok) throw new Error('estado ' + respuesta.status);
        const datos = await respuesta.json();
        pintarEstado(datos.motor);
        renderizarSugeridas(datos.sugeridas);
        estadoCargado = true;
      } catch (error) {
        pintarEstado({ modo: 'integrado', disponible: false, etiqueta: 'Sin conexión con el servidor' });
        estado.dataset.estado = 'error';
      }
    }

    function indicadorPensando(mostrar) {
      let indicador = mensajesEl.querySelector('[data-pensando]');
      if (mostrar && !indicador) {
        indicador = elemento('div', 'chat-msg chat-msg-asistente chat-pensando', 'Consultando…');
        indicador.dataset.pensando = '1';
        mensajesEl.appendChild(indicador);
        mensajesEl.scrollTop = mensajesEl.scrollHeight;
      } else if (!mostrar && indicador) {
        indicador.remove();
      }
    }

    async function enviar(texto) {
      const pregunta = String(texto || '').trim();
      if (!pregunta || enviando) return;
      mostrarError('');
      mensajes.push({ rol: 'usuario', texto: pregunta });
      guardarHistorial();
      renderizarTodo();
      input.value = '';
      enviando = true;
      enviarBtn.disabled = true;
      indicadorPensando(true);
      try {
        const respuesta = await global.fetch('/api/asistente/preguntar', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({
            pregunta,
            historial: historialParaEnviar(mensajes.slice(0, -1)),
            pantalla: global.location.pathname,
          }),
        });
        if (respuesta.status === 401) {
          mostrarError('Tu sesión venció. Volvé a iniciar sesión para seguir.');
          return;
        }
        if (respuesta.status === 403) {
          mostrarError('No tenés permiso para usar el asistente.');
          return;
        }
        let datos = null;
        try {
          datos = await respuesta.json();
        } catch (error) {
          datos = null;
        }
        if (!respuesta.ok) {
          mostrarError((datos && datos.error) || 'El asistente no pudo responder (' + respuesta.status + ').');
          return;
        }
        mensajes.push({
          rol: 'asistente',
          texto: datos.texto,
          origen: datos.origen,
          modelo: datos.modelo,
          acciones: datos.acciones,
          advertencias: datos.advertencias,
        });
        guardarHistorial();
        renderizarTodo();
      } catch (error) {
        mostrarError('No se pudo contactar al servidor. Revisá que Twinx Express siga abierto.');
      } finally {
        enviando = false;
        enviarBtn.disabled = false;
        indicadorPensando(false);
        input.focus();
      }
    }

    function alternar(abrir) {
      abierto = abrir === undefined ? !abierto : Boolean(abrir);
      panel.hidden = !abierto;
      panel.setAttribute('aria-hidden', abierto ? 'false' : 'true');
      if (backdrop) backdrop.classList.toggle('hidden', !abierto);
      if (boton) boton.setAttribute('aria-expanded', abierto ? 'true' : 'false');
      documento.body.classList.toggle('chat-abierto', abierto);
      if (abierto) {
        renderizarTodo();
        if (!estadoCargado) cargarEstado();
        input.focus();
      } else if (boton) {
        boton.focus();
      }
    }

    if (boton) boton.addEventListener('click', () => alternar(true));
    if (cerrar) cerrar.addEventListener('click', () => alternar(false));
    if (backdrop) backdrop.addEventListener('click', () => alternar(false));
    if (borrar) {
      borrar.addEventListener('click', () => {
        mensajes = [];
        try {
          global.sessionStorage.removeItem(clave);
        } catch (error) {
          /* sin storage */
        }
        renderizarTodo();
        input.focus();
      });
    }
    form.addEventListener('submit', (evento) => {
      evento.preventDefault();
      enviar(input.value);
    });
    input.addEventListener('keydown', (evento) => {
      if (evento.key === 'Enter' && !evento.shiftKey) {
        evento.preventDefault();
        enviar(input.value);
      }
    });
    documento.addEventListener('keydown', (evento) => {
      if (evento.key === 'Escape' && abierto) alternar(false);
    });

    return { alternar, enviar, cargarEstado };
  }

  const api = { esHrefSeguro, historialParaEnviar, clasificarEstado, bloquesDeTexto, etiquetaOrigen, iniciar };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    global.TwinxAsistente = api;
    if (global.document) {
      global.document.addEventListener('DOMContentLoaded', () => {
        const instancia = iniciar(global.document);
        if (instancia) global.TwinxAsistente.instancia = instancia;
      });
    }
  }
})(typeof window !== 'undefined' ? window : globalThis);
