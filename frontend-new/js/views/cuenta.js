import { h, toast, themeSwitch, logo } from '../dom.js';
import { icon } from '../icons.js';
import { api, ApiError } from '../api.js';
import { session, ROLE_LABELS } from '../session.js';
import { pageHeader, field, withPasswordToggle, notice, setBusy, focusFirstError } from '../components.js';

const RULES = [
  ['Al menos 10 caracteres', (p) => p.length >= 10],
  ['Una letra mayúscula', (p) => /[A-Z]/.test(p)],
  ['Una letra minúscula', (p) => /[a-z]/.test(p)],
  ['Un número', (p) => /\d/.test(p)],
  ['Un símbolo (por ejemplo ! ? # $)', (p) => /[^A-Za-z0-9]/.test(p)],
];

function passwordForm({ onDone, forced }) {
  const cur = withPasswordToggle(field({ id: 'pw-cur', label: 'Contraseña actual', type: 'password', required: true, autocomplete: 'current-password' }));
  const nw = withPasswordToggle(field({ id: 'pw-new', label: 'Contraseña nueva', type: 'password', required: true, autocomplete: 'new-password' }));
  const rep = withPasswordToggle(field({ id: 'pw-rep', label: 'Repite la contraseña nueva', type: 'password', required: true, autocomplete: 'new-password' }));
  const checklist = h('ul', { class: 'rules', 'aria-label': 'Requisitos de la contraseña' }, RULES.map(([t]) => h('li', { class: 'rule' }, icon('x', { size: 14 }), h('span', null, t))));
  const paint = () => {
    [...checklist.children].forEach((li, i) => {
      const ok = RULES[i][1](nw.value);
      li.classList.toggle('is-ok', ok);
      li.firstChild.replaceWith(icon(ok ? 'check' : 'x', { size: 14 }));
      li.lastChild.textContent = `${RULES[i][0]}${ok ? ' (listo)' : ''}`;
    });
  };
  nw.input.addEventListener('input', paint);
  const slot = h('div', { 'aria-live': 'assertive' });
  const submit = h('button', { class: 'btn btn-primary btn-lg', type: 'submit' }, forced ? 'Guardar y continuar' : 'Cambiar contraseña');
  const form = h('form', { class: 'stack', novalidate: '' }, cur.el, nw.el, checklist, rep.el, slot, submit);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    [cur, nw, rep].forEach((f) => f.setError('')); slot.replaceChildren();
    if (!cur.value) cur.setError('Escribe tu contraseña actual.');
    if (RULES.some(([, fn]) => !fn(nw.value))) nw.setError('La contraseña nueva aún no cumple todos los requisitos de la lista.');
    else if (nw.value === cur.value) nw.setError('La contraseña nueva debe ser distinta de la actual.');
    if (rep.value !== nw.value) rep.setError('Las dos contraseñas no coinciden.');
    if (focusFirstError([cur, nw, rep])) return;
    setBusy(submit, true, 'Guardando…');
    try {
      await api.post('/auth/change-password', { current_password: cur.value, new_password: nw.value });
      session.clearMustChange();
      cur.value = ''; nw.value = ''; rep.value = ''; paint();
      toast('Contraseña actualizada.', { type: 'ok' });
      if (onDone) onDone();
    } catch (e) {
      if (e instanceof ApiError && e.status === 400) {
        if (/actual/i.test(e.message)) cur.setError('La contraseña actual no es correcta.');
        else nw.setError(e.message);
        focusFirstError([cur, nw]);
      } else slot.replaceChildren(notice({ kind: 'bad', title: 'No se pudo cambiar la contraseña', text: e.message }));
    }
    setBusy(submit, false);
  });
  return form;
}

export default function cuenta(ctx) {
  const u = ctx.user || {};
  const header = pageHeader({ title: 'Mi cuenta', subtitle: 'Tus datos, tu contraseña y la apariencia de la aplicación.' });
  const profile = h('section', { class: 'card stack', 'aria-labelledby': 'h-perfil' },
    h('h2', { class: 'card-title', id: 'h-perfil' }, icon('user', { size: 18 }), 'Mis datos'),
    h('dl', { class: 'dl' },
      h('div', { class: 'dl-row' }, h('dt', null, 'Nombre'), h('dd', null, u.full_name || '—')),
      h('div', { class: 'dl-row' }, h('dt', null, 'Correo'), h('dd', null, u.email || '—')),
      h('div', { class: 'dl-row' }, h('dt', null, 'Perfil'), h('dd', null, ROLE_LABELS[u.role] || u.role || '—'))),
    h('div', { class: 'theme-row' }, h('div', null, h('strong', null, 'Tema oscuro'), h('p', { class: 'muted' }, 'Cambia entre tema claro y oscuro.')), themeSwitch()),
    h('div', { class: 'btn-row' },
      session.isSuper() ? h('a', { class: 'btn btn-secondary', href: '#/actividad' }, icon('activity', { size: 18 }), 'Ver actividad') : null,
      h('button', { class: 'btn btn-secondary', type: 'button', onClick: () => { session.clear(); ctx.navigate('/ingresar'); } }, icon('logout', { size: 18 }), 'Cerrar sesión')));
  const pw = h('section', { class: 'card stack', 'aria-labelledby': 'h-pw' }, h('h2', { class: 'card-title', id: 'h-pw' }, icon('lock', { size: 18 }), 'Cambiar contraseña'), passwordForm({}));
  ctx.container.append(header, h('div', { class: 'detail-grid' }, profile, pw));
}

/** Pantalla obligatoria tras el primer ingreso. */
export function forcedPassword(ctx) {
  ctx.container.append(h('div', { class: 'login login-single' },
    h('div', { class: 'login-main' },
      h('div', { class: 'login-top' }, h('span', { class: 'theme-label' }, 'Tema'), themeSwitch()),
      h('div', { class: 'login-card' },
        logo({ cls: 'login-logo' }),
        h('h1', { class: 'login-title', id: 'page-title' }, 'Elige una contraseña nueva'),
        h('p', { class: 'login-sub' }, 'Por seguridad debes cambiar la contraseña que te dieron antes de usar la plataforma.'),
        passwordForm({ forced: true, onDone: () => ctx.navigate('/panel') }),
        h('button', { class: 'btn btn-ghost btn-block', type: 'button', onClick: () => { session.clear(); ctx.navigate('/ingresar'); } }, 'Salir')))));
}
