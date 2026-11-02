/**
 * Listado de selección manual del POS: búsqueda y filtros sobre la tabla de
 * productos que el servidor ya renderizó en `ventas/pos.html`.
 *
 * Filtra en el navegador porque `GET /ventas` ya entrega el catálogo activo
 * completo: así cada tecla responde al instante, sin recargar la página (que
 * perdería el carrito) ni sumar consultas al servidor. Este módulo no decide
 * nada comercial: agregar al carrito pasa siempre por `alAgregar` (el
 * `agregarAlCarrito` de pos.js, con sus reglas de stock y precio de siempre).
 */
(function (raiz) {
  // Tope de filas visibles a la vez: con un catálogo grande la tabla sigue
  // liviana y el contador invita a refinar la búsqueda.
  const LIMITE_VISIBLE = 200;

  function normalizarTexto(texto) {
    return String(texto || '')
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase()
      .trim();
  }

  // Misma regla que `Producto.tiene_stock_critico`: el stock en 0 no es "bajo", es "sin stock".
  function estadoDeStock(stock, stockMinimo) {
    if (stock <= 0) return 'sin_stock';
    if (stock <= stockMinimo) return 'bajo';
    return 'disponible';
  }

  /**
   * `producto`: { nombre, codigo, categorias: ids de su categoría y ancestros, stock, stockMinimo }.
   * `filtros`: { texto, categoriaId, stock } -- `stock` es '', 'con_stock', 'sin_stock' o 'bajo'.
   * Cada palabra del texto debe aparecer en el nombre o en el código de barras.
   */
  function coincide(producto, filtros) {
    const palabras = normalizarTexto(filtros.texto).split(/\s+/).filter(Boolean);
    if (palabras.length) {
      const buscable = `${normalizarTexto(producto.nombre)} ${normalizarTexto(producto.codigo)}`;
      if (!palabras.every((palabra) => buscable.includes(palabra))) return false;
    }
    if (filtros.categoriaId && !producto.categorias.includes(String(filtros.categoriaId))) return false;
    if (filtros.stock) {
      const estado = estadoDeStock(producto.stock, producto.stockMinimo);
      if (filtros.stock === 'con_stock' && estado === 'sin_stock') return false;
      if (filtros.stock === 'sin_stock' && estado !== 'sin_stock') return false;
      if (filtros.stock === 'bajo' && estado !== 'bajo') return false;
    }
    return true;
  }

  /**
   * Qué producto agrega Enter en la búsqueda, o `null` si la elección es ambigua. Un código de barras
   * exacto gana siempre (aunque otro filtro lo oculte: es lo que haría el lector); si no, solo cuando
   * queda un único resultado visible.
   */
  function elegirParaEnter(todos, visibles, texto) {
    const buscado = normalizarTexto(texto);
    if (buscado) {
      const exacto = todos.find((producto) => normalizarTexto(producto.codigo) === buscado);
      if (exacto) return exacto;
    }
    return visibles.length === 1 ? visibles[0] : null;
  }

  function textoContador(cantidad) {
    return cantidad === 1 ? '1 producto encontrado' : `${cantidad} productos encontrados`;
  }

  const ETIQUETAS_ESTADO = {
    disponible: { texto: 'Disponible', clase: 'text-success' },
    bajo: { texto: 'Stock bajo', clase: 'text-warning' },
    sin_stock: { texto: 'Sin stock', clase: 'text-slate-400 dark:text-slate-500' },
  };

  function iniciar(documento, opciones) {
    const contenedor = documento.getElementById('listado-productos');
    if (!contenedor) return null;
    const alAgregar = opciones.alAgregar;
    const inputBusqueda = documento.getElementById('buscar-producto');
    const selectCategoria = documento.getElementById('filtro-categoria');
    const selectStock = documento.getElementById('filtro-stock');
    const botonLimpiar = documento.getElementById('limpiar-filtros');
    const contadorEl = documento.getElementById('listado-contador');
    const tablaEl = documento.getElementById('listado-tabla');
    const sinResultadosEl = documento.getElementById('listado-sin-resultados');
    const cuerpo = contenedor.querySelector('tbody');

    const productos = Array.from(contenedor.querySelectorAll('tr[data-producto-fila]')).map((fila) => ({
      fila,
      id: Number(fila.dataset.id),
      nombre: fila.dataset.nombre,
      codigo: fila.dataset.codigo,
      categorias: (fila.dataset.categorias || '').split(' ').filter(Boolean),
      precio: Number(fila.dataset.precio),
      stock: Number(fila.dataset.stock),
      stockMinimo: Number(fila.dataset.stockMinimo),
    }));
    let visibles = productos;

    function filtrosActuales() {
      return {
        texto: inputBusqueda ? inputBusqueda.value : '',
        categoriaId: selectCategoria ? selectCategoria.value : '',
        stock: selectStock ? selectStock.value : '',
      };
    }

    function hayFiltros(filtros) {
      return Boolean(filtros.texto.trim() || filtros.categoriaId || filtros.stock);
    }

    function aplicarFiltros() {
      const filtros = filtrosActuales();
      visibles = productos.filter((producto) => coincide(producto, filtros));
      const mostrados = new Set(visibles.slice(0, LIMITE_VISIBLE));
      for (const producto of productos) producto.fila.hidden = !mostrados.has(producto);

      // El único resultado de una búsqueda se resalta: es el que agrega Enter.
      const unico = visibles.length === 1 && hayFiltros(filtros) ? visibles[0] : null;
      for (const producto of productos) producto.fila.toggleAttribute('data-unico', producto === unico);

      let texto = textoContador(visibles.length);
      if (visibles.length > LIMITE_VISIBLE) texto += ` · mostrando ${LIMITE_VISIBLE}, refiná la búsqueda`;
      else if (unico) texto += ' · Enter en la búsqueda para agregarlo';
      if (contadorEl) contadorEl.textContent = texto;

      if (tablaEl) tablaEl.classList.toggle('hidden', visibles.length === 0);
      if (sinResultadosEl) sinResultadosEl.classList.toggle('hidden', visibles.length > 0);
      if (botonLimpiar) botonLimpiar.classList.toggle('hidden', !hayFiltros(filtros));
    }

    function limpiarFiltros() {
      if (inputBusqueda) inputBusqueda.value = '';
      if (selectCategoria) selectCategoria.value = '';
      if (selectStock) selectStock.value = '';
      aplicarFiltros();
      if (inputBusqueda) inputBusqueda.focus();
    }

    function agregar(producto) {
      alAgregar({ id: producto.id, nombre: producto.nombre, precio: producto.precio, stock: producto.stock });
    }

    function actualizarFila(producto) {
      const fila = producto.fila;
      fila.dataset.stock = String(producto.stock);
      const estado = estadoDeStock(producto.stock, producto.stockMinimo);
      // Cada fila muestra el stock en dos lugares (columna propia o debajo del nombre, según el ancho).
      fila.querySelectorAll('[data-stock-numero]').forEach((numero) => {
        numero.textContent = String(producto.stock);
      });
      fila.querySelectorAll('[data-stock-estado]').forEach((etiqueta) => {
        for (const { clase } of Object.values(ETIQUETAS_ESTADO)) etiqueta.classList.remove(...clase.split(' '));
        etiqueta.classList.add(...ETIQUETAS_ESTADO[estado].clase.split(' '));
        etiqueta.textContent = ETIQUETAS_ESTADO[estado].texto;
        etiqueta.dataset.stockEstado = estado;
      });
      if (estado === 'sin_stock') {
        const boton = fila.querySelector('[data-agregar]');
        if (boton) boton.disabled = true;
        fila.setAttribute('aria-disabled', 'true');
      }
    }

    // Tras un cobro confirmado: descuenta lo vendido de la tabla para que no muestre stock viejo
    // hasta la próxima recarga. Es solo visual; el servidor sigue validando el stock real.
    function descontarStock(itemsVendidos) {
      const vendidoPorId = new Map(itemsVendidos.map((item) => [item.producto_id, item.cantidad]));
      for (const producto of productos) {
        const vendido = vendidoPorId.get(producto.id);
        if (!vendido) continue;
        producto.stock = Math.max(0, producto.stock - vendido);
        actualizarFila(producto);
      }
      aplicarFiltros();
    }

    if (cuerpo) {
      // Toda la fila es seleccionable; el botón "Agregar" es el destino de teclado (Tab).
      cuerpo.addEventListener('click', (evento) => {
        const fila = evento.target.closest('tr[data-producto-fila]');
        if (!fila || fila.getAttribute('aria-disabled') === 'true') return;
        const producto = productos.find((candidato) => candidato.fila === fila);
        if (producto) agregar(producto);
      });
    }

    if (inputBusqueda) {
      inputBusqueda.addEventListener('input', aplicarFiltros);
      inputBusqueda.addEventListener('keydown', (evento) => {
        if (evento.key === 'Enter') {
          evento.preventDefault();
          const elegido = elegirParaEnter(productos, visibles, inputBusqueda.value);
          if (elegido) {
            agregar(elegido);
            // El texto queda seleccionado: la próxima búsqueda (o escaneo) lo reemplaza.
            inputBusqueda.select();
          } else if (visibles.length > 1) {
            opciones.avisar(`Hay ${visibles.length} productos con esa búsqueda: elegí uno de la lista o seguí escribiendo.`);
          }
          return;
        }
        if (evento.key === 'Escape') {
          evento.preventDefault();
          if (hayFiltros(filtrosActuales())) limpiarFiltros();
          else if (opciones.alSalir) opciones.alSalir();
        }
      });
    }
    if (selectCategoria) selectCategoria.addEventListener('change', aplicarFiltros);
    if (selectStock) selectStock.addEventListener('change', aplicarFiltros);
    if (botonLimpiar) botonLimpiar.addEventListener('click', limpiarFiltros);
    documento.querySelectorAll('[data-limpiar-filtros]').forEach((boton) => boton.addEventListener('click', limpiarFiltros));

    aplicarFiltros();
    return { descontarStock };
  }

  const api = { LIMITE_VISIBLE, normalizarTexto, estadoDeStock, coincide, elegirParaEnter, textoContador, iniciar };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (raiz) {
    raiz.PosListado = api;
  }
})(typeof window !== 'undefined' ? window : undefined);
