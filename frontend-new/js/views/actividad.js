import { h, replaceChildren, fmtDate } from '../dom.js';
import { api, optional } from '../api.js';
import { pageHeader, skeletonList, errorState, comingSoon, emptyState, field } from '../components.js';
import { humanAccion } from './panel.js';

export default async function actividad(ctx) {
  const body = h('div', { class: 'stack' });
  ctx.container.append(pageHeader({ title: 'Actividad', subtitle: 'Quién hizo qué y cuándo. Solo la ve el administrador.', back: { href: '#/cuenta', label: 'Volver a mi cuenta' } }), body);
  replaceChildren(body, skeletonList(6));
  let rows;
  try { rows = await optional(() => api.get('/audit', { query: { limit: 50 } })); } catch (e) {
    if (!ctx.alive()) return;
    replaceChildren(body, errorState({ message: e.message, onRetry: () => ctx.navigate('/actividad') }));
    return;
  }
  if (!ctx.alive()) return;
  if (rows == null) { replaceChildren(body, comingSoon('El registro de actividad')); return; }
  if (!rows.length) { replaceChildren(body, emptyState({ iconName: 'activity', title: 'Sin actividad registrada', text: 'Cuando alguien haga cambios los verás en esta lista.' })); return; }

  const q = field({ id: 'act-q', label: 'Buscar', type: 'search', placeholder: 'Persona o acción', autocomplete: 'off' });
  const tbody = h('tbody');
  function paint() {
    const t = q.value.trim().toLowerCase();
    const shown = rows.filter((r) => !t || `${r.usuario_email} ${humanAccion(r.accion)} ${r.detalles}`.toLowerCase().includes(t));
    replaceChildren(tbody, shown.length ? shown.map((r) => h('tr', null,
      h('td', { 'data-label': 'Cuándo' }, fmtDate(r.fecha)),
      h('td', { 'data-label': 'Quién' }, r.usuario_email || 'Sistema'),
      h('td', { 'data-label': 'Qué hizo' }, h('strong', null, humanAccion(r.accion))),
      h('td', { 'data-label': 'Detalle', class: 'td-detail' }, r.detalles || '—')))
      : h('tr', null, h('td', { colspan: 4 }, 'Nada coincide con tu búsqueda.')));
  }
  q.input.addEventListener('input', paint);
  replaceChildren(body, h('div', { class: 'toolbar' }, q.el),
    h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
      h('caption', { class: 'sr-only' }, 'Últimas acciones registradas'),
      h('thead', null, h('tr', null, ['Cuándo', 'Quién', 'Qué hizo', 'Detalle'].map((c) => h('th', { scope: 'col' }, c)))), tbody)));
  paint();
}
