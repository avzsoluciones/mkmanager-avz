import { h, logo, themeSwitch } from '../dom.js';
import { icon } from '../icons.js';
import { field, notice, setBusy, withPasswordToggle } from '../components.js';
import { api, MOCK, ApiError } from '../api.js';
import { session } from '../session.js';

export default function login(ctx) {
  const email = field({ id: 'login-email', label: 'Correo o usuario', type: 'text', required: true, autocomplete: 'username', spellcheck: false, placeholder: 'nombre@empresa.com' });
  const pass = field({ id: 'login-pass', label: 'Contraseña', type: 'password', required: true, autocomplete: 'current-password' });
  withPasswordToggle(pass);

  const alertBox = h('div', { class: 'form-alert-slot', 'aria-live': 'assertive' });
  const submit = h('button', { class: 'btn btn-primary btn-lg btn-block', type: 'submit' }, 'Entrar');

  const form = h('form', { class: 'stack', novalidate: '' }, email.el, pass.el, alertBox, submit);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    alertBox.replaceChildren();
    email.setError(''); pass.setError('');
    let bad = false;
    if (!email.value.trim()) { email.setError('Escribe tu correo o usuario.'); bad = true; }
    if (!pass.value) { pass.setError('Escribe tu contraseña.'); bad = true; }
    if (bad) { (email.value.trim() ? pass : email).focus(); return; }
    setBusy(submit, true, 'Entrando…');
    try {
      const resp = await api.post('/auth/login', { email: email.value.trim(), password: pass.value }, { auth: false });
      if (!ctx.alive()) return;
      session.setLogin(resp);
      ctx.navigate(resp.require_password_change ? '/cambiar-clave' : '/panel');
    } catch (e) {
      if (!ctx.alive()) return;
      setBusy(submit, false);
      let text = 'No pudimos iniciar sesión. Inténtalo otra vez.';
      let title = 'No se pudo entrar';
      if (e instanceof ApiError) {
        if (e.status === 401) { title = 'Datos incorrectos'; text = 'El correo o la contraseña no coinciden, o tu usuario está inactivo. Revisa los datos e inténtalo de nuevo.'; }
        else if (e.status === 429) { title = 'Demasiados intentos'; text = 'Por seguridad bloqueamos el acceso por unos minutos. Espera un poco y vuelve a intentarlo.'; }
        else text = e.message;
      }
      alertBox.replaceChildren(notice({ kind: 'bad', title, text }));
      pass.value = '';
      pass.focus();
    }
  });

  const demo = MOCK ? notice({
    kind: 'info', title: 'Modo demostración',
    text: 'Entra con cualquier correo y contraseña. Prueba: "nuevo@demo.com" (pide cambiar contraseña), "partner@demo.com", "soporte@demo.com" o "cliente@demo.com" (solo lectura). Contraseña "incorrecta" simula un error y "bloqueo" simula demasiados intentos.',
  }) : null;

  ctx.container.appendChild(h('div', { class: 'login' },
    h('div', { class: 'login-side', 'aria-hidden': 'true' },
      h('div', { class: 'login-side-inner' },
        h('p', { class: 'login-kicker' }, 'AVZ Cloud Central Manager'),
        h('p', { class: 'login-claim' }, 'Instala, controla y atiende los routers de tus clientes ', h('span', { class: 'grad-text' }, 'sin tocar la consola.')),
        h('ul', { class: 'login-points' },
          h('li', null, icon('check', { size: 18 }), 'Nuevo router en minutos con un asistente paso a paso'),
          h('li', null, icon('check', { size: 18 }), 'Túnel seguro AVZ para dar soporte a distancia'),
          h('li', null, icon('check', { size: 18 }), 'Cambios con vista previa y reversión automática')))),
    h('div', { class: 'login-main' },
      h('div', { class: 'login-top' }, h('span', { class: 'theme-label' }, 'Tema'), themeSwitch()),
      h('div', { class: 'login-card' },
        logo({ cls: 'login-logo' }),
        h('h1', { class: 'login-title', id: 'page-title' }, 'Bienvenido de nuevo'),
        h('p', { class: 'login-sub' }, 'Entra con tu cuenta de AVZ Cloud para gestionar tus equipos.'),
        form, demo))));
  email.focus();
}
