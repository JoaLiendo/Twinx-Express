/**
 * Carrito del punto de venta: vive enteramente en el navegador para que
 * agregar/quitar productos sea instantáneo (sin recargar la página). La
 * venta recién toca el servidor al hacer clic en "Cobrar", vía
 * POST /api/ventas (services.servicio_ventas.registrar_venta hace la
 * validación real de stock del lado del servidor).
 */
(function () {
  const carrito = new Map(); // producto_id -> { nombre, precioCentavos, cantidad, stockDisponible }
  let totalActualCentavos = 0; // último total calculado por renderizarCarrito(), lo usa el bloque de efectivo

  // Fase 5C (corrección de auditoría, IMPORTANTE 1): true mientras hay un
  // POST /api/ventas en vuelo. actualizarEstadoCobrar() lo consulta como
  // primera condición de "disabled" -- así ninguna modificación del
  // carrito/tipo de pago/monto recibido que ocurra DURANTE ese fetch (el
  // único código que sigue corriendo mientras se espera la respuesta)
  // puede volver a habilitar "Cobrar". Lo pone en `true`/`false`
  // exclusivamente cobrar(), en los mismos puntos donde ya administraba
  // establecerEstadoProcesando().
  let cobroEnCurso = false;

  const listaCarritoEl = document.getElementById('carrito-lista');
  const vacioEl = document.getElementById('carrito-vacio');
  const totalEl = document.getElementById('carrito-total');
  const botonCobrar = document.getElementById('boton-cobrar');
  const inputCodigo = document.getElementById('input-codigo-barras');

  // Fase 5B: barra inferior fija + bottom sheet del carrito (mobile/tablet,
  // <lg). En desktop (>=lg) estos elementos existen en el DOM pero quedan
  // ocultos por CSS (`lg:hidden`) y el `<aside id="carrito-sheet">` se
  // muestra como sidebar sticky de siempre -- ver pos.html.
  const carritoBarCantidadEl = document.getElementById('carrito-bar-cantidad');
  const carritoBarTotalEl = document.getElementById('carrito-bar-total');
  const botonCobrarBar = document.getElementById('boton-cobrar-bar');
  const carritoSheet = document.getElementById('carrito-sheet');
  const carritoBackdrop = document.getElementById('carrito-backdrop');
  const carritoBarToggle = document.getElementById('carrito-bar-toggle');
  const carritoSheetCerrar = document.getElementById('carrito-sheet-cerrar');
  const botonesCobrar = [botonCobrar, botonCobrarBar].filter(Boolean);

  // Fase 5C: monto recibido + vuelto (solo EFECTIVO, ver
  // actualizarEstadoCobrar). Estado transitorio de esta página: nunca se
  // agrega al body de POST /api/ventas ni participa de la clave de
  // idempotencia (ver domain.venta.calcular_hash_contenido, que solo
  // hashea items + tipo_pago). El bloqueo de "Cobrar" que produce es
  // exclusivamente de UX -- el servidor no conoce ni valida este monto.
  const bloqueEfectivoEl = document.getElementById('bloque-efectivo');
  const inputMontoRecibido = document.getElementById('monto-recibido');
  const vueltoResultadoEl = document.getElementById('vuelto-resultado');
  const radiosTipoPago = document.querySelectorAll('input[name="tipo_pago"]');

  // Fase 5D: acciones tras un cobro exitoso ("Imprimir ticket" abre
  // GET /ventas/{id}/ticket en otra pestaña -- solo lectura, datos de
  // DB, ver esa ruta; "Nueva venta" reproduce el reload que antes
  // disparaba un setTimeout automático). El id de la venta sale de la
  // respuesta de POST /api/ventas, nunca del carrito.
  const postVentaAccionesEl = document.getElementById('post-venta-acciones');
  const postVentaImprimirEl = document.getElementById('post-venta-imprimir');
  const botonNuevaVenta = document.getElementById('post-venta-nueva-venta');
  let mostrandoAccionesPostVenta = false;

  const iconoQuitar = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" class="w-4 h-4">
      <path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m2 0-.8 12.2A2 2 0 0 1 14.2 21H9.8a2 2 0 0 1-2-1.8L7 7"></path>
    </svg>`;

  function formatearCentavos(centavos) {
    const signo = centavos < 0 ? '-' : '';
    const absoluto = Math.abs(Math.round(centavos));
    const unidades = Math.floor(absoluto / 100);
    const resto = String(absoluto % 100).padStart(2, '0');
    return `${signo}$${unidades}.${resto}`;
  }

  // Fase 5B: devuelve el foco al input de código de barras después de
  // agregar un producto (card de la grilla o Enter en el scanner), para que
  // el lector USB (que emula teclado) siga funcionando aunque el cajero
  // haya tocado una card con el mouse/dedo. No roba el foco si el usuario
  // está tipeando a mano en otro input/textarea (ej. el filtro por nombre).
  function refocarCodigoBarras() {
    if (!inputCodigo) return;
    const activo = document.activeElement;
    const escribiendoEnOtroCampo =
      activo && activo !== inputCodigo && (activo.tagName === 'INPUT' || activo.tagName === 'TEXTAREA');
    if (!escribiendoEnOtroCampo) inputCodigo.focus();
  }

  // En mobile/tablet (<lg), +/-/quitar se tocan DENTRO del bottom sheet
  // (ver pos.html): forzar el foco al input de arriba en ese momento saca
  // el foco del control recién tocado, corre el viewport y puede abrir el
  // teclado virtual -- una UX inestable dentro del sheet. En desktop
  // (>=lg) el carrito es la barra lateral de siempre, sin sheet ni teclado
  // virtual de por medio, así que ahí sí conviene reenfocar para sostener
  // el flujo de escaneo continuo. Mismo breakpoint `lg` (1024px) que ya
  // usa el CSS de pos.html/base.html, no uno nuevo.
  function refocarCodigoBarrasTrasControlDelCarrito() {
    if (window.matchMedia('(min-width: 1024px)').matches) refocarCodigoBarras();
  }

  function agregarAlCarrito(producto) {
    // Fase 5D: si el cajero escanea/toca un producto estando todavía a
    // la vista el panel de "Imprimir ticket"/"Nueva venta" de la venta
    // anterior, eso ya es el arranque de una venta nueva -- se descarta
    // el panel y se vuelve al comportamiento normal del carrito.
    mostrandoAccionesPostVenta = false;
    if (producto.stock <= 0) {
      mostrarToast(`'${producto.nombre}' no tiene stock disponible.`, 'warning');
      refocarCodigoBarras();
      return;
    }
    const existente = carrito.get(producto.id);
    const cantidadActual = existente ? existente.cantidad : 0;
    if (cantidadActual + 1 > producto.stock) {
      mostrarToast(`Solo hay ${producto.stock} unidades de '${producto.nombre}' disponibles.`, 'warning');
      refocarCodigoBarras();
      return;
    }
    carrito.set(producto.id, {
      nombre: producto.nombre,
      precioCentavos: producto.precio,
      cantidad: cantidadActual + 1,
      stockDisponible: producto.stock,
    });
    renderizarCarrito();
    refocarCodigoBarras();
  }

  function cambiarCantidad(productoId, delta) {
    const item = carrito.get(productoId);
    if (!item) return;
    const nuevaCantidad = item.cantidad + delta;
    if (nuevaCantidad <= 0) {
      carrito.delete(productoId);
    } else if (nuevaCantidad > item.stockDisponible) {
      mostrarToast(`Solo hay ${item.stockDisponible} unidades disponibles.`, 'warning');
      refocarCodigoBarrasTrasControlDelCarrito();
      return;
    } else {
      item.cantidad = nuevaCantidad;
    }
    renderizarCarrito();
    refocarCodigoBarrasTrasControlDelCarrito();
  }

  function quitarDelCarrito(productoId) {
    carrito.delete(productoId);
    renderizarCarrito();
    refocarCodigoBarrasTrasControlDelCarrito();
  }

  function renderizarCarrito() {
    listaCarritoEl.innerHTML = '';
    let total = 0;
    let unidadesTotales = 0; // suma de cantidades, no cantidad de productos distintos (carrito.size)

    // Fase 5D: con el carrito vacío, se muestra el panel de acciones
    // post-venta en vez del mensaje habitual de "carrito vacío" mientras
    // mostrandoAccionesPostVenta esté activo (lo apaga agregarAlCarrito()
    // o el propio botón "Nueva venta").
    const debeMostrarAccionesPostVenta = mostrandoAccionesPostVenta && carrito.size === 0;
    vacioEl.classList.toggle('hidden', carrito.size > 0 || debeMostrarAccionesPostVenta);
    if (postVentaAccionesEl) postVentaAccionesEl.classList.toggle('hidden', !debeMostrarAccionesPostVenta);

    for (const [id, item] of carrito.entries()) {
      const subtotal = item.precioCentavos * item.cantidad;
      total += subtotal;
      unidadesTotales += item.cantidad;

      const fila = document.createElement('div');
      fila.className = 'flex items-center gap-3 py-3.5 border-b border-slate-100 dark:border-slate-800 last:border-0';
      // Fase 5E.3 (corrección de auditoría, XSS): `item.nombre` puede ser
      // cualquier string -- `domain.producto` solo exige que no esté
      // vacío, sin límite de caracteres -- y llega tanto por
      // `data-nombre` como directo de la respuesta JSON del escaneo por
      // código de barras, sin pasar nunca por el autoescape de Jinja.
      // Antes se interpolaba con el resto del HTML dentro de
      // `innerHTML`, incluso DENTRO de `aria-label="..."` -- el lugar
      // más peligroso: una sola comilla doble en el nombre rompía el
      // atributo y permitía inyectar cualquier otro atributo/handler
      // (ej. `onmouseover=...`), sin necesitar ni una etiqueta.
      //
      // Ahora el HTML de la fila nunca contiene el nombre: se arma vacío
      // y el nombre se asigna después con `textContent`/`setAttribute`,
      // que jamás interpretan su argumento como marcado -- no existe
      // ninguna secuencia de caracteres que pueda "romper" el HTML por
      // esta vía, sin importar qué contenga `item.nombre`.
      fila.innerHTML = `
        <div class="min-w-0 flex-1">
          <p class="nombre-producto-carrito text-sm font-medium text-slate-800 dark:text-slate-200 truncate"></p>
          <p class="text-xs text-slate-400 dark:text-slate-500">${formatearCentavos(item.precioCentavos)} c/u</p>
        </div>
        <div class="flex items-center gap-1.5 shrink-0">
          <button type="button" data-accion="restar" data-id="${id}"
                  class="w-9 h-9 flex items-center justify-center rounded-md border border-slate-200 dark:border-slate-700 text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 text-sm font-medium">−</button>
          <span class="w-5 text-center text-sm font-semibold text-slate-800 dark:text-slate-200">${item.cantidad}</span>
          <button type="button" data-accion="sumar" data-id="${id}"
                  class="w-9 h-9 flex items-center justify-center rounded-md border border-slate-200 dark:border-slate-700 text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 text-sm font-medium">+</button>
        </div>
        <p class="w-16 text-right text-sm font-semibold text-slate-900 dark:text-slate-100 shrink-0">${formatearCentavos(subtotal)}</p>
        <button type="button" data-accion="quitar" data-id="${id}"
                class="p-2 -m-1 rounded-md text-slate-300 dark:text-slate-600 hover:text-error hover:bg-error/10 shrink-0">${iconoQuitar}</button>
      `;

      fila.querySelector('.nombre-producto-carrito').textContent = item.nombre;
      fila.querySelector('[data-accion="restar"]').setAttribute('aria-label', `Restar unidad de ${item.nombre}`);
      fila.querySelector('[data-accion="sumar"]').setAttribute('aria-label', `Sumar unidad de ${item.nombre}`);
      fila.querySelector('[data-accion="quitar"]').setAttribute('aria-label', `Quitar ${item.nombre} del carrito`);

      listaCarritoEl.appendChild(fila);
    }

    totalEl.textContent = formatearCentavos(total);
    if (carritoBarTotalEl) carritoBarTotalEl.textContent = formatearCentavos(total);
    if (carritoBarCantidadEl) carritoBarCantidadEl.textContent = String(unidadesTotales);

    totalActualCentavos = total;
    actualizarEstadoCobrar();
  }

  // Fase 5C: recalcula el bloque de monto recibido/vuelto y el estado
  // disabled de "Cobrar" a partir de tres cosas que pueden cambiar de
  // forma independiente: el carrito (tamaño/total), el tipo de pago
  // elegido, y lo que el cajero tipeó en "Monto recibido". Se llama
  // desde renderizarCarrito() (cambia cantidad/se agrega/se quita un
  // producto) y desde los listeners de 'change'/'input' de más abajo.
  //
  // Regla de "total $0" (ej. un producto cargado a precio $0, permitido
  // por domain.producto -- no rechaza precio == 0, solo < 0): no tiene
  // sentido pedir efectivo recibido para cobrar $0, así que el bloque se
  // oculta y "Cobrar" vuelve a depender solo de que el carrito no esté
  // vacío, igual que con cualquier tipo de pago distinto de EFECTIVO.
  function actualizarEstadoCobrar() {
    const tipoPagoInput = document.querySelector('input[name="tipo_pago"]:checked');
    const tipoPagoActual = tipoPagoInput ? tipoPagoInput.value : null;
    const requiereEfectivo = tipoPagoActual === 'EFECTIVO' && totalActualCentavos > 0;

    if (bloqueEfectivoEl) bloqueEfectivoEl.classList.toggle('hidden', !requiereEfectivo);

    let bloqueadoPorEfectivo = false;

    if (requiereEfectivo && vueltoResultadoEl) {
      const montoTexto = inputMontoRecibido ? inputMontoRecibido.value : '';
      const resultado = window.DineroCobro.textoACentavos(montoTexto);

      if (!resultado.ok) {
        bloqueadoPorEfectivo = true;
        if (resultado.motivo === 'vacio') {
          vueltoResultadoEl.textContent = '';
          vueltoResultadoEl.className = 'text-sm font-semibold min-h-[1.25rem]';
        } else {
          const mensaje = resultado.motivo === 'demasiado_grande' ? 'Monto demasiado grande.' : 'Ingresá un monto válido.';
          vueltoResultadoEl.textContent = mensaje;
          vueltoResultadoEl.className = 'text-sm font-semibold min-h-[1.25rem] text-error';
        }
      } else {
        const resultadoVuelto = window.DineroCobro.calcularResultadoCobroEfectivo(
          totalActualCentavos,
          resultado.centavos
        );
        if (!resultadoVuelto.ok) {
          // Defensivo: no debería ocurrir nunca desde acá (textoACentavos
          // ya garantizó un entero seguro arriba), pero el contrato de
          // calcularResultadoCobroEfectivo no debe fallar en silencio.
          bloqueadoPorEfectivo = true;
          vueltoResultadoEl.textContent = 'Ingresá un monto válido.';
          vueltoResultadoEl.className = 'text-sm font-semibold min-h-[1.25rem] text-error';
        } else if (resultadoVuelto.estado === 'insuficiente') {
          bloqueadoPorEfectivo = true;
          vueltoResultadoEl.textContent = `Falta ${formatearCentavos(Math.abs(resultadoVuelto.diferenciaCentavos))}`;
          vueltoResultadoEl.className = 'text-sm font-semibold min-h-[1.25rem] text-error';
        } else {
          vueltoResultadoEl.textContent = `Vuelto: ${formatearCentavos(resultadoVuelto.diferenciaCentavos)}`;
          vueltoResultadoEl.className = 'text-sm font-semibold min-h-[1.25rem] text-success';
        }
      }
    }

    // Bloqueo por efectivo insuficiente/inválido: solo UX (ver comentario
    // arriba de las const de este bloque) -- el servidor no valida esto.
    // cobroEnCurso va primero y nunca puede ser revertido por nada de lo
    // de acá arriba (ver comentario junto a su declaración).
    const carritoVacio = carrito.size === 0;
    const disabled = cobroEnCurso || carritoVacio || bloqueadoPorEfectivo;

    if (botonCobrar) {
      botonCobrar.disabled = disabled;
      botonCobrar.classList.toggle('opacity-50', disabled);
      botonCobrar.classList.toggle('cursor-not-allowed', disabled);
    }

    // Fase 5C (corrección de auditoría, IMPORTANTE 2): en la barra fija de
    // mobile/tablet, el botón "Cobrar" es la zona que un cajero toca por
    // instinto -- pero un <button disabled> jamás dispara `click` (ni
    // siquiera hacia un padre), así que cuando el único motivo de bloqueo
    // es el efectivo (carrito no vacío, sin cobro en curso) ese toque se
    // perdía sin ninguna pista de qué hacer. En ese caso puntual el botón
    // de la barra deja de estar disabled, cambia su texto a "Ingresar
    // monto" y su click (ver el addEventListener más abajo) abre el mismo
    // sheet de 5B en vez de intentar cobrar -- nunca ejecuta la venta.
    // #boton-cobrar (dentro del sheet) no lo necesita: cuando está
    // visible, el campo de monto ya está al lado.
    const bloqueadoSoloPorEfectivo = !cobroEnCurso && !carritoVacio && bloqueadoPorEfectivo;

    if (botonCobrarBar) {
      if (bloqueadoSoloPorEfectivo) {
        botonCobrarBar.disabled = false;
        botonCobrarBar.textContent = 'Ingresar monto';
        botonCobrarBar.classList.remove('opacity-50', 'cursor-not-allowed');
        botonCobrarBar.dataset.accion = 'abrir-sheet';
      } else {
        botonCobrarBar.disabled = disabled;
        botonCobrarBar.classList.toggle('opacity-50', disabled);
        botonCobrarBar.classList.toggle('cursor-not-allowed', disabled);
        delete botonCobrarBar.dataset.accion;
        // Mientras hay un cobro en curso, el texto ("Procesando..."/"...")
        // lo administra exclusivamente establecerEstadoProcesando(): acá
        // no se toca para no pisarlo si esta función se ejecuta de nuevo
        // en el medio (ej. el carrito cambia mientras la request vuela).
        if (!cobroEnCurso) botonCobrarBar.textContent = 'Cobrar';
      }
    }
  }

  radiosTipoPago.forEach((radio) => radio.addEventListener('change', actualizarEstadoCobrar));
  if (inputMontoRecibido) inputMontoRecibido.addEventListener('input', actualizarEstadoCobrar);

  listaCarritoEl.addEventListener('click', (evento) => {
    const boton = evento.target.closest('button[data-accion]');
    if (!boton) return;
    const id = Number(boton.dataset.id);
    if (boton.dataset.accion === 'sumar') cambiarCantidad(id, 1);
    if (boton.dataset.accion === 'restar') cambiarCantidad(id, -1);
    if (boton.dataset.accion === 'quitar') quitarDelCarrito(id);
  });

  document.querySelectorAll('[data-producto-card]').forEach((tarjeta) => {
    tarjeta.addEventListener('click', () => {
      if (tarjeta.disabled) return;
      agregarAlCarrito({
        id: Number(tarjeta.dataset.id),
        nombre: tarjeta.dataset.nombre,
        precio: Number(tarjeta.dataset.precio),
        stock: Number(tarjeta.dataset.stock),
      });
    });
  });

  if (inputCodigo) {
    inputCodigo.addEventListener('keydown', async (evento) => {
      if (evento.key !== 'Enter') return;
      evento.preventDefault();
      const codigo = inputCodigo.value.trim();
      inputCodigo.value = '';
      if (!codigo) return;

      try {
        const respuesta = await fetch(`/api/productos/buscar-codigo/${encodeURIComponent(codigo)}`);
        const datos = await respuesta.json();
        if (!respuesta.ok) {
          mostrarToast(datos.error || 'No se encontró el producto.', 'error');
          return;
        }
        agregarAlCarrito({
          id: datos.id,
          nombre: datos.nombre,
          precio: datos.precio_venta_centavos,
          stock: datos.stock_actual,
        });
      } catch (error) {
        mostrarToast('No se pudo consultar el producto. Revisá tu conexión.', 'error');
      }
    });
  }

  // Fase 5B: controlador chico y específico del bottom sheet del carrito
  // (mobile/tablet, <lg) -- deliberadamente no se generaliza `alternarDrawer()`
  // de base.html ni se crea una abstracción de "overlay" reutilizable: es el
  // mismo patrón visual (backdrop + transform + toggle), aplicado en su
  // propio archivo porque el carrito es un componente propio de esta página.
  //
  // Fase 5C (corrección de auditoría, IMPORTANTE 2): abrirCarritoSheet()
  // quedó a nivel de módulo (antes vivía solo dentro del `if` de más abajo)
  // para que el click de "Ingresar monto" en #boton-cobrar-bar pueda
  // llamarla directamente -- sigue siendo la misma función de 5B, con un
  // guard defensivo por si los elementos no existieran.
  function cerrarConEscape(evento) {
    if (evento.key === 'Escape') cerrarCarritoSheet();
  }

  function abrirCarritoSheet() {
    if (!carritoSheet || !carritoBackdrop || !carritoBarToggle) return;
    carritoSheet.classList.remove('translate-y-full');
    carritoBackdrop.classList.remove('hidden');
    carritoBarToggle.setAttribute('aria-expanded', 'true');
    document.addEventListener('keydown', cerrarConEscape);
    if (carritoSheetCerrar) carritoSheetCerrar.focus();
  }

  function cerrarCarritoSheet() {
    if (!carritoSheet || !carritoBackdrop || !carritoBarToggle) return;
    carritoSheet.classList.add('translate-y-full');
    carritoBackdrop.classList.add('hidden');
    carritoBarToggle.setAttribute('aria-expanded', 'false');
    document.removeEventListener('keydown', cerrarConEscape);
    carritoBarToggle.focus();
  }

  if (carritoSheet && carritoBackdrop && carritoBarToggle) {
    carritoBarToggle.addEventListener('click', abrirCarritoSheet);
    carritoBackdrop.addEventListener('click', cerrarCarritoSheet);
    if (carritoSheetCerrar) carritoSheetCerrar.addEventListener('click', cerrarCarritoSheet);
  }

  // Fase 5A: una clave de idempotencia por intento real de cobro, nunca
  // una nueva por cada fetch -- así el servidor puede reconocer un
  // reintento (doble clic, error de red, "atrás y reenviar") y nunca
  // duplicar la venta (ver services.servicio_ventas.registrar_venta).
  // Vive únicamente en esta variable de módulo mientras dura la
  // pestaña: nunca se guarda en localStorage/sessionStorage/IndexedDB
  // (Fase 5A no implementa persistencia ni soporte offline).
  let claveIdempotenciaActual = null;

  // Fase 5B: la barra inferior fija agrega un segundo botón visual
  // ("boton-cobrar-bar") que dispara exactamente esta misma función --
  // nunca un segundo fetch ni una segunda clave de idempotencia.
  function establecerEstadoProcesando(procesando) {
    if (botonCobrar) botonCobrar.textContent = procesando ? 'Procesando...' : 'Cobrar';
    if (botonCobrarBar) botonCobrarBar.textContent = procesando ? '...' : 'Cobrar';
    for (const boton of botonesCobrar) boton.disabled = procesando;
  }

  async function cobrar() {
    if (carrito.size === 0) return;
    const tipoPagoInput = document.querySelector('input[name="tipo_pago"]:checked');
    if (!tipoPagoInput) {
      mostrarToast('Elegí un tipo de pago.', 'warning');
      return;
    }

    const items = Array.from(carrito.entries()).map(([id, item]) => ({
      producto_id: id,
      cantidad: item.cantidad,
    }));

    if (!claveIdempotenciaActual) {
      claveIdempotenciaActual = crypto.randomUUID();
    }

    // Fase 5C (corrección de auditoría, IMPORTANTE 1): a partir de acá y
    // hasta que la request termine (éxito o error), cobroEnCurso hace que
    // actualizarEstadoCobrar() nunca pueda rehabilitar "Cobrar", sin
    // importar qué haga el usuario con el carrito/tipo de pago/monto
    // mientras tanto. establecerEstadoProcesando(true) además deshabilita
    // ambos botones de forma síncrona, antes de cualquier `await`.
    cobroEnCurso = true;
    establecerEstadoProcesando(true);

    try {
      const respuesta = await fetch('/api/ventas', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items,
          tipo_pago: tipoPagoInput.value,
          clave_idempotencia: claveIdempotenciaActual,
        }),
      });
      const datos = await respuesta.json();

      if (!respuesta.ok) {
        mostrarToast(datos.error || 'No se pudo registrar la venta.', 'error');
        cobroEnCurso = false;
        establecerEstadoProcesando(false);
        // No alcanza con "reactivar" -- hay que recalcular el estado REAL
        // según el carrito/efectivo actuales (pueden haber cambiado
        // mientras la request estaba en vuelo).
        actualizarEstadoCobrar();
        // Se conserva claveIdempotenciaActual: un reintento de este
        // mismo intento de cobro debe reutilizarla, no generar otra.
        return;
      }

      mostrarToast(`Venta #${datos.id} registrada. Total: ${formatearCentavos(datos.total_centavos)}.`, 'success');
      claveIdempotenciaActual = null; // el intento terminó bien: el próximo cobro arranca uno nuevo
      cobroEnCurso = false;
      carrito.clear();
      // Fase 5D: en vez de recargar solo tras 1.3s, se le da al cajero
      // una acción explícita ("Imprimir ticket"/"Nueva venta") y el
      // tiempo que necesite para elegirla antes de que la página se
      // reinicie. `datos.id` es el id que devolvió el servidor -- el
      // ticket nunca se arma con datos del carrito (que además ya se
      // vació arriba).
      mostrandoAccionesPostVenta = true;
      if (postVentaImprimirEl) postVentaImprimirEl.href = `/ventas/${datos.id}/ticket`;
      renderizarCarrito();
    } catch (error) {
      mostrarToast('No se pudo registrar la venta. Revisá tu conexión.', 'error');
      cobroEnCurso = false;
      establecerEstadoProcesando(false);
      actualizarEstadoCobrar();
      // Igual que ante un error del servidor: se conserva la clave
      // para que el reintento del usuario sea, para el backend, el
      // mismo intento -- no uno nuevo.
    }
  }

  if (botonCobrar) botonCobrar.addEventListener('click', cobrar);
  if (botonCobrarBar) {
    botonCobrarBar.addEventListener('click', () => {
      // Fase 5C (corrección de auditoría, IMPORTANTE 2): mientras el
      // bloqueo es solo por efectivo, este botón no cobra -- abre el
      // sheet para que el cajero cargue el monto (ver actualizarEstadoCobrar,
      // que es quien pone/saca este dataset). Nunca ejecuta la venta acá.
      if (botonCobrarBar.dataset.accion === 'abrir-sheet') {
        abrirCarritoSheet();
        return;
      }
      cobrar();
    });
  }

  if (botonNuevaVenta) {
    // Recarga la página, igual que hacía el setTimeout automático antes
    // de Fase 5D: es la forma más simple de volver a un POS limpio con
    // stock/productos frescos, ahora disparada por el cajero, no sola.
    botonNuevaVenta.addEventListener('click', () => {
      window.location.reload();
    });
  }

  renderizarCarrito();
  if (inputCodigo) inputCodigo.focus();
})();
