// Script clásico y bloqueante: fija el tema antes del primer pintado para evitar parpadeo.
// Orden: preferencia guardada > prefers-color-scheme > oscuro.
(function () {
  var theme = null;
  try {
    var saved = localStorage.getItem('avz_theme');
    if (saved === 'light' || saved === 'dark') theme = saved;
  } catch (e) { /* almacenamiento bloqueado */ }
  if (!theme) {
    try {
      theme = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    } catch (e) { theme = 'dark'; }
  }
  document.documentElement.setAttribute('data-theme', theme);
})();
