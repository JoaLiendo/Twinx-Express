/**
 * Preview de la imagen elegida en el formulario de alta/edición de
 * producto, antes de enviar el formulario. Puramente cosmético: la
 * validación real (formato/tamaño/contenido) ocurre en el servidor
 * (services.servicio_imagenes) -- esto solo evita que el usuario
 * tenga que enviar el formulario para darse cuenta de qué archivo
 * eligió.
 */
(function () {
  const input = document.getElementById('input-imagen');
  const preview = document.getElementById('preview-imagen');
  const placeholder = document.getElementById('placeholder-imagen');
  const checkboxQuitar = document.getElementById('checkbox-quitar-imagen');
  const nombreArchivo = document.getElementById('nombre-archivo-imagen');

  if (!input || !preview) return;

  input.addEventListener('change', () => {
    const archivo = input.files[0];

    // El input real queda oculto (`sr-only`, ver C1-05); este span es la
    // única forma de que el usuario vea qué archivo eligió, ya que el
    // texto nativo del navegador ("No file chosen") no se puede traducir.
    if (nombreArchivo) {
      nombreArchivo.textContent = archivo ? archivo.name : 'Ningún archivo seleccionado';
    }

    if (!archivo) return;

    preview.src = URL.createObjectURL(archivo);
    preview.classList.remove('hidden');
    if (placeholder) placeholder.classList.add('hidden');
    if (checkboxQuitar) checkboxQuitar.checked = false;
  });
})();
