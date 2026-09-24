/**
 * Evita el doble envío de cualquier formulario POST (V1.1): el segundo clic
 * (o el segundo Enter) sobre un formulario ya enviado se descarta y el botón
 * queda deshabilitado mientras el navegador espera la respuesta. Es solo la
 * primera capa: las operaciones que mueven dinero o stock además llevan una
 * clave de idempotencia que el servidor hace cumplir.
 */
(function () {
  function botonesDe(formulario) {
    return formulario.querySelectorAll('button[type="submit"], input[type="submit"]');
  }

  function liberar(formulario) {
    delete formulario.dataset.enviando;
    botonesDe(formulario).forEach((boton) => {
      boton.disabled = false;
      boton.classList.remove('opacity-60', 'cursor-not-allowed');
    });
  }

  document.addEventListener('submit', (evento) => {
    const formulario = evento.target;
    if (!(formulario instanceof HTMLFormElement)) return;
    if ((formulario.method || '').toLowerCase() !== 'post') return;
    if (formulario.target === '_blank') return;

    if (formulario.dataset.enviando === '1') {
      evento.preventDefault();
      return;
    }
    formulario.dataset.enviando = '1';

    // Se decide recién después de que corrieron todos los demás handlers: si
    // alguno canceló el envío (validación propia), el formulario no queda trabado.
    setTimeout(() => {
      if (evento.defaultPrevented) {
        liberar(formulario);
        return;
      }
      botonesDe(formulario).forEach((boton) => {
        boton.disabled = true;
        boton.classList.add('opacity-60', 'cursor-not-allowed');
      });
    }, 0);
  });

  // "Atrás" desde el historial del navegador puede restaurar la página con el
  // formulario ya marcado como enviado.
  window.addEventListener('pageshow', (evento) => {
    if (evento.persisted) document.querySelectorAll('form[data-enviando]').forEach(liberar);
  });
})();
