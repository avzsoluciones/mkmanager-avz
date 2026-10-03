import { h, replaceChildren } from '../dom.js';
import { icon, hasIcon } from '../icons.js';
import { api, optional } from '../api.js';
import { pageHeader, skeletonCards, errorState, comingSoon, field, riskBadge, emptyState } from '../components.js';
import { listDevices, categoriaDe, CATEGORIAS } from '../data.js';

export const iconFor = (t, cat) => (t.icono && hasIcon(t.icono) ? t.icono : categoriaDe(cat || t.categoria).icon);

export default async function servicios(ctx) {
  const body = h('div', { class: 'stack-lg' });
  ctx.container.append(pageHeader({ title: 'Servicios', subtitle: 'Elige qué quieres hacer en un equipo. Siempre verás una vista previa antes de aplicar nada.' }), body);
  replaceChildren(body, skeletonCards(6));

  let templates, devices;
  try {
    [templates, devices] = await Promise.all([optional(() => api.get('/templates')), listDevices().catch(() => [])]);
  } catch (e) {
    if (!ctx.alive()) return;
    replaceChildren(body, errorState({ message: e.message, onRetry: () => ctx.navigate('/servicios') }));
    return;
  }
  if (!ctx.alive()) return;
  if (templates == null) { replaceChildren(body, comingSoon('El catálogo de servicios')); return; }
  if (!templates.length) { replaceChildren(body, emptyState({ iconName: 'sliders', title: 'El catálogo está vacío', text: 'Todavía no hay servicios publicados para tu cuenta.' })); return; }
  if (!devices.length) {
    replaceChildren(body, emptyState({ iconName: 'router', title: 'Primero necesitas un equipo', text: 'Los servicios se aplican a un router. Crea uno y vuelve aquí.', action: h('a', { class: 'btn btn-primary', href: '#/nuevo-router' }, icon('plus', { size: 18 }), 'Nuevo router') }));
    return;
  }

  const sel = field({
    id: 'svc-equipo', label: '¿En qué equipo?', type: 'select', value: ctx.query.get('equipo') || '',
    options: [{ value: '', label: 'Elige un equipo…' }, ...devices.map((d) => ({ value: String(d.id), label: `${d.nombre} · ${d.sede}` }))],
    hint: 'Puedes elegirlo ahora o en el siguiente paso.',
  });
  const q = field({ id: 'svc-q', label: 'Buscar servicio', type: 'search', placeholder: 'Ej. puertos, Wi-Fi, velocidad', autocomplete: 'off' });
  const list = h('div', { class: 'stack-lg' });

  function paint() {
    const term = q.value.trim().toLowerCase();
    const shown = templates.filter((t) => !term || `${t.nombre} ${t.descripcion}`.toLowerCase().includes(term));
    const groups = new Map();
    for (const t of shown) {
      const c = categoriaDe(t.categoria);
      if (!groups.has(c.key)) groups.set(c.key, { c, items: [] });
      groups.get(c.key).items.push(t);
    }
    const ordered = [...groups.values()].sort((a, b) => {
      const ia = CATEGORIAS.findIndex((x) => x.key === a.c.key); const ib = CATEGORIAS.findIndex((x) => x.key === b.c.key);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    if (!ordered.length) { replaceChildren(list, emptyState({ iconName: 'search', title: 'No encontramos ese servicio', text: 'Prueba con otra palabra.' })); return; }
    replaceChildren(list, ordered.map(({ c, items }) => h('section', { 'aria-labelledby': `cat-${c.key.replace(/\W+/g, '')}` },
      h('div', { class: 'cat-head' },
        h('span', { class: `cat-ico bg-${c.color}` }, icon(c.icon, { size: 20 })),
        h('div', null, h('h2', { class: 'cat-title', id: `cat-${c.key.replace(/\W+/g, '')}` }, c.label), c.desc ? h('p', { class: 'muted' }, c.desc) : null)),
      h('div', { class: 'cards-grid' }, items.map((t) => h('a', {
        class: 'card card-link svc-card',
        href: `#/servicios/${encodeURIComponent(t.id)}${sel.value ? `?equipo=${encodeURIComponent(sel.value)}` : ''}`,
      },
      h('span', { class: `svc-ico bg-${c.color}` }, icon(iconFor(t), { size: 24 })),
      h('h3', { class: 'svc-title' }, t.nombre),
      h('p', { class: 'svc-desc' }, t.descripcion),
      h('div', { class: 'svc-foot' }, riskBadge(t.riesgo), t.revierte ? h('span', { class: 'pill pill-info' }, icon('undo', { size: 14 }), 'Se revierte solo') : null)))))));
  }
  q.input.addEventListener('input', paint);
  sel.input.addEventListener('change', paint);
  replaceChildren(body, h('div', { class: 'toolbar' }, sel.el, q.el), list);
  paint();
}
