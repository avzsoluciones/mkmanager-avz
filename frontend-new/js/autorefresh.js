// Control de actualización automática: el usuario elige cada cuántos segundos se refrescan los datos.
import { h } from './dom.js';
import { icon } from './icons.js';

const OPCIONES = [
  { sec: 0, label: 'Manual' },
  { sec: 5, label: 'Cada 5 s' },
  { sec: 10, label: 'Cada 10 s' },
  { sec: 15, label: 'Cada 15 s' },
  { sec: 30, label: 'Cada 30 s' },
  { sec: 60, label: 'Cada 60 s' },
];
const KEY = 'avz_refresco_seg';

const read = (def) => {
  try {
    const v = localStorage.getItem(KEY);
    if (v != null && OPCIONES.some((o) => String(o.sec) === v)) return Number(v);
  } catch { /* sin almacenamiento */ }
  return def;
};

/**
 * ctx: contexto de la pantalla (para limpiar el temporizador al salir).
 * def: segundos por defecto si el usuario nunca eligió. onTick(): vuelve a pedir y pintar los datos (silencioso).
 * Devuelve { el, refreshNow }.
 */
export function createAutoRefresh({ ctx, def = 15, onTick }) {
  let sec = read(def);
  let timer = null;
  let busy = false;

  const stamp = h('span', { class: 'ar-stamp', 'aria-live': 'off' }, '');
  const sel = h('select', { class: 'ar-select', 'aria-label': 'Frecuencia de actualización' },
    OPCIONES.map((o) => h('option', { value: String(o.sec) }, o.label)));
  sel.value = String(sec);
  const now = h('button', { class: 'icon-btn ar-now', type: 'button', title: 'Actualizar ahora', 'aria-label': 'Actualizar ahora' }, icon('refresh', { size: 18 }));

  async function run() {
    if (busy || !ctx.alive()) return;
    busy = true;
    now.classList.add('is-spinning');
    try { await onTick(); } catch { /* silencioso: la pantalla conserva los últimos datos */ }
    busy = false;
    now.classList.remove('is-spinning');
    if (ctx.alive()) stamp.textContent = new Date().toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function schedule() {
    clearInterval(timer);
    timer = null;
    if (sec > 0) timer = setInterval(() => { if (document.visibilityState === 'visible') run(); }, sec * 1000);
  }

  sel.addEventListener('change', () => {
    sec = Number(sel.value);
    try { localStorage.setItem(KEY, String(sec)); } catch { /* sin almacenamiento */ }
    schedule();
    if (sec > 0) run();
  });
  now.addEventListener('click', run);
  // Al volver a la pestaña, se actualiza de inmediato.
  const onVisible = () => { if (document.visibilityState === 'visible' && sec > 0) run(); };
  document.addEventListener('visibilitychange', onVisible);
  ctx.onCleanup(() => { clearInterval(timer); document.removeEventListener('visibilitychange', onVisible); });
  schedule();

  const el = h('div', { class: 'ar' }, icon('clock', { size: 16 }), sel, now, stamp);
  return { el, refreshNow: run };
}
