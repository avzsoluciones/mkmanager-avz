// Aplicar una plantilla a un equipo: formulario generado desde el esquema, vista previa, riesgo y reversión.
import { h, replaceChildren, clear, toast, confirmDialog, fmtClock, debounce } from '../dom.js';
import { icon } from '../icons.js';
import { api, optional, ApiError } from '../api.js';
import {
  pageHeader, skeletonList, errorState, comingSoon, field, checkField, notice, codeViewer, riskBadge, riesgoInfo, setBusy,
  applyFieldErrors, focusFirstError, startCountdown, parseTarget,
} from '../components.js';
import { listDevices, categoriaDe } from '../data.js';
import * as net from '../net.js';
import { iconFor } from './servicios.js';

const MINUTES = [2, 5, 10, 15];

function buildField(c, idPrefix) {
  const id = `${idPrefix}-${c.name}`;
  const base = { id, label: c.label || c.name, required: !!c.required, hint: c.help };
  switch (c.type) {
    case 'bool': return checkField({ id, label: c.label || c.name, hint: c.help, checked: !!c.default });
    case 'select': {
      const opts = (c.options || []).map((o) => (typeof o === 'object' ? { value: String(o.value), label: o.label ?? String(o.value) } : { value: String(o), label: String(o) }));
      if (!c.required) opts.unshift({ value: '', label: 'Sin elegir' });
      return field({ ...base, type: 'select', options: opts, value: c.default != null ? String(c.default) : (opts[0] ? opts[0].value : '') });
    }
    case 'number': return field({ ...base, type: 'number', inputmode: 'numeric', value: c.default != null ? String(c.default) : '', step: 'any' });
    case 'list': return field({ ...base, type: 'textarea', rows: 3, value: Array.isArray(c.default) ? c.default.join('\n') : (c.default || ''), hint: c.help || 'Escribe un valor por línea.', spellcheck: false });
    case 'ip': return field({ ...base, value: c.default || '', placeholder: '192.168.10.20', spellcheck: false, autocomplete: 'off' });
    case 'cidr': return field({ ...base, value: c.default || '', placeholder: '192.168.10.0/24', spellcheck: false, autocomplete: 'off' });
    default: return field({ ...base, value: c.default != null ? String(c.default) : '', autocomplete: 'off' });
  }
}

function readValue(c, f) {
  if (c.type === 'bool') return f.value;
  const raw = f.value;
  if (c.type === 'number') return raw === '' ? undefined : Number(raw);
  if (c.type === 'list') return raw.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
  const t = String(raw).trim();
  return t === '' ? undefined : t;
}

function checkValue(c, f) {
  if (c.type === 'bool') return '';
  const v = readValue(c, f);
  const empty = v === undefined || (Array.isArray(v) && !v.length);
  if (empty) return c.required ? 'Este campo es obligatorio.' : '';
  if (c.type === 'number' && Number.isNaN(v)) return 'Escribe un número.';
  if (c.type === 'ip') return net.checkIp(v, 'La dirección');
  if (c.type === 'cidr') return net.checkCidr(v, 'La red');
  return '';
}

export default async function servicio(ctx) {
  const tid = ctx.params.tid;
  const body = h('div', { class: 'stack-lg' });
  ctx.container.append(body);
  replaceChildren(body, skeletonList(4));
  let templates, devices;
  try {
    [templates, devices] = await Promise.all([optional(() => api.get('/templates')), listDevices()]);
  } catch (e) {
    if (!ctx.alive()) return;
    replaceChildren(body, pageHeader({ title: 'Aplicar un servicio', back: { href: '#/servicios', label: 'Volver a servicios' } }), errorState({ message: e.message, onRetry: () => ctx.navigate(`/servicios/${tid}`) }));
    return;
  }
  if (!ctx.alive()) return;
  if (templates == null) { replaceChildren(body, pageHeader({ title: 'Aplicar un servicio', back: { href: '#/servicios', label: 'Volver a servicios' } }), comingSoon('El catálogo de servicios')); return; }
  const tpl = templates.find((t) => String(t.id) === String(tid));
  if (!tpl) {
    replaceChildren(body, pageHeader({ title: 'Servicio no encontrado', back: { href: '#/servicios', label: 'Volver a servicios' } }), errorState({ title: 'No existe ese servicio', message: 'Puede que ya no esté en el catálogo. Elige otro desde la lista.' }));
    return;
  }
  ctx.setTitle(tpl.nombre);
  const cat = categoriaDe(tpl.categoria);
  const riesgo = riesgoInfo(tpl.riesgo);
  const prefix = `p-${String(tpl.id).replace(/\W+/g, '')}`;

  /* ----- campos ----- */
  const equipo = field({
    id: `${prefix}-equipo`, label: '¿En qué equipo?', type: 'select', required: true, value: ctx.query.get('equipo') || '',
    options: [{ value: '', label: 'Elige un equipo…' }, ...devices.map((d) => ({ value: String(d.id), label: `${d.nombre} · ${d.sede}` }))],
  });
  const fields = (tpl.campos || []).map((c) => ({ c, f: buildField(c, prefix) }));
  const all = [equipo, ...fields.map((x) => x.f)];
  const errMap = Object.fromEntries(fields.map(({ c, f }) => [c.name, f]));
  errMap['params'] = equipo;

  let minutos = tpl.revierte ? 5 : 0;
  let revertOn = !!tpl.revierte;
  let viewer = codeViewer({ text: '', filename: 'cambio.rsc', label: 'Vista previa del cambio', allowCopy: true, allowDownload: true });
  const previewBox = h('div', { class: 'stack' });
  let previewReady = false;
  let currentApp = null;
  let stopCount = null;
  ctx.onCleanup(() => { if (stopCount) stopCount(); viewer.wipe(); });

  const slot = h('div', { 'aria-live': 'assertive' });

  function gather() {
    let bad = false;
    all.forEach((f) => f.setError && f.setError(''));
    if (!equipo.value) { equipo.setError('Elige el equipo donde quieres aplicarlo.'); bad = true; }
    const params = {};
    for (const { c, f } of fields) {
      const msg = checkValue(c, f);
      if (msg) { f.setError(msg); bad = true; }
      const v = readValue(c, f);
      if (v !== undefined) params[c.name] = v;
    }
    if (bad) { focusFirstError(all); return null; }
    return params;
  }

  function markStale() {
    if (previewReady) { previewReady = false; applyBtn.disabled = true; previewBox.classList.add('is-stale'); }
  }
  const stale = debounce(markStale, 50);
  for (const x of all) { x.input && x.input.addEventListener('input', stale); x.input && x.input.addEventListener('change', stale); }

  /* ----- vista previa ----- */
  const previewBtn = h('button', { class: 'btn btn-secondary btn-lg', type: 'button' }, icon('eye', { size: 18 }), 'Ver vista previa');
  const applyBtn = h('button', { class: 'btn btn-primary btn-lg', type: 'submit', disabled: true }, icon('bolt', { size: 18 }), 'Aplicar al equipo');

  previewBtn.addEventListener('click', async () => {
    slot.replaceChildren();
    const params = gather();
    if (!params) return;
    setBusy(previewBtn, true, 'Preparando…');
    try {
      const res = await api.post(`/devices/${encodeURIComponent(equipo.value)}/templates/${encodeURIComponent(tpl.id)}/preview`, { params });
      if (!ctx.alive()) return;
      viewer.setText(res.script || '');
      const avisos = Array.isArray(res.avisos) ? res.avisos : [];
      replaceChildren(previewBox,
        avisos.length ? notice({ kind: 'warn', title: avisos.length === 1 ? 'Un aviso' : `${avisos.length} avisos`, children: h('ul', { class: 'notice-list' }, avisos.map((a) => h('li', null, a))) }) : notice({ kind: 'ok', title: 'Sin avisos', text: 'Revisamos los datos y no vimos nada raro.' }),
        viewer.el);
      previewBox.classList.remove('is-stale');
      previewReady = true; applyBtn.disabled = false;
    } catch (e) { handleError(e, 'No pudimos generar la vista previa'); }
    if (ctx.alive()) setBusy(previewBtn, false);
  });

  function handleError(e, title) {
    if (!ctx.alive()) return;
    if (e instanceof ApiError && e.status === 422) {
      const rest = applyFieldErrors(e, errMap);
      const text = rest.length ? rest.join(' ') : (typeof e.message === 'string' && Object.keys(e.fields).length === 0 ? e.message : 'Revisa los campos marcados.');
      slot.replaceChildren(notice({ kind: 'bad', title, text }));
    } else if (e instanceof ApiError && e.notFound) {
      slot.replaceChildren(notice({ kind: 'warn', title: 'Todavía no disponible', text: 'El servidor aún no tiene activa esta función. Inténtalo más tarde.' }));
    } else {
      slot.replaceChildren(notice({ kind: 'bad', title, text: e.message || 'Error inesperado.' }));
    }
  }

  /* ----- reversión ----- */
  const revertBox = h('fieldset', { class: 'revert' });
  function paintRevert() {
    if (!tpl.revierte) { clear(revertBox); revertBox.hidden = true; return; }
    const mustRevert = tpl.riesgo === 'alto';
    if (mustRevert) revertOn = true;
    const sw = checkField({ id: `${prefix}-rev`, label: 'Revertir automáticamente si no confirmo', checked: revertOn, hint: mustRevert ? 'Con riesgo alto la reversión es obligatoria: así no te quedas sin acceso al equipo.' : 'Si algo sale mal y pierdes la conexión, el equipo vuelve solo a como estaba.', onChange: (v) => { revertOn = v; paintRevert(); } });
    if (mustRevert) sw.input.disabled = true;
    const mins = h('div', { class: 'seg', role: 'radiogroup', 'aria-label': 'Minutos para revertir' }, MINUTES.map((m) => {
      const id = `${prefix}-m${m}`;
      const input = h('input', { type: 'radio', name: `${prefix}-min`, id, value: String(m), class: 'seg-input', checked: m === minutos, disabled: !revertOn });
      input.addEventListener('change', () => { minutos = m; });
      return h('label', { class: 'seg-opt', for: id }, input, h('span', null, `${m} min`));
    }));
    replaceChildren(revertBox, h('legend', { class: 'field-label' }, icon('undo', { size: 16 }), 'Red de seguridad'), sw.el,
      h('p', { class: 'field-label' }, `Revertir automáticamente en ${revertOn ? minutos : 'N'} minutos si no confirmo`), mins);
    for (const r of mins.querySelectorAll('input')) r.addEventListener('change', () => { revertBox.querySelector('p.field-label').textContent = `Revertir automáticamente en ${minutos} minutos si no confirmo`; });
  }
  paintRevert();

  /* ----- aplicar ----- */
  const resultBox = h('div', { class: 'stack' });
  const form = h('form', { class: 'stack', novalidate: '' });
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    slot.replaceChildren();
    if (!previewReady) { toast('Primero pulsa «Ver vista previa» para revisar el cambio.', { type: 'info' }); return; }
    const params = gather();
    if (!params) return;
    const dev = devices.find((d) => String(d.id) === equipo.value);
    const ok = await confirmDialog({
      title: `¿Aplicar «${tpl.nombre}»?`,
      message: `Se enviará el cambio al equipo «${dev ? dev.nombre : ''}». ${riesgo.text}${tpl.revierte && revertOn ? ` Si no confirmas, volverá a como estaba en ${minutos} minutos.` : ''}`,
      confirmLabel: 'Sí, aplicar', danger: tpl.riesgo === 'alto',
    });
    if (!ok) return;
    setBusy(applyBtn, true, 'Aplicando…');
    try {
      const payload = { params };
      if (tpl.revierte && revertOn) payload.revertir_minutos = minutos;
      const res = await api.post(`/devices/${encodeURIComponent(equipo.value)}/templates/${encodeURIComponent(tpl.id)}/apply`, payload);
      if (!ctx.alive()) return;
      currentApp = { id: res.aplicacion_id, estado: res.estado, target: parseTarget(res.revertir_en) };
      toast('Cambio aplicado al equipo.', { type: 'ok' });
      showResult(dev);
    } catch (e) { handleError(e, 'No se pudo aplicar el cambio'); }
    if (ctx.alive()) { setBusy(applyBtn, false); applyBtn.disabled = !previewReady; }
  });

  function showResult(dev) {
    if (stopCount) stopCount();
    const a = currentApp;
    const clock = h('div', { class: 'countdown-clock', role: 'timer', 'aria-live': 'off' }, '--:--');
    const confirmBtn = h('button', { class: 'btn btn-primary btn-xl', type: 'button' }, icon('check', { size: 22 }), 'Confirmar que todo funciona');
    const status = h('p', { class: 'muted', 'aria-live': 'polite' });
    const hasTimer = a.target && a.target > Date.now() && a.estado !== 'confirmada';
    confirmBtn.addEventListener('click', async () => {
      setBusy(confirmBtn, true, 'Confirmando…');
      try {
        await api.post(`/applications/${encodeURIComponent(a.id)}/confirm`, {});
        if (stopCount) stopCount();
        a.estado = 'confirmada';
        toast('Listo, el cambio quedó confirmado.', { type: 'ok' });
        showResult(dev);
      } catch (e) { setBusy(confirmBtn, false); toast(e.message, { type: 'error' }); }
    });
    const done = h('div', { class: 'card result-card' },
      h('div', { class: 'success-ico' }, icon('check', { size: 28 })),
      h('div', { class: 'stack' },
        h('h2', { class: 'step-title' }, a.estado === 'confirmada' ? 'Cambio confirmado' : 'Cambio aplicado'),
        a.estado === 'confirmada'
          ? h('p', { class: 'step-intro' }, 'Quedó guardado de forma definitiva en el equipo.')
          : hasTimer
            ? h('p', { class: 'step-intro' }, 'Comprueba que el local sigue con internet y que todo funciona. Si no confirmas a tiempo, el equipo vuelve solo a como estaba.')
            : h('p', { class: 'step-intro' }, tpl.revierte ? 'Este cambio se aplicó sin reversión automática.' : 'Este servicio no necesita confirmación.'),
        hasTimer ? clock : null, hasTimer ? confirmBtn : null, status,
        h('div', { class: 'wizard-nav' },
          h('a', { class: 'btn btn-secondary', href: '#/servicios' }, 'Aplicar otro servicio'),
          h('a', { class: 'btn btn-secondary', href: `#/equipos/${encodeURIComponent(equipo.value)}` }, 'Ver el equipo'))));
    replaceChildren(resultBox, done);
    resultBox.scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (hasTimer) {
      stopCount = startCountdown(a.target, (s) => { clock.textContent = fmtClock(s); clock.classList.toggle('is-low', s <= 30); },
        () => { clock.textContent = '00:00'; status.textContent = 'Se acabó el tiempo: el equipo está volviendo a su estado anterior. Revisa el historial del equipo en unos segundos.'; confirmBtn.disabled = true; });
    }
  }

  /* ----- armado de la pantalla ----- */
  form.append(
    h('section', { class: 'card stack' },
      h('h2', { class: 'card-title' }, icon('sliders', { size: 18 }), 'Datos del servicio'),
      equipo.el,
      ...fields.map((x) => x.f.el)),
    revertBox, slot,
    h('div', { class: 'wizard-nav' }, previewBtn, applyBtn));

  replaceChildren(body,
    pageHeader({
      title: tpl.nombre, subtitle: tpl.descripcion, back: { href: '#/servicios', label: 'Volver a servicios' },
    }),
    h('div', { class: 'wizard-cols svc-cols' },
      form,
      h('aside', { class: 'stack', 'aria-label': 'Resumen y vista previa' },
        h('section', { class: `card risk-card risk-${tpl.riesgo}` },
          h('div', { class: 'risk-top' }, h('span', { class: `svc-ico bg-${cat.color}` }, icon(iconFor(tpl), { size: 24 })), riskBadge(tpl.riesgo)),
          h('p', null, riesgo.text),
          tpl.revierte ? h('p', { class: 'risk-extra' }, icon('undo', { size: 16 }), 'Este servicio se puede revertir solo si algo sale mal.') : h('p', { class: 'risk-extra' }, icon('info', { size: 16 }), 'Este servicio no se revierte solo. Si necesitas deshacerlo, aplica el cambio contrario.')),
        h('section', { class: 'card stack', 'aria-labelledby': 'h-vp' },
          h('h2', { class: 'card-title', id: 'h-vp' }, icon('terminal', { size: 18 }), 'Vista previa'),
          previewBox.childNodes.length ? previewBox : (replaceChildren(previewBox, h('p', { class: 'muted' }, 'Rellena los datos y pulsa «Ver vista previa» para ver exactamente qué se enviará al equipo.')), previewBox)),
        resultBox)));
}
