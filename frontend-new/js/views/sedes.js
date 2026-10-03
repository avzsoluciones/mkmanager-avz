import { h, replaceChildren, toast, confirmDialog } from '../dom.js';
import { icon } from '../icons.js';
import { session } from '../session.js';
import { api } from '../api.js';
import { pageHeader, skeletonList, emptyState, errorState, field } from '../components.js';
import { listSedes } from '../data.js';
import { openSedeDialog } from './sede-dialog.js';
import { createListTools } from '../listtools.js';
import { createAutoRefresh } from '../autorefresh.js';

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

export default async function sedes(ctx) {
  const canWrite = session.canWrite();
  const canDelete = session.isSuper();
  const canEdit = ['superadmin', 'soporte', 'partner'].includes(session.role());
  const newBtn = canWrite ? h('button', { class: 'btn btn-primary', type: 'button', onClick: () => openSedeDialog(() => load()) }, icon('plus', { size: 18 }), 'Nueva sede') : null;
  const body = h('div', { class: 'stack' });
  ctx.container.append(pageHeader({ title: 'Sedes', subtitle: 'Los lugares donde están instalados los equipos de tus clientes.', actions: newBtn ? [newBtn] : [] }), body);

  let rows = [];
  const search = field({ id: 'sede-q', label: 'Buscar sede', type: 'search', placeholder: 'Nombre, ciudad o empresa', autocomplete: 'off' });
  const listEl = h('div');

  const visible = () => {
    const q = search.value.trim().toLowerCase();
    return rows.filter((s) => !q || `${s.nombre} ${s.ciudad} ${s.empresa || ''}`.toLowerCase().includes(q));
  };

  const tools = createListTools({
    key: 'avz_vista_sedes', canSelect: canDelete, deleteLabel: 'Eliminar sedes',
    noun: { one: 'sede', many: 'sedes', sel: ['seleccionada', 'seleccionadas'] },
    getVisibleIds: () => visible().map((s) => s.id),
    onDelete: (ids) => removeMany(ids), onChange: () => paint(),
  });

  const auto = createAutoRefresh({
    ctx, def: 15,
    onTick: async () => { rows = await listSedes(); tools.prune(rows.map((s) => s.id)); paint(); },
  });

  /** Borra una o varias sedes. Las que aún tienen equipos se omiten y se avisa. */
  async function removeMany(ids) {
    const chosen = rows.filter((s) => ids.includes(String(s.id)));
    const blocked = chosen.filter((s) => s.dispositivos);
    const free = chosen.filter((s) => !s.dispositivos);
    if (!free.length) {
      const go = await confirmDialog({
        title: chosen.length === 1 ? `No se puede eliminar «${chosen[0].nombre}» todavía` : 'No se pueden eliminar todavía',
        message: chosen.length === 1
          ? `Esta sede tiene ${plural(chosen[0].dispositivos, 'equipo asociado', 'equipos asociados')}. Primero da de baja ${chosen[0].dispositivos === 1 ? 'ese equipo' : 'esos equipos'}; cuando la sede quede vacía podrás eliminarla.`
          : `Las ${chosen.length} sedes elegidas tienen equipos asociados. Primero da de baja esos equipos; cuando queden vacías podrás eliminarlas.`,
        confirmLabel: 'Ir a los equipos', cancelLabel: 'Cerrar',
      });
      if (go) ctx.navigate(chosen.length === 1 ? `/equipos?sede=${encodeURIComponent(chosen[0].id)}` : '/equipos');
      return;
    }
    const names = free.slice(0, 5).map((s) => `«${s.nombre}»`).join(', ') + (free.length > 5 ? ` y ${free.length - 5} más` : '');
    const ok = await confirmDialog({
      title: free.length === 1 ? `¿Eliminar la sede «${free[0].nombre}»?` : `¿Eliminar ${free.length} sedes?`,
      message: `${free.length === 1 ? '' : `Se borrarán ${names}. `}${blocked.length ? `Se omitirán ${plural(blocked.length, 'sede que aún tiene', 'sedes que aún tienen')} equipos. ` : ''}Se borra para siempre. Si hay accesos VPN activos, primero tendrás que revocarlos. Esta acción no se puede deshacer.`,
      confirmLabel: free.length === 1 ? 'Sí, eliminar sede' : `Sí, eliminar ${free.length} sedes`, danger: true,
    });
    if (!ok) return;
    let done = 0; const failed = [];
    for (const s of free) {
      try { await api.del(`/sedes/${encodeURIComponent(s.id)}`); done++; } catch (e) { failed.push(`«${s.nombre}»: ${e.message}`); }
    }
    if (done) toast(done === 1 ? 'Sede eliminada.' : `${done} sedes eliminadas.`, { type: 'ok' });
    if (failed.length) toast(`${failed.join(' · ')} Revoca esos accesos en «VPN para personas».`, { type: 'error' });
    if (blocked.length) toast(`${plural(blocked.length, 'sede se omitió', 'sedes se omitieron')} porque tiene equipos.`, { type: 'info' });
    tools.clear();
    await load();
  }

  const delIcon = (s) => (canDelete ? h('button', { class: 'icon-btn sede-del', type: 'button', title: 'Eliminar sede', 'aria-label': `Eliminar la sede ${s.nombre}`, onClick: () => removeMany([String(s.id)]) }, icon('trash', { size: 18 })) : null);

  /** Tras guardar y recargar la lista, el foco vuelve al botón de editar de la sede guardada. */
  const afterEdit = async (saved) => {
    await load();
    const id = String(saved?.id ?? '');
    const btn = id ? Array.from(document.querySelectorAll('button[data-sede]')).find((b) => b.dataset.sede === id) : null;
    if (btn) btn.focus();
  };

  const editIcon = (s) => (canEdit ? h('button', { class: 'icon-btn sede-edit', type: 'button', title: 'Editar sede', 'aria-label': `Editar la sede ${s.nombre}`, 'data-sede': String(s.id), onClick: () => openSedeDialog(afterEdit, { sede: s }) }, icon('pencil', { size: 18 })) : null);

  function card(s) {
    const ctl = [tools.checkbox(s.id, s.nombre), editIcon(s), delIcon(s)].filter(Boolean);
    const wrap = h('div', { class: 'sede-wrap' },
      h('a', { class: 'card card-link sede-card', href: `#/equipos?sede=${encodeURIComponent(s.id)}` },
        h('div', { class: 'sede-head' },
          h('span', { class: 'stat-ico tone-red' }, icon('building', { size: 22 })),
          h('div', { class: 'sede-title' }, h('h2', null, s.nombre), h('p', { class: 'muted' }, icon('pin', { size: 14 }), s.ciudad))),
        s.empresa ? h('p', { class: 'sede-line' }, icon('building', { size: 14 }), s.empresa) : null,
        s.direccion ? h('p', { class: 'sede-line' }, s.direccion) : null,
        s.contacto_email ? h('p', { class: 'sede-line muted' }, icon('user', { size: 14 }), s.contacto_email) : null,
        h('div', { class: 'sede-foot' },
          h('span', { class: 'pill pill-muted' }, icon('router', { size: 14 }), plural(s.dispositivos ?? 0, 'equipo', 'equipos')),
          h('span', { class: 'link-more' }, 'Ver equipos', icon('chevron', { size: 16 })))),
      ctl.length ? h('div', { class: 'sede-actions' }, ctl) : null);
    wrap.style.setProperty('--n', String(ctl.length));
    return wrap;
  }

  function row(s) {
    return h('li', { class: 'row-li' }, tools.checkbox(s.id, s.nombre),
      h('a', { class: 'row-card', href: `#/equipos?sede=${encodeURIComponent(s.id)}` },
        h('span', { class: 'row-ico' }, icon('building', { size: 22 })),
        h('span', { class: 'row-main' }, h('strong', { class: 'row-title' }, s.nombre), h('span', { class: 'row-sub' }, icon('pin', { size: 14 }), s.empresa ? `${s.ciudad} · ${s.empresa}` : s.ciudad)),
        h('span', { class: 'row-status' }, h('span', { class: 'pill pill-muted' }, icon('router', { size: 14 }), plural(s.dispositivos ?? 0, 'equipo', 'equipos'))),
        h('span', { class: 'row-meta' },
          h('span', null, h('small', null, 'Dirección'), s.direccion || '—'),
          h('span', null, h('small', null, 'Contacto'), s.contacto_email || '—')),
        icon('chevron', { size: 18, cls: 'row-chev' })),
      editIcon(s), delIcon(s));
  }

  function paint() {
    const shown = visible();
    if (!shown.length) {
      replaceChildren(listEl, emptyState({ iconName: 'search', title: 'Ninguna sede coincide', text: 'Prueba con otra palabra o borra la búsqueda.' }));
      return;
    }
    replaceChildren(listEl, tools.mode === 'list'
      ? h('ul', { class: 'rows' }, shown.map(row))
      : h('div', { class: 'cards-grid' }, shown.map(card)));
  }

  async function load() {
    replaceChildren(body, skeletonList(4));
    try { rows = await listSedes(); } catch (e) {
      if (!ctx.alive()) return;
      replaceChildren(body, errorState({ message: e.message, onRetry: load }));
      return;
    }
    if (!ctx.alive()) return;
    if (!rows.length) {
      replaceChildren(body, emptyState({
        iconName: 'building', title: 'Aún no hay sedes',
        text: canWrite ? 'Empieza creando la primera sede. Después podrás instalar un router en ella.' : 'Cuando tu aliado registre una sede la verás aquí.',
        action: canWrite ? h('button', { class: 'btn btn-primary', type: 'button', onClick: () => openSedeDialog(() => load()) }, icon('plus', { size: 18 }), 'Crear la primera sede') : null,
      }));
      return;
    }
    tools.prune(rows.map((s) => s.id));
    search.input.oninput = paint;
    replaceChildren(body, h('div', { class: 'list-top' }, auto.el, tools.toggle), h('div', { class: 'toolbar' }, search.el), tools.bar, listEl);
    paint();
  }
  await load();
}
