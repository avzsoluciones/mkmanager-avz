// Utilidades de DOM seguras: nunca se usa innerHTML; todo texto entra por nodos de texto.
import { icon } from './icons.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

function applyProps(el, props) {
  if (!props) return;
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'hidden' || k === 'selected') el[k] = v;
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, String(v));
  }
}

export function append(el, kids) {
  for (const kid of kids) {
    if (kid == null || kid === false) continue;
    if (Array.isArray(kid)) append(el, kid);
    else if (kid instanceof Node) el.appendChild(kid);
    else el.appendChild(document.createTextNode(String(kid)));
  }
  return el;
}

export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  applyProps(el, props);
  return append(el, kids);
}

export function svg(tag, props, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  if (props) for (const [k, v] of Object.entries(props)) if (v != null) el.setAttribute(k, String(v));
  return append(el, kids);
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function replaceChildren(el, ...kids) {
  clear(el);
  return append(el, kids);
}

let seq = 0;
export const uid = (p = 'id') => `${p}-${++seq}`;

export function debounce(fn, ms = 300) {
  let t;
  const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  d.cancel = () => clearTimeout(t);
  return d;
}

/* ---------- formato ---------- */
export function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('es', { dateStyle: 'medium', timeStyle: 'short' });
}

export function relTime(iso) {
  if (!iso) return 'Sin contacto todavía';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '—';
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 10) return 'Ahora mismo';
  if (s < 60) return `Hace ${s} s`;
  const m = Math.round(s / 60);
  if (m < 60) return `Hace ${m} min`;
  const hr = Math.round(m / 60);
  if (hr < 24) return `Hace ${hr} h`;
  const d = Math.round(hr / 24);
  return `Hace ${d} ${d === 1 ? 'día' : 'días'}`;
}

export function fmtBytes(n) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = Number(n), i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
}

export function fmtClock(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

/* ---------- portapapeles y descargas ---------- */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = h('textarea', { 'aria-hidden': 'true', tabindex: '-1', class: 'sr-offscreen' });
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
    return ok;
  }
}

export function downloadBlob(filename, data, mime = 'text/plain') {
  const blob = data instanceof Blob ? data : new Blob([data], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = h('a', { href: url, download: filename, class: 'sr-offscreen' });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/* ---------- avisos ---------- */
export function toast(message, { type = 'info', duration } = {}) {
  const host = document.getElementById('toasts');
  if (!host) return;
  const names = { ok: 'check', error: 'alert', warn: 'alert', info: 'info' };
  const el = h('div', { class: `toast toast-${type}` },
    icon(names[type] || 'info', { size: 20 }),
    h('p', { class: 'toast-msg' }, message),
    h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Cerrar aviso', onClick: () => el.remove() }, icon('x', { size: 16 })));
  host.appendChild(el);
  setTimeout(() => el.remove(), duration || (type === 'error' ? 9000 : 5000));
}

/* ---------- diálogos accesibles (elemento <dialog> nativo) ---------- */
export function openDialog({ title, body, actions = [], wide = false, onClose }) {
  const titleId = uid('dlg-title');
  const dlg = h('dialog', { class: `dialog${wide ? ' dialog-wide' : ''}`, 'aria-labelledby': titleId });
  const closeBtn = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Cerrar', onClick: () => close() }, icon('x', { size: 18 }));
  const footer = actions.length ? h('div', { class: 'dialog-actions' }) : null;
  dlg.append(
    h('div', { class: 'dialog-head' }, h('h2', { id: titleId, class: 'dialog-title' }, title), closeBtn),
    h('div', { class: 'dialog-body' }, body),
  );
  if (footer) dlg.appendChild(footer);

  let closed = false;
  function close(value) {
    if (closed) return;
    closed = true;
    if (dlg.open) dlg.close();
    dlg.remove();
    if (onClose) onClose(value);
  }
  for (const a of actions) {
    const btn = h('button', {
      type: 'button', class: `btn btn-${a.variant || 'secondary'}`,
      onClick: async () => {
        if (a.onClick) {
          btn.disabled = true;
          try { const r = await a.onClick({ close, button: btn }); if (r === false) btn.disabled = false; }
          catch (e) { btn.disabled = false; throw e; }
        } else close(a.value);
      },
    }, a.label);
    footer.appendChild(btn);
  }
  dlg.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
  dlg.addEventListener('click', (e) => { if (e.target === dlg) close(); });
  document.body.appendChild(dlg);
  dlg.showModal();
  return { dialog: dlg, close };
}

export function confirmDialog({ title, message, confirmLabel = 'Confirmar', cancelLabel = 'Cancelar', danger = false }) {
  return new Promise((resolve) => {
    let result = false;
    const { close } = openDialog({
      title,
      body: h('p', { class: 'dialog-text' }, message),
      actions: [
        { label: cancelLabel, variant: 'secondary', onClick: ({ close: c }) => { result = false; c(); } },
        { label: confirmLabel, variant: danger ? 'danger' : 'primary', onClick: ({ close: c }) => { result = true; c(); } },
      ],
      onClose: () => resolve(result),
    });
    void close;
  });
}

/* ---------- interruptor de tema ---------- */
export function currentTheme() {
  return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
}

export function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem('avz_theme', theme); } catch { /* sin almacenamiento */ }
  window.dispatchEvent(new CustomEvent('avz:theme', { detail: theme }));
}

export function themeSwitch() {
  const btn = h('button', { type: 'button', class: 'theme-switch', role: 'switch', 'aria-label': 'Tema oscuro' },
    h('span', { class: 'theme-switch-icons' }, icon('sun', { size: 14 }), icon('moon', { size: 14 })),
    h('span', { class: 'theme-switch-knob' }),
    h('span', { class: 'sr-only' }, 'Tema oscuro'));
  const sync = () => {
    btn.setAttribute('aria-checked', String(currentTheme() === 'dark'));
    btn.title = currentTheme() === 'dark' ? 'Cambiar a tema claro' : 'Cambiar a tema oscuro';
  };
  btn.addEventListener('click', () => setTheme(currentTheme() === 'dark' ? 'light' : 'dark'));
  window.addEventListener('avz:theme', sync);
  sync();
  return btn;
}

/* ---------- logo (dos imágenes; el CSS muestra la que corresponde al tema) ---------- */
export function logo({ cls = '' } = {}) {
  return h('span', { class: `logo ${cls}`.trim() },
    h('img', { class: 'logo-img logo-dark', src: 'assets/brand/logo-oscuro.png', alt: 'AVZ Cloud', width: 1690, height: 612 }),
    h('img', { class: 'logo-img logo-light', src: 'assets/brand/logo-claro.png', alt: 'AVZ Cloud', width: 1690, height: 612 }));
}

export function logoIcon({ cls = '' } = {}) {
  return h('img', { class: `logo-icon ${cls}`.trim(), src: 'assets/brand/icono.png', alt: 'AVZ Cloud', width: 738, height: 612 });
}
