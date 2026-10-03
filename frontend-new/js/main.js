// Arranque, estructura de la aplicación (barra lateral, barra superior, barra inferior) y router por hash.
import { h, clear, logo, logoIcon, themeSwitch, toast } from './dom.js';
import { icon } from './icons.js';
import { session, ROLE_LABELS } from './session.js';
import { MOCK, setUnauthorizedHandler } from './api.js';

const app = document.getElementById('app');

const ROUTES = [
  { path: '/ingresar', load: () => import('./views/login.js'), bare: true, public: true, title: 'Entrar' },
  { path: '/cambiar-clave', load: () => import('./views/cuenta.js').then((m) => ({ default: m.forcedPassword })), bare: true, title: 'Cambiar contraseña' },
  { path: '/panel', load: () => import('./views/panel.js'), title: 'Panel' },
  { path: '/empresas', load: () => import('./views/empresas.js'), title: 'Empresas' },
  { path: '/empresas/:id', load: () => import('./views/empresa.js'), title: 'Empresa', parent: '/empresas' },
  { path: '/aliados/:id', load: () => import('./views/aliado.js'), title: 'Aliado', parent: '/panel' },
  { path: '/noc', load: () => import('./views/noc.js'), title: 'Vista NOC', bare: true },
  { path: '/sedes', load: () => import('./views/sedes.js'), title: 'Sedes' },
  { path: '/equipos', load: () => import('./views/equipos.js'), title: 'Equipos' },
  { path: '/equipos/:id', load: () => import('./views/equipo.js'), title: 'Equipo', parent: '/equipos' },
  { path: '/nuevo-router', load: () => import('./views/asistente.js'), title: 'Nuevo router', write: true },
  { path: '/servicios', load: () => import('./views/servicios.js'), title: 'Servicios', write: true },
  { path: '/servicios/:tid', load: () => import('./views/servicio.js'), title: 'Aplicar un servicio', write: true, parent: '/servicios' },
  { path: '/vpn', load: () => import('./views/vpn.js'), title: 'VPN para personas', write: true },
  { path: '/cuenta', load: () => import('./views/cuenta.js'), title: 'Mi cuenta' },
  { path: '/actividad', load: () => import('./views/actividad.js'), title: 'Actividad', superOnly: true, parent: '/cuenta' },
  { path: '/mas', load: () => import('./views/mas.js'), title: 'Más opciones' },
];

function compile(r) {
  const names = [];
  const rx = new RegExp('^' + r.path.replace(/:([a-z]+)/g, (_, n) => { names.push(n); return '([^/]+)'; }) + '/?$');
  return { ...r, rx, names };
}
const compiled = ROUTES.map(compile);

function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, qs] = raw.split('?');
  return { path: path || '/', query: new URLSearchParams(qs || '') };
}

export function navigate(path, { replace = false } = {}) {
  const target = '#' + path;
  if (replace) { history.replaceState(null, '', location.pathname + location.search + target); render(); }
  else if (location.hash === target) render();
  else location.hash = target;
}

/* ---------- estado de la pantalla actual ---------- */
let cleanups = [];
let token = 0;
let mode = null; // 'bare' | 'shell'
let shell = null;

function runCleanups() {
  for (const fn of cleanups.splice(0)) { try { fn(); } catch { /* limpieza best-effort */ } }
}

/* ---------- estructura ---------- */
function navItems() {
  const w = session.canWrite();
  const items = [
    { href: '/panel', label: 'Panel', icon: 'home' },
    { href: '/empresas', label: 'Empresas', icon: 'layers' },
    { href: '/sedes', label: 'Sedes', icon: 'building' },
    { href: '/equipos', label: 'Equipos', icon: 'router' },
  ];
  if (w) items.push({ href: '/servicios', label: 'Servicios', icon: 'sliders' }, { href: '/vpn', label: 'VPN para personas', icon: 'key' });
  items.push({ href: '/cuenta', label: 'Mi cuenta', icon: 'user' });
  return items;
}

function buildShell() {
  const user = session.user() || {};
  const side = h('aside', { class: 'sidebar' },
    h('a', { class: 'brand', href: '#/panel', 'aria-label': 'AVZ Cloud, ir al panel' }, logo({ cls: 'brand-full' }), logoIcon({ cls: 'brand-mini' })),
    h('p', { class: 'brand-sub' }, 'Central Manager'),
    session.canWrite() ? h('a', { class: 'btn btn-primary btn-block side-cta', href: '#/nuevo-router' }, icon('plus', { size: 18 }), h('span', { class: 'nav-label' }, 'Nuevo router')) : null,
    h('nav', { class: 'nav', 'aria-label': 'Principal' }, navItems().map((i) =>
      h('a', { class: 'nav-link', href: '#' + i.href, dataset: { route: i.href } }, icon(i.icon, { size: 20 }), h('span', { class: 'nav-label' }, i.label)))),
    h('div', { class: 'side-foot' },
      session.isSuper() ? h('a', { class: 'nav-link', href: '#/actividad', dataset: { route: '/actividad' } }, icon('activity', { size: 20 }), h('span', { class: 'nav-label' }, 'Actividad')) : null,
      h('button', { class: 'nav-link nav-btn', type: 'button', onClick: logout }, icon('logout', { size: 20 }), h('span', { class: 'nav-label' }, 'Cerrar sesión'))));

  const readHidden = () => { try { return localStorage.getItem('avz_menu_oculto') === '1'; } catch { return false; } };
  const sideToggle = h('button', { class: 'icon-btn side-toggle', type: 'button', 'aria-controls': 'main' }, icon('more', { size: 22 }));
  const topbar = h('header', { class: 'topbar' },
    sideToggle,
    h('a', { class: 'topbar-brand', href: '#/panel', 'aria-label': 'AVZ Cloud, ir al panel' }, logoIcon({ cls: 'topbar-icon' }), h('span', null, 'Central Manager')),
    h('div', { class: 'topbar-right' },
      h('span', { class: 'theme-label' }, 'Tema'),
      themeSwitch(),
      h('a', { class: 'user-chip', href: '#/cuenta', 'aria-label': 'Mi cuenta' },
        h('span', { class: 'avatar', 'aria-hidden': 'true' }, initials(user.full_name || user.email)),
        h('span', { class: 'user-chip-text' }, h('strong', null, user.full_name || user.email || 'Mi cuenta'), h('small', null, ROLE_LABELS[user.role] || user.role || '')))));

  const content = h('main', { class: 'content', id: 'main', tabindex: '-1' });
  const bottom = h('nav', { class: 'bottombar', 'aria-label': 'Navegación móvil' },
    bottomLink('/panel', 'Panel', 'home'),
    session.canWrite() ? bottomLink('/equipos', 'Equipos', 'router') : bottomLink('/sedes', 'Sedes', 'building'),
    session.canWrite()
      ? h('a', { class: 'bottom-fab', href: '#/nuevo-router', 'aria-label': 'Nuevo router' }, icon('plus', { size: 26 }))
      : bottomLink('/equipos', 'Equipos', 'router'),
    session.canWrite() ? bottomLink('/servicios', 'Servicios', 'sliders') : bottomLink('/cuenta', 'Mi cuenta', 'user'),
    session.canWrite() ? bottomLink('/mas', 'Más', 'more') : null);

  const col = h('div', { class: 'main-col' },
    MOCK ? h('div', { class: 'demo-bar' }, icon('sparkle', { size: 14 }), 'Modo demostración: los datos son de ejemplo y solo se conservan mientras esta pestaña siga abierta.') : null,
    topbar, content);
  const root = h('div', { class: 'shell' }, side, col, bottom);
  function paintSide(hidden) {
    root.classList.toggle('side-hidden', hidden);
    sideToggle.setAttribute('aria-expanded', String(!hidden));
    sideToggle.setAttribute('aria-label', hidden ? 'Mostrar el menú' : 'Ocultar el menú');
    sideToggle.title = hidden ? 'Mostrar el menú' : 'Ocultar el menú';
  }
  paintSide(readHidden());
  sideToggle.addEventListener('click', () => {
    const hidden = !root.classList.contains('side-hidden');
    paintSide(hidden);
    try { localStorage.setItem('avz_menu_oculto', hidden ? '1' : '0'); } catch { /* sin almacenamiento */ }
  });
  return { root, content, nav: [...side.querySelectorAll('[data-route]'), ...bottom.querySelectorAll('[data-route]')] };
}

function bottomLink(href, label, ic) {
  return h('a', { class: 'bottom-link', href: '#' + href, dataset: { route: href } }, icon(ic, { size: 22 }), h('span', null, label));
}

function initials(name) {
  const parts = String(name || '?').split(/[\s@.]+/).filter(Boolean);
  return ((parts[0] || '?')[0] + (parts[1] ? parts[1][0] : '')).toUpperCase();
}

function logout() {
  session.clear();
  navigate('/ingresar');
}

function markActive(path, route) {
  if (!shell) return;
  const base = '/' + (route.parent || path).split('/')[1];
  for (const a of shell.nav) {
    const r = a.dataset.route;
    const active = r === base || (route.path === '/mas' && r === '/mas');
    if (active) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
}

/* ---------- ciclo de pantalla ---------- */
async function render() {
  const my = ++token;
  runCleanups();
  const { path, query } = parseHash();

  if (path === '/' || path === '') return navigate(session.isAuthed() ? '/panel' : '/ingresar', { replace: true });

  let route = null, params = {};
  for (const r of compiled) {
    const m = r.rx.exec(path);
    if (m) { route = r; r.names.forEach((n, i) => { params[n] = decodeURIComponent(m[i + 1]); }); break; }
  }
  if (!route) return navigate(session.isAuthed() ? '/panel' : '/ingresar', { replace: true });

  if (!route.public && !session.isAuthed()) return navigate('/ingresar', { replace: true });
  if (route.public && session.isAuthed()) return navigate(session.mustChange() ? '/cambiar-clave' : '/panel', { replace: true });
  if (session.isAuthed() && session.mustChange() && route.path !== '/cambiar-clave') return navigate('/cambiar-clave', { replace: true });
  if (route.write && !session.canWrite()) { toast('Tu perfil es de solo lectura: puedes consultar, pero no cambiar nada.', { type: 'info' }); return navigate('/panel', { replace: true }); }
  if (route.superOnly && !session.isSuper()) return navigate('/panel', { replace: true });

  let container;
  if (route.bare) {
    if (mode !== 'bare') { clear(app); mode = 'bare'; shell = null; }
    if (!app.firstChild) { container = h('main', { class: 'bare', id: 'main', tabindex: '-1' }); app.appendChild(container); }
    else container = app.firstChild;
    clear(container);
  } else {
    if (mode !== 'shell') { clear(app); shell = buildShell(); app.appendChild(shell.root); mode = 'shell'; }
    container = shell.content;
    clear(container);
    markActive(path, route);
  }

  document.title = `${route.title} · AVZ Cloud Central Manager`;
  const ctx = {
    params, query, container, route,
    user: session.user(),
    alive: () => my === token,
    onCleanup: (fn) => cleanups.push(fn),
    setTitle: (t) => { document.title = `${t} · AVZ Cloud Central Manager`; },
    navigate,
  };
  window.scrollTo(0, 0);

  try {
    const mod = await route.load();
    if (!ctx.alive()) return;
    await mod.default(ctx);
  } catch (e) {
    if (!ctx.alive()) return;
    console.error('Error de pantalla:', e && e.message ? e.message : 'desconocido');
    const { errorState } = await import('./components.js');
    clear(container);
    container.appendChild(errorState({ message: 'La pantalla no se pudo abrir. Recarga la página e inténtalo otra vez.', onRetry: () => render() }));
  }
  if (ctx.alive()) {
    const hd = container.querySelector('#page-title');
    if (hd && !route.bare) hd.focus({ preventScroll: true });
  }
}

setUnauthorizedHandler(() => {
  if (!session.isAuthed()) return;
  session.clear();
  toast('Tu sesión terminó. Entra de nuevo para continuar.', { type: 'warn' });
  navigate('/ingresar');
});

window.addEventListener('hashchange', render);
render();
