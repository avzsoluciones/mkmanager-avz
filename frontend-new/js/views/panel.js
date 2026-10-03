import { h, relTime, fmtDate, replaceChildren } from '../dom.js';
import { icon } from '../icons.js';
import { api, optional } from '../api.js';
import { session } from '../session.js';
import { pageHeader, skeletonCards, skeletonList, emptyState, errorState } from '../components.js';
import { listDevices, listSedes, listEmpresas, normEstado, agruparEmpresas, agruparAliados } from '../data.js';
import { empresaCards, aliadoCards } from './cartera.js';
import { createAutoRefresh } from '../autorefresh.js';

async function loadDashboard() {
  const d = await optional(() => api.get('/dashboard'));
  if (d) return d;
  // Respaldo mientras /dashboard no exista: se calcula con lo que ya hay.
  const [sedes, equipos] = await Promise.all([listSedes(), listDevices()]);
  return {
    sedes: sedes.length,
    equipos: equipos.length,
    online: equipos.filter((e) => e.estado === 'online').length,
    offline: equipos.filter((e) => e.estado === 'offline').length,
    pendientes: equipos.filter((e) => e.estado === 'pendiente').length,
    ips_libres: null,
    ips_total: null,
    actividad: [],
    degraded: true,
  };
}

function stat({ ic, tone, value, label, href, note }) {
  const body = [
    h('span', { class: `stat-ico tone-${tone}` }, icon(ic, { size: 22 })),
    h('span', { class: 'stat-text' },
      h('span', { class: 'stat-value' }, value == null ? '—' : String(value)),
      h('span', { class: 'stat-label' }, label),
      note ? h('span', { class: 'stat-note' }, note) : null),
  ];
  return href ? h('a', { class: 'stat card card-link', href }, body) : h('div', { class: 'stat card' }, body);
}

function quick({ href, ic, title, text, tone }) {
  return h('a', { class: 'qa', href, title: text },
    h('span', { class: `qa-ico tone-${tone}` }, icon(ic, { size: 18 })),
    h('span', null, title));
}

export default async function panel(ctx) {
  const user = ctx.user || {};
  const first = String(user.full_name || user.email || '').split(' ')[0].split('@')[0];
  const body = h('div', { class: 'stack-lg' });
  const auto = createAutoRefresh({ ctx, def: 15, onTick: () => paintAll() });
  ctx.container.append(
    pageHeader({ title: first ? `Hola, ${first}` : 'Panel', subtitle: 'Así está tu red hoy.', actions: [auto.el] }),
    body);

  const quicks = h('section', { class: 'qa-bar', 'aria-label': 'Acciones rápidas' }, h('span', { class: 'qa-label' }, 'Acciones rápidas'));
  if (session.canWrite()) {
    quicks.append(
      quick({ href: '#/nuevo-router', ic: 'router', tone: 'red', title: 'Nuevo router', text: 'Prepara y conecta un equipo nuevo paso a paso.' }),
      quick({ href: '#/vpn', ic: 'key', tone: 'acc', title: 'Nueva VPN', text: 'Da acceso seguro a alguien, con código QR.' }),
      quick({ href: '#/servicios', ic: 'sliders', tone: 'wifi', title: 'Aplicar un servicio', text: 'Firewall, puertos, Wi-Fi, límites de velocidad…' }));
  } else {
    quicks.append(quick({ href: '#/equipos', ic: 'router', tone: 'red', title: 'Ver mis equipos', text: 'Consulta el estado de los routers de tu sede.' }));
  }

  replaceChildren(body, skeletonCards(5), skeletonList(3));

  /** Pide los datos y pinta el panel. En refrescos silenciosos conserva lo anterior si algo falla. */
  async function paintAll() {
    let d; let empresas = []; let sedes = []; let equipos = [];
    try {
      [d, empresas, sedes, equipos] = await Promise.all([
        loadDashboard(), listEmpresas().catch(() => []), listSedes().catch(() => []), listDevices().catch(() => []),
      ]);
    } catch (e) {
      if (!ctx.alive()) return;
      if (!body.querySelector('.grid-stats')) replaceChildren(body, errorState({ message: e.message, onRetry: () => ctx.navigate('/panel') }));
      return;
    }
    if (!ctx.alive()) return;
    paint(d, agruparAliados(agruparEmpresas(empresas, sedes, equipos)));
  }

  function paint(d, aliados) {
    const total = d.equipos || 0;
    const pct = total ? Math.round(((d.online || 0) / total) * 100) : 0;
    const stats = h('section', { class: 'grid-stats', 'aria-label': 'Resumen' },
      stat({ ic: 'building', tone: 'red', value: d.sedes, label: d.sedes === 1 ? 'Sede' : 'Sedes', href: '#/sedes' }),
      stat({ ic: 'router', tone: 'wifi', value: d.equipos, label: d.equipos === 1 ? 'Equipo' : 'Equipos', href: '#/equipos' }),
      stat({ ic: 'check', tone: 'ok', value: d.online, label: 'En línea', href: '#/equipos?estado=online' }),
      stat({ ic: 'ban', tone: 'bad', value: d.offline, label: 'Fuera de línea', href: '#/equipos?estado=offline', note: d.pendientes ? `${d.pendientes} pendiente${d.pendientes === 1 ? '' : 's'} de conexión` : null }),
      stat({ ic: 'link', tone: 'acc', value: d.ips_libres, label: 'IPs de túnel libres', note: d.ips_libres != null ? (d.ips_total != null ? `de ${d.ips_total} disponibles` : 'Del total disponible') : 'Dato no disponible aún' }));

    const health = total ? h('section', { class: 'card health', 'aria-label': 'Salud de la red' },
      h('div', { class: 'health-row' },
        h('strong', null, `${pct}% de tus equipos está en línea`),
        h('span', { class: 'muted' }, `${d.online || 0} de ${total}`)),
      h('div', { class: 'meter', role: 'img', 'aria-label': `${pct}% en línea` }, (() => { const f = h('span', { class: 'meter-fill' }); f.style.setProperty('--w', pct + '%'); return f; })())) : null;

    const act = d.actividad || [];
    const list = act.length
      ? h('ul', { class: 'timeline' }, act.map((a) => h('li', { class: 'timeline-item' },
        h('span', { class: 'timeline-dot', 'aria-hidden': 'true' }),
        h('div', { class: 'timeline-body' },
          h('p', { class: 'timeline-title' }, humanAccion(a.accion), a.detalle ? h('span', { class: 'muted' }, ` · ${a.detalle}`) : null),
          h('p', { class: 'timeline-meta' }, `${a.usuario || 'Sistema'} · `, h('time', { datetime: a.fecha, title: fmtDate(a.fecha) }, relTime(a.fecha)))))))
      : emptyState({ iconName: 'activity', title: 'Todavía no hay actividad', text: d.degraded ? 'La lista de actividad aparecerá aquí cuando el servidor la tenga disponible.' : 'Cuando crees routers o apliques servicios, los verás aquí.' });

    // Primer vistazo: tarjetas. Un aliado ve sus empresas; AVZ Cloud ve a todos los aliados.
    // El gráfico (enjambre) es lo primero que se ve al entrar a una tarjeta.
    let vista = null;
    if (aliados.length === 1) {
      vista = h('section', { 'aria-labelledby': 'h-vista' },
        h('h2', { class: 'card-title', id: 'h-vista' }, icon('layers', { size: 18 }), aliados[0].empresas.length === 1 ? 'Empresa que atiendes' : 'Empresas que atiendes'),
        empresaCards(aliados[0].empresas));
    } else if (aliados.length > 1) {
      vista = h('section', { 'aria-labelledby': 'h-vista' },
        h('h2', { class: 'card-title', id: 'h-vista' }, icon('user', { size: 18 }), 'Aliados y las empresas que atienden'),
        aliadoCards(aliados));
    } else if (session.canWrite()) {
      vista = emptyState({ iconName: 'building', title: 'Aún no hay empresas', text: 'Empieza por crear la empresa de tu cliente; después sus sedes y sus routers.', action: h('a', { class: 'btn btn-primary', href: '#/empresas' }, icon('plus', { size: 18 }), 'Crear la primera empresa') });
    }

    replaceChildren(body, vista, quicks, stats, health,
      h('section', { class: 'card' }, h('h2', { class: 'card-title' }, icon('activity', { size: 18 }), 'Actividad reciente'), list));
  }

  await paintAll();
}

const ACCIONES = {
  router_aprovisionado: 'Se creó un router',
  sede_creada: 'Se creó una sede',
  login: 'Inicio de sesión',
  login_fallido: 'Intento de acceso fallido',
  cambio_password: 'Cambio de contraseña',
  script_consultado: 'Se descargó un script de instalación',
  plantilla_aplicada: 'Se aplicó un servicio',
  plantilla_confirmada: 'Se confirmó un servicio',
  plantilla_revertida: 'Se revirtió un servicio',
  vpn_creada: 'Se creó un acceso VPN',
  vpn_revocada: 'Se revocó un acceso VPN',
};
export function humanAccion(a) {
  return ACCIONES[a] || String(a || 'Acción').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}
void normEstado;
