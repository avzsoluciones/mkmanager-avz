// Detalle de una empresa: su mapa de sedes interconectadas y la lista de sedes con su estado.
import { h, replaceChildren } from '../dom.js';
import { icon } from '../icons.js';
import { session } from '../session.js';
import { pageHeader, skeletonList, errorState, emptyState } from '../components.js';
import { listDevices, listSedes, listEmpresas, agruparEmpresas } from '../data.js';
import { networkMap } from '../mapa-red.js';
import { openSedeDialog } from './sede-dialog.js';
import { createAutoRefresh } from '../autorefresh.js';

const ESTADO = { online: ['ok', 'En línea'], parcial: ['warn', 'Parcial'], offline: ['bad', 'Fuera de línea'], pendiente: ['warn', 'Pendiente'], vacia: ['muted', 'Sin equipos'] };

export default async function empresa(ctx) {
  const id = Number(ctx.params.id);
  const canCreate = ['superadmin', 'soporte', 'partner'].includes(session.role());
  const body = h('div', { class: 'stack-lg' });
  const back = { href: '#/panel', label: 'Volver al panel' };
  const auto = createAutoRefresh({ ctx, def: 15, onTick: () => load(false) });
  const head = h('div');
  ctx.container.append(head, body);
  replaceChildren(body, skeletonList(4));

  async function load(first = true) {
    let grupo;
    try {
      const [empresas, sedes, equipos] = await Promise.all([listEmpresas(), listSedes(), listDevices()]);
      grupo = agruparEmpresas(empresas, sedes, equipos).find((g) => g.id === id);
    } catch (e) {
      if (ctx.alive() && first) replaceChildren(body, errorState({ message: e.message, onRetry: () => load() }));
      return;
    }
    if (!ctx.alive()) return;
    if (!grupo) {
      replaceChildren(head, pageHeader({ title: 'Empresa', back }));
      replaceChildren(body, emptyState({ iconName: 'building', title: 'No encontramos esta empresa', text: 'Puede que ya no exista o que no tengas permiso para verla.', action: h('a', { class: 'btn btn-secondary', href: '#/empresas' }, 'Ver empresas') }));
      return;
    }
    ctx.setTitle(grupo.nombre);
    const newSede = canCreate ? h('button', { class: 'btn btn-primary', type: 'button', onClick: () => openSedeDialog(() => load(), { empresaId: grupo.id }) }, icon('plus', { size: 18 }), 'Nueva sede') : null;
    replaceChildren(head, pageHeader({ title: grupo.nombre, subtitle: `Empresa atendida por el aliado ${grupo.aliado}. Sus sedes y cómo están conectadas.`, back, actions: [auto.el, newSede] }));
    if (!grupo.sedes.length) {
      replaceChildren(body, emptyState({ iconName: 'building', title: 'Esta empresa aún no tiene sedes', text: 'Crea la primera sede; después podrás instalar sus routers.', action: newSede }));
      return;
    }
    replaceChildren(body,
      h('section', { class: 'card', 'aria-labelledby': 'h-mapa' },
        h('h2', { class: 'card-title', id: 'h-mapa' }, icon('globe', { size: 18 }), 'Mapa de la red'),
        h('p', { class: 'muted' }, 'La empresa arriba y sus sedes conectadas entre sí. Toca una sede o un equipo para abrirlo.'),
        networkMap({ empresa: grupo.nombre, sedes: grupo.sedes, equipos: grupo.equipos })),
      h('section', { class: 'stack', 'aria-labelledby': 'h-sedes' },
        h('h2', { class: 'card-title', id: 'h-sedes' }, icon('building', { size: 18 }), `Sedes (${grupo.sedes.length})`),
        h('ul', { class: 'rows' }, grupo.sedes.map((s) => {
          const [cls, txt] = ESTADO[s.estado] || ESTADO.vacia;
          return h('li', null, h('a', { class: 'row-card', href: `#/equipos?sede=${encodeURIComponent(s.id)}` },
            h('span', { class: 'row-ico' }, icon('building', { size: 22 })),
            h('span', { class: 'row-main' }, h('strong', { class: 'row-title' }, s.nombre), h('span', { class: 'row-sub' }, icon('pin', { size: 14 }), s.ciudad)),
            h('span', { class: 'row-status' }, h('span', { class: `pill pill-${cls}` }, txt)),
            h('span', { class: 'row-meta' },
              h('span', null, h('small', null, 'Equipos'), String(s.mine.length)),
              h('span', null, h('small', null, 'En línea'), String(s.mine.filter((e) => e.estado === 'online').length))),
            icon('chevron', { size: 18, cls: 'row-chev' })));
        }))));
  }
  await load();
}
