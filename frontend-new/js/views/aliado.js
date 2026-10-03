// Cartera de un aliado (vista de AVZ Cloud): sus empresas y el mapa de sus sedes.
import { h, replaceChildren } from '../dom.js';
import { pageHeader, skeletonList, errorState, emptyState } from '../components.js';
import { listDevices, listSedes, listEmpresas, agruparEmpresas, agruparAliados } from '../data.js';
import { carteraBlock } from './cartera.js';
import { createAutoRefresh } from '../autorefresh.js';

export default async function aliado(ctx) {
  const id = String(ctx.params.id);
  const body = h('div', { class: 'stack-lg' });
  const head = h('div');
  const auto = createAutoRefresh({ ctx, def: 15, onTick: () => load(false) });
  ctx.container.append(head, body);
  replaceChildren(body, skeletonList(4));

  async function load(first = true) {
    let a;
    try {
      const [empresas, sedes, equipos] = await Promise.all([listEmpresas(), listSedes(), listDevices()]);
      a = agruparAliados(agruparEmpresas(empresas, sedes, equipos)).find((x) => x.id === id);
    } catch (e) {
      if (ctx.alive() && first) replaceChildren(body, errorState({ message: e.message, onRetry: () => load() }));
      return;
    }
    if (!ctx.alive()) return;
    const back = { href: '#/panel', label: 'Volver al panel' };
    if (!a) {
      replaceChildren(head, pageHeader({ title: 'Aliado', back }));
      replaceChildren(body, emptyState({ iconName: 'user', title: 'No encontramos este aliado', text: 'Puede que ya no tenga empresas o que no tengas permiso para verlo.', action: h('a', { class: 'btn btn-secondary', href: '#/panel' }, 'Volver al panel') }));
      return;
    }
    ctx.setTitle(a.nombre);
    replaceChildren(head, pageHeader({ title: a.nombre, subtitle: 'Aliado. Todas las empresas que atiende.', back, actions: [auto.el] }));
    replaceChildren(body, carteraBlock(a));
  }
  await load();
}
