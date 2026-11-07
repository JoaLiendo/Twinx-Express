// Tailwind compilado y autohospedado (V1.17-B). Reemplaza al Play CDN que cargaba `base.html`: la
// aplicación es de escritorio y local-first, tiene que abrir sin conectividad. El CSS resultante se
// versiona (`interfaces/web/static/css/twinx.css`) para que el ejecutable no dependa de Node.
//
//   npm run build:css        (una vez, tras tocar clases en templates o JS)
//   npm run build:css:watch  (mientras se diseña)
//
// Solo se generan las clases que aparecen en los templates y en el JS propio; una clase armada en
// tiempo de ejecución por concatenación no existiría en el CSS (hoy no hay ninguna: ver
// `tests/test_interfaces_web/test_css_compilado.py`).
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./interfaces/web/templates/**/*.html', './interfaces/web/static/js/**/*.js'],
  // Las clases del design system se emiten siempre: varias se arman en los macros de Jinja
  // (`badge-{{ variante }}`) y el escaneo de contenido no las vería.
  safelist: [
    {
      pattern: /^(btn|badge|alert|kpi|card|nav-pill|segmented|input|select|textarea|label|data-table|empty-state|page|field-error|muted|section-title|table-wrap)(-[a-z0-9-]+)?$/,
    },
  ],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: { sans: ['Plus Jakarta Sans', 'ui-sans-serif', 'system-ui', 'sans-serif'] },
      // Canales RGB de los tokens (ver twinx.src.css) para que funcionen las opacidades (`bg-error/10`).
      colors: {
        primary: {
          DEFAULT: 'rgb(var(--color-primary-rgb) / <alpha-value>)',
          hover: 'rgb(var(--color-primary-hover-rgb) / <alpha-value>)',
          subtle: 'rgb(var(--color-primary-subtle-rgb) / <alpha-value>)',
        },
        success: 'rgb(var(--color-success-rgb) / <alpha-value>)',
        warning: 'rgb(var(--color-warning-rgb) / <alpha-value>)',
        error: 'rgb(var(--color-error-rgb) / <alpha-value>)',
        info: 'rgb(var(--color-info-rgb) / <alpha-value>)',
      },
    },
  },
};
