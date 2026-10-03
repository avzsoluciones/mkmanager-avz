// Herramientas comunes de las listas: selector Tarjetas/Lista y barra de selección con acción de eliminar.
import { h } from './dom.js';
import { icon } from './icons.js';

// Por defecto se ve en lista; las tarjetas son la segunda opción y se recuerdan si el usuario las elige.
const readMode = (key) => { try { return localStorage.getItem(key) === 'cards' ? 'cards' : 'list'; } catch { return 'list'; } };
const saveMode = (key, v) => { try { localStorage.setItem(key, v); } catch { /* sin almacenamiento */ } };

/**
 * key: clave para recordar la vista. canSelect: si el rol puede eliminar.
 * noun: { one, many } en femenino o masculino según corresponda, con `sel` = [singular, plural] de "seleccionado".
 * getVisibleIds: ids que se ven ahora. onDelete(ids): acción de eliminar. onChange(): repinta al cambiar la vista.
 */
export function createListTools({ key, canSelect, noun, deleteLabel, getVisibleIds, onDelete, onChange }) {
  let mode = readMode(key);
  const selected = new Set();

  const btnCards = h('button', { type: 'button', class: 'vt-btn' }, icon('grid', { size: 16 }), 'Tarjetas');
  const btnList = h('button', { type: 'button', class: 'vt-btn' }, icon('list', { size: 16 }), 'Lista');
  const toggle = h('div', { class: 'view-toggle', role: 'group', 'aria-label': 'Cómo ver la lista' }, btnList, btnCards);
  function paintToggle() {
    btnCards.setAttribute('aria-pressed', String(mode === 'cards'));
    btnList.setAttribute('aria-pressed', String(mode === 'list'));
  }
  function setMode(m) { if (m === mode) return; mode = m; saveMode(key, m); paintToggle(); onChange(); }
  btnCards.addEventListener('click', () => setMode('cards'));
  btnList.addEventListener('click', () => setMode('list'));
  paintToggle();

  const count = h('strong', null, '');
  const allBtn = h('button', { type: 'button', class: 'btn btn-secondary btn-sm' }, 'Seleccionar todo');
  const noneBtn = h('button', { type: 'button', class: 'btn btn-secondary btn-sm' }, 'Quitar selección');
  const delBtn = h('button', { type: 'button', class: 'btn btn-danger-ghost btn-sm' }, icon('trash', { size: 16 }), deleteLabel);
  const bar = h('div', { class: 'sel-bar', role: 'region', 'aria-label': 'Acciones sobre la selección', hidden: true },
    count, h('span', { class: 'sel-bar-actions' }, allBtn, noneBtn, delBtn));

  function updateBar() {
    const n = selected.size;
    bar.hidden = n === 0;
    count.textContent = `${n} ${n === 1 ? noun.one : noun.many} ${n === 1 ? noun.sel[0] : noun.sel[1]}`;
    for (const c of document.querySelectorAll('input.sel-check')) c.checked = selected.has(c.dataset.sel);
  }
  allBtn.addEventListener('click', () => { getVisibleIds().forEach((id) => selected.add(String(id))); updateBar(); });
  noneBtn.addEventListener('click', () => { selected.clear(); updateBar(); });
  delBtn.addEventListener('click', async () => {
    delBtn.disabled = true;
    try { await onDelete([...selected]); } finally { delBtn.disabled = false; }
  });

  return {
    toggle,
    bar: canSelect ? bar : h('div', { hidden: true }),
    get mode() { return mode; },
    /** Casilla de una fila o tarjeta; null si el rol no puede eliminar. */
    checkbox(id, name) {
      if (!canSelect) return null;
      const input = h('input', { type: 'checkbox', class: 'sel-check', 'aria-label': `Seleccionar ${name}`, checked: selected.has(String(id)) });
      input.dataset.sel = String(id);
      input.addEventListener('change', () => { if (input.checked) selected.add(String(id)); else selected.delete(String(id)); updateBar(); });
      return input;
    },
    /** Descarta de la selección lo que ya no existe. */
    prune(validIds) { const ok = new Set(validIds.map(String)); for (const id of [...selected]) if (!ok.has(id)) selected.delete(id); updateBar(); },
    clear() { selected.clear(); updateBar(); },
  };
}
