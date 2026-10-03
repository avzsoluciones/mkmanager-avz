// Empresas: el primer nivel. Primero la empresa, luego sus sedes y por último sus equipos.
import { h, replaceChildren, toast, confirmDialog } from '../dom.js';
import { icon } from '../icons.js';
import { session } from '../session.js';
import { api } from '../api.js';
import { pageHeader, skeletonList, emptyState, errorState, field } from '../components.js';
import { listEmpresas, listSedes, listDevices, agruparEmpresas } from '../data.js';
import { openEmpresaDialog } from './empresa-dialog.js';
import { openSedeDialog } from './sede-dialog.js';
import { createAutoRefresh } from '../autorefresh.js';

const PILL = { online: ['ok', 'Todo en línea'], parcial: ['warn', 'Atención'], offline: ['bad', 'Sin conexión'], pendiente: ['warn', 'Pendiente'], vacia: ['muted', 'Sin equipos'] };

export default async function empresas(ctx) {
  const role = session.role();
  const canCreate = ['superadmin', 'soporte', 'partner'].includes(role);
  const canDelete = role === 'superadmin';
  const newBtn = canCreate ? h('button', { class: 'btn btn-primary', type: 'button', onClick: () => openEmpresaDialog(() => load()) }, icon('plus', { size: 18 }), 'Nueva empresa') : null;
  const body = h('div', { class: 'stack' });
  ctx.container.append(pageHeader({ title: 'Empresas', subtitle: 'Tus clientes. Cada empresa tiene sus sedes y cada sede sus equipos.', actions: newBtn ? [newBtn] : [] }), body);

  let rows = [];
  const search = field({ id: 'emp-q', label: 'Buscar empresa', type: 'search', placeholder: 'Nombre de la empresa', autocomplete: 'off' });
  const listEl = h('ul', { class: 'rows' });

  async function fetchAll() {
    const [e, s, d] = await Promise.all([listEmpresas(), listSedes().catch(() => []), listDevices().catch(() => [])]);
    rows = agruparEmpresas(e, s, d);
  }

  async function remove(g) {
    if (g.sedes.length) {
      toast(`«${g.nombre}» tiene ${g.sedes.length} ${g.sedes.length === 1 ? 'sede' : 'sedes'}. Elimina primero sus sedes.`, { type: 'error' });
      return;
    }
    const ok = await confirmDialog({
      title: `¿Eliminar la empresa «${g.nombre}»?`,
      message: 'La empresa se borra para siempre. Esta acción no se puede deshacer.',
      confirmLabel: 'Sí, eliminar empresa', danger: true,
    });
    if (!ok) return;
    try { await api.del(`/empresas/${encodeURIComponent(g.id)}`); toast(`Empresa «${g.nombre}» eliminada.`, { type: 'ok' }); await load(); } catch (e) { toast(e.message, { type: 'error' }); }
  }

  function paint() {
    const q = search.value.trim().toLowerCase();
    const shown = rows.filter((g) => !q || `${g.nombre} ${g.aliado}`.toLowerCase().includes(q));
    const multi = new Set(rows.map((g) => g.aliado)).size > 1;
    if (!shown.length) { replaceChildren(listEl, h('li', null, emptyState({ iconName: 'search', title: 'Ninguna empresa coincide', text: 'Prueba con otra palabra.' }))); return; }
    replaceChildren(listEl, shown.map((g) => {
      const [cls, base] = PILL[g.estado] || PILL.vacia;
      const txt = g.estado === 'vacia' && !g.sedes.length ? 'Sin sedes' : base;
      return h('li', { class: 'row-li' },
        h('a', { class: 'row-card', href: `#/empresas/${encodeURIComponent(g.id)}` },
          h('span', { class: 'row-ico' }, icon('layers', { size: 22 })),
          h('span', { class: 'row-main' }, h('strong', { class: 'row-title' }, g.nombre), h('span', { class: 'row-sub' }, icon('user', { size: 14 }), multi ? `Atendida por ${g.aliado}` : `${g.sedes.length} ${g.sedes.length === 1 ? 'sede' : 'sedes'}`)),
          h('span', { class: 'row-status' }, h('span', { class: `pill pill-${cls}` }, txt)),
          h('span', { class: 'row-meta' },
            h('span', null, h('small', null, 'Sedes'), String(g.sedes.length)),
            h('span', null, h('small', null, 'Equipos'), String(g.equipos.length)),
            h('span', null, h('small', null, 'En línea'), String(g.equipos.filter((e) => e.estado === 'online').length))),
          icon('chevron', { size: 18, cls: 'row-chev' })),
        canCreate ? h('button', { class: 'icon-btn', type: 'button', title: 'Nueva sede en esta empresa', 'aria-label': `Nueva sede en ${g.nombre}`, onClick: () => openSedeDialog(() => load(), { empresaId: g.id }) }, icon('plus', { size: 18 })) : null,
        canCreate ? h('button', { class: 'icon-btn', type: 'button', title: 'Renombrar', 'aria-label': `Renombrar ${g.nombre}`, onClick: () => openEmpresaDialog(() => load(), { empresa: g }) }, icon('sliders', { size: 18 })) : null,
        canDelete ? h('button', { class: 'icon-btn row-del', type: 'button', title: 'Eliminar empresa', 'aria-label': `Eliminar la empresa ${g.nombre}`, onClick: () => remove(g) }, icon('trash', { size: 18 })) : null);
    }));
  }

  const auto = createAutoRefresh({ ctx, def: 15, onTick: async () => { await fetchAll(); paint(); } });

  async function load() {
    replaceChildren(body, skeletonList(4));
    try { await fetchAll(); } catch (e) {
      if (!ctx.alive()) return;
      replaceChildren(body, errorState({ message: e.message, onRetry: load }));
      return;
    }
    if (!ctx.alive()) return;
    if (!rows.length) {
      replaceChildren(body, emptyState({
        iconName: 'layers', title: 'Aún no hay empresas',
        text: canCreate ? 'Empieza creando la primera empresa: el cliente al que pertenecerán las sedes y los equipos.' : 'Cuando tu aliado registre una empresa la verás aquí.',
        action: canCreate ? h('button', { class: 'btn btn-primary', type: 'button', onClick: () => openEmpresaDialog(() => load()) }, icon('plus', { size: 18 }), 'Crear la primera empresa') : null,
      }));
      return;
    }
    search.input.oninput = paint;
    replaceChildren(body, h('div', { class: 'list-top' }, auto.el), h('div', { class: 'toolbar' }, search.el), listEl);
    paint();
  }
  await load();
}
