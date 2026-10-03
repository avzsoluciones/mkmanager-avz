import { h, themeSwitch } from '../dom.js';
import { icon } from '../icons.js';
import { session } from '../session.js';
import { pageHeader } from '../components.js';

export default function mas(ctx) {
  const items = [
    { href: '#/sedes', ic: 'building', t: 'Sedes', d: 'Los lugares donde están tus equipos' },
    { href: '#/vpn', ic: 'key', t: 'VPN para personas', d: 'Accesos seguros con código QR' },
    { href: '#/cuenta', ic: 'user', t: 'Mi cuenta', d: 'Tus datos y tu contraseña' },
  ];
  if (session.isSuper()) items.push({ href: '#/actividad', ic: 'activity', t: 'Actividad', d: 'Quién hizo qué y cuándo' });
  ctx.container.append(
    pageHeader({ title: 'Más opciones' }),
    h('ul', { class: 'rows' }, items.map((i) => h('li', null,
      h('a', { class: 'row-card row-simple', href: i.href },
        h('span', { class: 'row-ico' }, icon(i.ic, { size: 22 })),
        h('span', { class: 'row-main' }, h('strong', { class: 'row-title' }, i.t), h('span', { class: 'row-sub' }, i.d)),
        icon('chevron', { size: 18, cls: 'row-chev' }))))),
    h('div', { class: 'card theme-row' }, h('div', null, h('strong', null, 'Tema oscuro'), h('p', { class: 'muted' }, 'Cambia entre claro y oscuro.')), themeSwitch()),
    h('button', { class: 'btn btn-secondary btn-block', type: 'button', onClick: () => { session.clear(); ctx.navigate('/ingresar'); } }, icon('logout', { size: 18 }), 'Cerrar sesión'));
}
