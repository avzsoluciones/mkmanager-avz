import { h, replaceChildren, relTime, fmtDate, toast, confirmDialog } from '../dom.js';
import { icon } from '../icons.js';
import { session } from '../session.js';
import { pageHeader, skeletonList, emptyState, errorState, field, statusPill } from '../components.js';
import { listDevices, bajaEquipo, borrarEquipo, puedeDarDeBaja } from '../data.js';
import { createListTools } from '../listtools.js';
import { createAutoRefresh } from '../autorefresh.js';

const CHIPS = [
  { key: '', label: 'Todos' },
  { key: 'online', label: 'En línea' },
  { key: 'pendiente', label: 'Pendientes' },
  { key: 'offline', label: 'Fuera de línea' },
];

export default async function equipos(ctx) {
  const canWrite = session.canWrite();
  const canDelete = puedeDarDeBaja(session.role());
  const state = { estado: ctx.query.get('estado') || '', sede: ctx.query.get('sede') || '' };
  const body = h('div', { class: 'stack' });
  ctx.container.append(pageHeader({
    title: 'Equipos', subtitle: 'Todos los routers que gestionas y si están conectados.',
    actions: canWrite ? [h('a', { class: 'btn btn-primary', href: '#/nuevo-router' }, icon('plus', { size: 18 }), 'Nuevo router')] : [],
  }), body);

  let rows = [];
  const search = field({ id: 'eq-q', label: 'Buscar', type: 'search', placeholder: 'Nombre, sede o IP', autocomplete: 'off' });
  const sedeSel = field({ id: 'eq-sede', label: 'Sede', type: 'select', options: [{ value: '', label: 'Todas las sedes' }] });
  const chipBox = h('div', { class: 'chips', role: 'group', 'aria-label': 'Filtrar por estado' });
  const count = h('p', { class: 'muted result-count', 'aria-live': 'polite' });
  const listEl = h('div');

  const visible = () => {
    const q = search.value.trim().toLowerCase();
    return rows.filter((r) =>
      (!state.estado || r.estado === state.estado)
      && (!state.sede || String(r.sede_id) === state.sede)
      && (!q || `${r.nombre} ${r.sede} ${r.tunnel_ip} ${r.lan_cidr}`.toLowerCase().includes(q)));
  };

  const tools = createListTools({
    key: 'avz_vista_equipos', canSelect: canDelete, deleteLabel: 'Dar de baja',
    noun: { one: 'equipo', many: 'equipos', sel: ['seleccionado', 'seleccionados'] },
    getVisibleIds: () => visible().map((r) => r.id),
    onDelete: (ids) => removeMany(ids), onChange: () => paint(),
  });

  /** Da de baja uno o varios equipos tras una sola confirmación. */
  async function removeMany(ids) {
    const chosen = rows.filter((r) => ids.includes(String(r.id)));
    if (!chosen.length) return;
    const names = chosen.slice(0, 5).map((r) => `«${r.nombre}»`).join(', ') + (chosen.length > 5 ? ` y ${chosen.length - 5} más` : '');
    const ok = await confirmDialog({
      title: chosen.length === 1 ? `¿Dar de baja «${chosen[0].nombre}»?` : `¿Dar de baja ${chosen.length} equipos?`,
      message: `${chosen.length === 1 ? '' : `Se darán de baja ${names}. `}Se eliminan los equipos y su conexión al túnel seguro AVZ, y dejarán de poder administrarse desde aquí. Los routers físicos conservan su configuración. Esta acción no se puede deshacer.`,
      confirmLabel: chosen.length === 1 ? 'Sí, dar de baja' : `Sí, dar de baja ${chosen.length}`, danger: true,
    });
    if (!ok) return;
    let done = 0; const failed = [];
    for (const r of chosen) {
      try { await borrarEquipo(r.id); done++; rows = rows.filter((x) => x.id !== r.id); } catch (e) { failed.push(`«${r.nombre}»: ${e.message}`); }
    }
    if (done) toast(done === 1 ? 'Equipo dado de baja.' : `${done} equipos dados de baja.`, { type: 'ok' });
    if (failed.length) toast(failed.join(' · '), { type: 'error' });
    tools.prune(rows.map((r) => r.id));
    paintChips(); paint();
  }

  const auto = createAutoRefresh({
    ctx, def: 15,
    onTick: async () => {
      rows = await listDevices();
      tools.prune(rows.map((r) => r.id));
      paintChips(); paint();
    },
  });

  function paintChips() {
    replaceChildren(chipBox, CHIPS.map((c) => {
      const n = c.key ? rows.filter((r) => r.estado === c.key).length : rows.length;
      return h('button', {
        type: 'button', class: `chip${state.estado === c.key ? ' is-on' : ''}`, 'aria-pressed': String(state.estado === c.key),
        onClick: () => { state.estado = c.key; paintChips(); paint(); },
      }, c.label, h('span', { class: 'chip-n' }, String(n)));
    }));
  }

  const delIcon = (r) => (canDelete ? h('button', {
    class: 'icon-btn sede-del row-del', type: 'button', title: 'Dar de baja', 'aria-label': `Dar de baja ${r.nombre}`,
    onClick: async () => { if (await bajaEquipo(r)) { rows = rows.filter((x) => x.id !== r.id); tools.prune(rows.map((x) => x.id)); paintChips(); paint(); } },
  }, icon('trash', { size: 18 })) : null);

  function row(r) {
    return h('li', { class: 'row-li' }, tools.checkbox(r.id, r.nombre),
      h('a', { class: 'row-card', href: `#/equipos/${r.id}` },
        h('span', { class: `row-ico st-${r.estado}` }, icon('router', { size: 22 })),
        h('span', { class: 'row-main' },
          h('strong', { class: 'row-title' }, r.nombre),
          h('span', { class: 'row-sub' }, icon('building', { size: 14 }), r.sede || 'Sin sede')),
        h('span', { class: 'row-status' }, statusPill(r.estado)),
        h('span', { class: 'row-meta' },
          h('span', null, h('small', null, 'IP de túnel'), r.tunnel_ip || '—'),
          h('span', null, h('small', null, 'Red local'), r.lan_cidr || '—'),
          h('span', null, h('small', null, 'Último contacto'), h('time', { datetime: r.ultimo_heartbeat || '', title: fmtDate(r.ultimo_heartbeat) }, relTime(r.ultimo_heartbeat)))),
        icon('chevron', { size: 18, cls: 'row-chev' })),
      canDelete ? delIcon(r) : null);
  }

  function card(r) {
    return h('div', { class: 'sede-wrap' },
      h('a', { class: 'card card-link sede-card', href: `#/equipos/${r.id}` },
        h('div', { class: 'sede-head' },
          h('span', { class: `row-ico st-${r.estado}` }, icon('router', { size: 22 })),
          h('div', { class: 'sede-title' }, h('h2', null, r.nombre), h('p', { class: 'muted' }, icon('building', { size: 14 }), r.sede || 'Sin sede'))),
        h('div', null, statusPill(r.estado)),
        h('div', { class: 'row-meta card-meta' },
          h('span', null, h('small', null, 'IP de túnel'), r.tunnel_ip || '—'),
          h('span', null, h('small', null, 'Red local'), r.lan_cidr || '—'),
          h('span', null, h('small', null, 'Último contacto'), h('time', { datetime: r.ultimo_heartbeat || '', title: fmtDate(r.ultimo_heartbeat) }, relTime(r.ultimo_heartbeat)))),
        h('div', { class: 'sede-foot' }, h('span'), h('span', { class: 'link-more' }, 'Ver equipo', icon('chevron', { size: 16 })))),
      tools.checkbox(r.id, r.nombre), delIcon(r));
  }

  function paint() {
    const shown = visible();
    count.textContent = `Mostrando ${shown.length} de ${rows.length}`;
    if (!shown.length) {
      replaceChildren(listEl, emptyState({
        iconName: 'search', title: 'Ningún equipo coincide',
        text: 'Cambia los filtros o la búsqueda para ver más resultados.',
        action: h('button', { class: 'btn btn-secondary', type: 'button', onClick: () => { state.estado = ''; state.sede = ''; search.value = ''; sedeSel.value = ''; paintChips(); paint(); } }, 'Quitar filtros'),
      }));
      return;
    }
    replaceChildren(listEl, tools.mode === 'list'
      ? h('ul', { class: 'rows' }, shown.map(row))
      : h('div', { class: 'cards-grid' }, shown.map(card)));
  }

  async function load() {
    replaceChildren(body, skeletonList(5));
    try { rows = await listDevices(); } catch (e) {
      if (!ctx.alive()) return;
      replaceChildren(body, errorState({ message: e.message, onRetry: load }));
      return;
    }
    if (!ctx.alive()) return;
    if (!rows.length) {
      replaceChildren(body, emptyState({
        iconName: 'router', title: 'Aún no hay equipos',
        text: canWrite ? 'Cuando crees tu primer router aparecerá aquí con su estado de conexión.' : 'Cuando haya equipos en tu sede los verás aquí.',
        action: canWrite ? h('a', { class: 'btn btn-primary', href: '#/nuevo-router' }, icon('plus', { size: 18 }), 'Crear mi primer router') : null,
      }));
      return;
    }
    const sedes = [...new Map(rows.map((r) => [String(r.sede_id), r.sede])).entries()];
    sedeSel.input.append(...sedes.map(([id, name]) => h('option', { value: id }, name)));
    sedeSel.value = state.sede;
    search.input.addEventListener('input', paint);
    sedeSel.input.addEventListener('change', () => { state.sede = sedeSel.value; paint(); });
    replaceChildren(body, h('div', { class: 'list-top' }, auto.el, tools.toggle), h('div', { class: 'toolbar' }, search.el, sedeSel.el), chipBox, count, tools.bar, listEl);
    paintChips();
    paint();
  }
  await load();
}
