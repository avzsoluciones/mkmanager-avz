// Vista NOC: pantalla completa de solo lectura para proyectar en un televisor.
// Arriba (75 %) el mapa; abajo (25 %) indicadores, empresas y lo que necesita atención. Todo a la vez, sin scroll.
import { h, replaceChildren, relTime, logo } from '../dom.js';
import { icon } from '../icons.js';
import { listDevices, listSedes, listEmpresas, agruparEmpresas, agruparAliados } from '../data.js';
import { createAutoRefresh } from '../autorefresh.js';
import { networkMap, carteraMap, aliadosMap } from '../mapa-red.js';

const ESTADO_TXT = { online: 'En línea', parcial: 'Atención', offline: 'Sin conexión', pendiente: 'Pendiente', vacia: 'Sin equipos' };
const TONE = { online: 'ok', parcial: 'warn', pendiente: 'warn', offline: 'bad', vacia: 'muted' };
const MAX_FILAS = 3;

export default async function noc(ctx) {
  // Tema oscuro mientras dure la vista; se restaura al salir.
  const root = document.documentElement;
  const prevTheme = root.getAttribute('data-theme');
  root.setAttribute('data-theme', 'dark');
  document.body.classList.add('noc-body');
  let lock = null;
  try { if (navigator.wakeLock) lock = await navigator.wakeLock.request('screen'); } catch { /* el navegador no lo permite */ }
  ctx.onCleanup(() => {
    if (prevTheme) root.setAttribute('data-theme', prevTheme); else root.removeAttribute('data-theme');
    document.body.classList.remove('noc-body', 'noc-idle');
    if (lock) lock.release().catch(() => {});
  });

  const clock = h('time', { class: 'noc-clock' });
  const stamp = h('span', { class: 'noc-stamp' }, 'Cargando…');
  const banner = h('div', { class: 'noc-banner noc-ok', role: 'status', 'aria-live': 'polite' }, 'Cargando…');
  const mapa = h('section', { class: 'noc-map', 'aria-label': 'Mapa de la red' });
  const kpis = h('div', { class: 'noc-kpis' });
  const empresasEl = h('div', { class: 'noc-panel' });
  const attn = h('div', { class: 'noc-panel' });
  const conn = h('div', { class: 'noc-conn', hidden: true, role: 'alert' }, icon('alert', { size: 20 }), 'Sin conexión con el servidor. Se muestran los últimos datos.');

  const fsBtn = h('button', { class: 'noc-btn', type: 'button', title: 'Pantalla completa', 'aria-label': 'Pantalla completa' }, icon('expand', { size: 20 }));
  fsBtn.addEventListener('click', () => {
    if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen().catch(() => {});
  });
  const exit = h('a', { class: 'noc-btn', href: '#/panel', title: 'Salir de la vista NOC', 'aria-label': 'Salir de la vista NOC' }, icon('x', { size: 20 }));
  const head = h('div', { class: 'noc-head-r' }, stamp, clock, fsBtn, exit);

  ctx.container.append(h('div', { class: 'noc' },
    h('header', { class: 'noc-head' }, logo({ cls: 'noc-logo' }), h('h1', { class: 'noc-title', id: 'page-title' }, 'Centro de operaciones'), banner, head),
    conn, mapa,
    h('div', { class: 'noc-stats' }, h('section', { class: 'noc-panel', 'aria-label': 'Resumen' }, h('h2', { class: 'noc-panel-t' }, icon('activity', { size: 18 }), 'Resumen'), kpis), empresasEl, attn)));

  function tickClock() { const d = new Date(); clock.textContent = d.toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit', second: '2-digit' }); clock.dateTime = d.toISOString(); }
  tickClock();
  const clockTimer = setInterval(tickClock, 1000);
  ctx.onCleanup(() => clearInterval(clockTimer));

  // Oculta el cursor tras unos segundos sin moverlo.
  let idle = null;
  const wake = () => { document.body.classList.remove('noc-idle'); clearTimeout(idle); idle = setTimeout(() => document.body.classList.add('noc-idle'), 6000); };
  window.addEventListener('mousemove', wake);
  wake();
  ctx.onCleanup(() => { window.removeEventListener('mousemove', wake); clearTimeout(idle); });

  const kpi = (value, label, tone) => h('div', { class: `noc-kpi noc-${tone}` }, h('span', { class: 'noc-kpi-v' }, String(value)), h('span', { class: 'noc-kpi-l' }, label));
  const plural = (n, a, b) => `${n} ${n === 1 ? a : b}`;

  /** Mapa según lo que ve el usuario: varios aliados → aliados; un aliado con varias empresas → su cartera; una empresa → sus sedes. */
  function buildMapa(grupos) {
    const aliados = agruparAliados(grupos);
    const opts = { noc: false, herramientas: false };
    if (aliados.length > 1) return aliadosMap({ raiz: 'AVZ Cloud', aliados, ...opts });
    if (aliados.length === 1 && aliados[0].empresas.length > 1) return carteraMap({ aliado: aliados[0].nombre, empresas: aliados[0].empresas, ...opts });
    if (aliados.length === 1) return networkMap({ empresa: aliados[0].empresas[0].nombre, sedes: aliados[0].empresas[0].sedes, equipos: aliados[0].empresas[0].equipos, ...opts });
    return h('p', { class: 'noc-none' }, icon('alert', { size: 24 }), 'Todavía no hay empresas');
  }

  let lastOk = 0;
  function paint(empresas, sedes, equipos) {
    const grupos = agruparEmpresas(empresas, sedes, equipos);
    const variosAliados = new Set(grupos.map((g) => g.aliado)).size > 1;
    const on = equipos.filter((e) => e.estado === 'online').length;
    const off = equipos.filter((e) => e.estado === 'offline').length;
    const pend = equipos.filter((e) => e.estado === 'pendiente').length;
    const sedesOn = grupos.reduce((n, g) => n + g.online, 0);
    const sedesTotal = grupos.reduce((n, g) => n + g.sedes.length, 0);

    banner.className = `noc-banner ${off ? 'noc-bad' : pend ? 'noc-warnbg' : 'noc-ok'}`;
    banner.textContent = off ? `${plural(off, 'equipo sin conexión', 'equipos sin conexión')}` : pend ? `Todo operando · ${plural(pend, 'equipo pendiente', 'equipos pendientes')} de conectar` : 'Todo operando con normalidad';

    replaceChildren(mapa, buildMapa(grupos));

    replaceChildren(kpis,
      kpi(`${sedesOn}/${sedesTotal}`, 'Sedes en línea', sedesOn === sedesTotal ? 'ok' : 'warn'),
      kpi(`${on}/${equipos.length}`, 'Equipos en línea', on === equipos.length ? 'ok' : 'warn'),
      kpi(off, 'Fuera de línea', off ? 'bad' : 'ok'),
      kpi(pend, 'Pendientes', pend ? 'warn' : 'ok'));

    replaceChildren(empresasEl, h('h2', { class: 'noc-panel-t' }, icon('layers', { size: 18 }), 'Empresas atendidas'),
      grupos.length
        ? h('ul', { class: 'noc-list' }, grupos.slice(0, MAX_FILAS).map((g) => h('li', { class: `noc-row noc-b-${TONE[g.estado]}` },
          h('strong', null, g.nombre, variosAliados ? h('small', null, `vía ${g.aliado}`) : null),
          h('span', null, `${g.online}/${g.sedes.length} ${g.sedes.length === 1 ? 'sede' : 'sedes'}`),
          h('span', { class: `noc-pill noc-${TONE[g.estado]}` }, g.estado === 'vacia' && !g.sedes.length ? 'Sin sedes' : ESTADO_TXT[g.estado]))))
        : h('p', { class: 'noc-none' }, 'Sin empresas'),
      grupos.length > MAX_FILAS ? h('p', { class: 'noc-more' }, `y ${grupos.length - MAX_FILAS} más`) : null);

    const lista = equipos.filter((e) => e.estado !== 'online')
      .sort((a, b) => (a.estado === b.estado ? 0 : a.estado === 'offline' ? -1 : 1));
    replaceChildren(attn, h('h2', { class: 'noc-panel-t' }, icon('alert', { size: 18 }), 'Atención ahora'),
      lista.length
        ? h('ul', { class: 'noc-list' }, lista.slice(0, MAX_FILAS).map((e) => h('li', { class: `noc-row noc-b-${TONE[e.estado]}` },
          h('strong', null, e.nombre, h('small', null, e.sede || '')),
          h('span', null, e.estado === 'pendiente' ? 'pendiente de conectar' : e.ultimo_heartbeat ? `sin respuesta ${relTime(e.ultimo_heartbeat)}` : 'sin contacto'))))
        : h('p', { class: 'noc-none' }, icon('check', { size: 26 }), 'Sin incidencias'),
      lista.length > MAX_FILAS ? h('p', { class: 'noc-more' }, `y ${lista.length - MAX_FILAS} más`) : null);
  }

  async function refresh() {
    try {
      const [empresas, sedes, equipos] = await Promise.all([listEmpresas(), listSedes(), listDevices()]);
      if (!ctx.alive()) return;
      lastOk = Date.now();
      conn.hidden = true;
      paint(empresas, sedes, equipos);
    } catch {
      if (ctx.alive()) conn.hidden = false;
    }
    if (ctx.alive()) stamp.textContent = lastOk ? `Actualizado ${new Date(lastOk).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}` : 'Sin datos';
  }
  const auto = createAutoRefresh({ ctx, def: 10, onTick: refresh });
  head.prepend(auto.el);
  await refresh();
}
