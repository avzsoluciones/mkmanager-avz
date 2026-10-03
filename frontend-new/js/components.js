// Componentes de interfaz reutilizables.
import { h, uid, toast, copyText, downloadBlob, replaceChildren, clear } from './dom.js';
import { icon } from './icons.js';

export function pageHeader({ title, subtitle, actions, back }) {
  return h('header', { class: 'page-head' },
    back ? h('a', { class: 'back-link', href: back.href }, icon('arrow-left', { size: 16 }), back.label) : null,
    h('div', { class: 'page-head-row' },
      h('div', { class: 'page-head-text' },
        h('h1', { class: 'page-title', tabindex: '-1', id: 'page-title' }, title),
        subtitle ? h('p', { class: 'page-sub' }, subtitle) : null),
      actions && actions.length ? h('div', { class: 'page-actions' }, actions) : null));
}

export const ESTADOS = {
  online: { label: 'En línea', icon: 'check', cls: 'ok' },
  pendiente: { label: 'Pendiente de conexión', icon: 'clock', cls: 'warn' },
  offline: { label: 'Fuera de línea', icon: 'ban', cls: 'bad' },
};

export function statusPill(estado) {
  const e = ESTADOS[estado] || { label: 'Sin datos', icon: 'info', cls: 'muted' };
  return h('span', { class: `pill pill-${e.cls}` }, icon(e.icon, { size: 14 }), e.label);
}

const RIESGO = {
  bajo: { label: 'Riesgo bajo', icon: 'shield', cls: 'ok', text: 'Es un cambio pequeño. No debería afectar la conexión del local.' },
  medio: { label: 'Riesgo medio', icon: 'alert', cls: 'warn', text: 'Puede cortar algunos servicios por unos segundos. Conviene aplicarlo con alguien atento en el local.' },
  alto: { label: 'Riesgo alto', icon: 'alert', cls: 'bad', text: 'Puede dejar al local sin internet o sin acceso remoto si algo sale mal. Usa la reversión automática.' },
};
export const riesgoInfo = (r) => RIESGO[r] || RIESGO.medio;

export function riskBadge(riesgo) {
  const r = riesgoInfo(riesgo);
  return h('span', { class: `pill pill-${r.cls}` }, icon(r.icon, { size: 14 }), r.label);
}

/* ---------- estados de carga / vacío / error ---------- */
export function skeletonBlock(cls = '') { return h('div', { class: `skeleton ${cls}`.trim(), 'aria-hidden': 'true' }); }

export function skeletonList(n = 4) {
  const wrap = h('div', { class: 'skeleton-list', role: 'status', 'aria-label': 'Cargando' });
  for (let i = 0; i < n; i++) wrap.appendChild(skeletonBlock('sk-row'));
  wrap.appendChild(h('span', { class: 'sr-only' }, 'Cargando…'));
  return wrap;
}

export function skeletonCards(n = 4) {
  const wrap = h('div', { class: 'grid-stats', role: 'status', 'aria-label': 'Cargando' });
  for (let i = 0; i < n; i++) wrap.appendChild(skeletonBlock('sk-card'));
  wrap.appendChild(h('span', { class: 'sr-only' }, 'Cargando…'));
  return wrap;
}

export function emptyState({ iconName = 'cloud', title, text, action }) {
  return h('div', { class: 'empty' },
    h('div', { class: 'empty-art' }, icon(iconName, { size: 34 })),
    h('h2', { class: 'empty-title' }, title),
    text ? h('p', { class: 'empty-text' }, text) : null,
    action || null);
}

export function errorState({ message, onRetry, title = 'No pudimos cargar esta información' }) {
  return h('div', { class: 'empty empty-error', role: 'alert' },
    h('div', { class: 'empty-art empty-art-bad' }, icon('alert', { size: 34 })),
    h('h2', { class: 'empty-title' }, title),
    h('p', { class: 'empty-text' }, message),
    onRetry ? h('button', { class: 'btn btn-secondary', type: 'button', onClick: onRetry }, icon('refresh', { size: 16 }), 'Reintentar') : null);
}

export function comingSoon(what) {
  return emptyState({
    iconName: 'sparkle',
    title: 'Esta función estará disponible muy pronto',
    text: `${what} todavía no está activa en este servidor. Cuando esté lista aparecerá aquí sin que tengas que hacer nada.`,
  });
}

export function notice({ kind = 'info', title, text, children }) {
  const names = { info: 'info', warn: 'alert', bad: 'alert', ok: 'check' };
  return h('div', { class: `notice notice-${kind}`, role: kind === 'bad' ? 'alert' : null },
    icon(names[kind] || 'info', { size: 20 }),
    h('div', { class: 'notice-body' }, title ? h('strong', null, title) : null, text ? h('p', null, text) : null, children || null));
}

/* ---------- formularios ---------- */
export function field(opts) {
  const {
    id = uid('f'), label, hint, required = false, type = 'text', value = '', placeholder, options,
    rows = 3, list, inputmode, autocomplete, maxlength, min, max, step, onInput, readonly, spellcheck,
  } = opts;
  const hintId = `${id}-hint`, errId = `${id}-err`;
  let input;
  const common = { id, class: 'input', 'aria-required': required ? 'true' : null, placeholder, autocomplete, inputmode, maxlength, min, max, step, list, readonly: readonly ? '' : null, spellcheck: spellcheck === false ? 'false' : null };
  if (type === 'select') {
    input = h('select', { id, class: 'input', 'aria-required': required ? 'true' : null },
      (options || []).map((o) => h('option', { value: o.value }, o.label)));
    input.value = value;
  } else if (type === 'textarea') {
    input = h('textarea', { ...common, rows });
    input.value = value;
  } else {
    input = h('input', { ...common, type });
    input.value = value;
  }
  const errText = h('span', { class: 'field-error-text' });
  const err = h('p', { class: 'field-error', id: errId, hidden: true }, icon('alert', { size: 14 }), errText);
  const hintEl = hint ? h('p', { class: 'field-hint', id: hintId }, hint) : null;
  const root = h('div', { class: 'field' },
    h('label', { class: 'field-label', for: id }, label, required ? null : h('span', { class: 'field-opt' }, ' (opcional)')),
    input, hintEl, err);

  const describe = (hasErr) => {
    const ids = [hint ? hintId : null, hasErr ? errId : null].filter(Boolean).join(' ');
    if (ids) input.setAttribute('aria-describedby', ids); else input.removeAttribute('aria-describedby');
  };
  describe(false);
  const api = {
    el: root, input, id,
    get value() { return input.value; },
    set value(v) { input.value = v; },
    setError(msg) {
      err.hidden = !msg;
      errText.textContent = msg || '';
      input.setAttribute('aria-invalid', msg ? 'true' : 'false');
      root.classList.toggle('has-error', !!msg);
      describe(!!msg);
    },
    setHint(text) { if (hintEl) hintEl.textContent = text; },
    focus() { input.focus(); },
  };
  if (onInput) input.addEventListener('input', () => onInput(api));
  return api;
}

/** Añade el botón "mostrar contraseña" a un campo de tipo password. */
export function withPasswordToggle(f) {
  const btn = h('button', { type: 'button', class: 'input-addon', 'aria-label': 'Mostrar contraseña', 'aria-pressed': 'false' }, icon('eye', { size: 18 }));
  btn.addEventListener('click', () => {
    const show = f.input.type === 'password';
    f.input.type = show ? 'text' : 'password';
    btn.setAttribute('aria-pressed', String(show));
    btn.setAttribute('aria-label', show ? 'Ocultar contraseña' : 'Mostrar contraseña');
    btn.replaceChildren(icon(show ? 'eye-off' : 'eye', { size: 18 }));
  });
  const wrap = h('div', { class: 'input-wrap' });
  f.input.replaceWith(wrap);
  wrap.append(f.input, btn);
  return f;
}

export function checkField({ id = uid('c'), label, hint, checked = false, onChange }) {
  const input = h('input', { type: 'checkbox', id, class: 'check-input', checked });
  const el = h('div', { class: 'field field-check' },
    h('label', { class: 'check', for: id }, input, h('span', { class: 'check-box' }, icon('check', { size: 14 })), h('span', { class: 'check-label' }, label)),
    hint ? h('p', { class: 'field-hint' }, hint) : null);
  if (onChange) input.addEventListener('change', () => onChange(input.checked));
  return { el, input, get value() { return input.checked; }, set value(v) { input.checked = !!v; }, setError() {}, focus() { input.focus(); } };
}

export function radioCards({ name, legend, options, value, onChange, cols = 3 }) {
  const fs = h('fieldset', { class: 'radio-cards' }, h('legend', { class: 'field-label' }, legend));
  const grid = h('div', { class: `radio-grid cols-${cols}` });
  let current = value;
  for (const o of options) {
    const id = uid('rc');
    const input = h('input', { type: 'radio', name, id, value: o.value, class: 'radio-input', checked: o.value === value });
    input.addEventListener('change', () => { current = o.value; if (onChange) onChange(o.value); });
    grid.appendChild(h('label', { class: 'radio-card', for: id }, input,
      h('span', { class: 'radio-card-inner' },
        h('span', { class: 'radio-card-title' }, o.icon ? icon(o.icon, { size: 18 }) : null, o.title),
        o.desc ? h('span', { class: 'radio-card-desc' }, o.desc) : null)));
  }
  fs.appendChild(grid);
  return { el: fs, get value() { return current; } };
}

/** Muestra errores 422 de ApiError en los campos. map: { claveApi: campo }. Devuelve los que no encontró. */
export function applyFieldErrors(err, map) {
  const leftovers = [];
  if (!err || !err.fields) return leftovers;
  let first = true;
  for (const [key, msg] of Object.entries(err.fields)) {
    const f = map[key] || map[key.split('.').pop()];
    if (f) { f.setError(msg); if (first) { f.focus(); first = false; } } else leftovers.push(msg);
  }
  return leftovers;
}

export function focusFirstError(fields) {
  const f = fields.find((x) => x.el && x.el.classList.contains('has-error'));
  if (f) f.focus();
  return !!f;
}

/** Botón con estado de carga. */
const savedKids = new WeakMap();
export function setBusy(btn, busy, busyLabel) {
  if (busy) {
    savedKids.set(btn, Array.from(btn.childNodes));
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    replaceChildren(btn, h('span', { class: 'spinner', 'aria-hidden': 'true' }), busyLabel || 'Un momento…');
  } else {
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    if (savedKids.has(btn)) { replaceChildren(btn, savedKids.get(btn)); savedKids.delete(btn); }
  }
}

/* ---------- visor de código ---------- */
function paintLine(line) {
  const parts = line.split(/(<[A-Z_]+>)/);
  const span = h('span', { class: line.trimStart().startsWith('#') ? 'ln ln-comment' : line.startsWith('/') ? 'ln ln-cmd' : 'ln' });
  for (const p of parts) {
    if (/^<[A-Z_]+>$/.test(p)) span.appendChild(h('mark', { class: 'ph' }, p)); else span.appendChild(document.createTextNode(p));
  }
  span.appendChild(document.createTextNode('\n'));
  return span;
}

export function codeViewer({ text = '', filename = 'script.rsc', label = 'Script de instalación', allowDownload = true, allowCopy = true, maxHeight = true }) {
  const code = h('code', null);
  const pre = h('pre', { class: `code${maxHeight ? '' : ' code-tall'}`, tabindex: '0', 'aria-label': label });
  pre.appendChild(code);
  let current = text;
  const render = () => {
    clear(code);
    for (const line of current.split('\n')) code.appendChild(paintLine(line));
  };
  render();
  const bar = h('div', { class: 'code-bar' }, h('span', { class: 'code-name' }, icon('terminal', { size: 16 }), filename));
  const tools = h('div', { class: 'code-tools' });
  if (allowCopy) {
    tools.appendChild(h('button', {
      class: 'btn btn-secondary btn-sm', type: 'button',
      onClick: async () => { toast((await copyText(current)) ? 'Copiado. Pégalo en el router.' : 'No se pudo copiar. Selecciona el texto y cópialo a mano.', { type: 'ok' }); },
    }, icon('copy', { size: 16 }), 'Copiar'));
  }
  if (allowDownload) {
    tools.appendChild(h('button', {
      class: 'btn btn-secondary btn-sm', type: 'button',
      onClick: () => downloadBlob(filename, current),
    }, icon('download', { size: 16 }), 'Descargar .rsc'));
  }
  bar.appendChild(tools);
  const el = h('div', { class: 'code-box' }, bar, pre);
  return {
    el,
    setText(t) { current = t; render(); },
    get text() { return current; },
    wipe() { current = ''; clear(code); },
  };
}

/* ---------- cuenta regresiva ---------- */
export function startCountdown(targetMs, onTick, onDone) {
  let timer = null;
  const tick = () => {
    const left = Math.round((targetMs - Date.now()) / 1000);
    if (left <= 0) { onTick(0); clearInterval(timer); if (onDone) onDone(); return; }
    onTick(left);
  };
  timer = setInterval(tick, 1000);
  tick();
  return () => clearInterval(timer);
}

export function parseTarget(revertirEn) {
  if (revertirEn == null || revertirEn === '') return null;
  if (typeof revertirEn === 'number') return revertirEn > 1e12 ? revertirEn : Date.now() + revertirEn * 1000;
  const t = new Date(revertirEn).getTime();
  return Number.isNaN(t) ? null : t;
}
