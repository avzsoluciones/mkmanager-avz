import { h, replaceChildren, relTime, fmtDate, fmtBytes, fmtClock, openDialog, toast } from '../dom.js';
import { icon } from '../icons.js';
import { api, optional } from '../api.js';
import { session } from '../session.js';
import { pageHeader, skeletonList, errorState, statusPill, notice, codeViewer, comingSoon, emptyState, startCountdown, parseTarget, setBusy } from '../components.js';
import { getDeviceStatus, normEstado, bajaEquipo, puedeDarDeBaja } from '../data.js';

const APP_ESTADO = {
  aplicada: { label: 'Aplicada', cls: 'info', icon: 'check' },
  confirmada: { label: 'Confirmada', cls: 'ok', icon: 'check' },
  revertida: { label: 'Revertida', cls: 'warn', icon: 'undo' },
  error: { label: 'Con error', cls: 'bad', icon: 'alert' },
};

export default async function equipo(ctx) {
  const id = ctx.params.id;
  const canWrite = session.canWrite();
  const body = h('div', { class: 'stack-lg' });
  ctx.container.append(body);
  replaceChildren(body, skeletonList(4));

  let dev;
  try { dev = await api.get(`/devices/${encodeURIComponent(id)}`); } catch (e) {
    if (!ctx.alive()) return;
    replaceChildren(body, pageHeader({ title: 'Equipo', back: { href: '#/equipos', label: 'Volver a equipos' } }),
      errorState({ title: e.notFound ? 'No encontramos este equipo' : 'No pudimos cargar el equipo', message: e.notFound ? 'Puede que se haya eliminado o que no tengas permiso para verlo.' : e.message, onRetry: () => ctx.navigate(`/equipos/${id}`) }));
    return;
  }
  if (!ctx.alive()) return;
  ctx.setTitle(dev.nombre);

  /* ----- estado del túnel ----- */
  const pillSlot = h('span', null, statusPill(normEstado(dev.estado)));
  const contactEl = h('p', { class: 'big-line' }, '—');
  const contactSub = h('p', { class: 'muted' }, '');
  const rx = h('strong', null, '—'); const tx = h('strong', null, '—');
  const degradedNote = h('div', null);
  const refreshBtn = h('button', { class: 'btn btn-secondary btn-sm', type: 'button' }, icon('refresh', { size: 16 }), 'Actualizar');

  async function refreshStatus() {
    setBusy(refreshBtn, true, 'Revisando…');
    try {
      const s = await getDeviceStatus(id);
      if (!ctx.alive()) return;
      replaceChildren(pillSlot, statusPill(s.estado));
      contactEl.textContent = s.ultimo_contacto ? relTime(s.ultimo_contacto) : 'Aún no se ha conectado';
      contactSub.textContent = s.ultimo_contacto ? fmtDate(s.ultimo_contacto) : 'Instala el script en el router para que se conecte.';
      rx.textContent = fmtBytes(s.rx_bytes); tx.textContent = fmtBytes(s.tx_bytes);
      replaceChildren(degradedNote, s.degraded ? h('p', { class: 'field-hint' }, 'Los datos de tráfico estarán disponibles cuando el servidor active la revisión en vivo.') : null);
    } catch (e) {
      if (ctx.alive()) toast(e.message, { type: 'error' });
    }
    if (ctx.alive()) setBusy(refreshBtn, false);
  }
  refreshBtn.addEventListener('click', refreshStatus);
  const poll = setInterval(() => { if (document.visibilityState === 'visible') refreshStatus(); }, 20000);
  ctx.onCleanup(() => clearInterval(poll));

  /* ----- script ----- */
  async function openScript(btn) {
    setBusy(btn, true, 'Preparando…');
    let res;
    try { res = await api.get(`/provision/${encodeURIComponent(id)}/script`); } catch (e) {
      setBusy(btn, false);
      toast(e.notFound ? 'Este equipo no tiene datos de instalación guardados.' : e.message, { type: 'error' });
      return;
    }
    setBusy(btn, false);
    const viewer = codeViewer({ text: res.script || '', filename: `${dev.nombre.replace(/[^A-Za-z0-9_.-]+/g, '-')}.rsc`, maxHeight: true });
    res = null;
    openDialog({
      title: 'Script de instalación', wide: true,
      body: [
        notice({ kind: 'warn', title: 'Este script contiene claves', text: 'Úsalo solo para este router. No lo envíes por chat ni lo guardes en documentos compartidos.' }),
        viewer.el,
        h('p', { class: 'field-hint' }, 'Para instalarlo: abre Winbox, entra a «Nuevo terminal», pega el script y pulsa Enter.'),
      ],
      actions: [{ label: 'Cerrar', variant: 'primary', value: true }],
      onClose: () => viewer.wipe(),
    });
  }
  const scriptBtn = h('button', { class: 'btn btn-secondary', type: 'button' }, icon('download', { size: 18 }), 'Volver a descargar el script de instalación');
  scriptBtn.addEventListener('click', () => openScript(scriptBtn));

  /* ----- servicios aplicados ----- */
  const appsBox = h('div', null, skeletonList(2));
  let stoppers = [];
  async function loadApps() {
    stoppers.forEach((s) => s()); stoppers = [];
    let rows;
    try { rows = await optional(() => api.get(`/devices/${encodeURIComponent(id)}/applications`)); } catch (e) {
      if (ctx.alive()) replaceChildren(appsBox, errorState({ message: e.message, onRetry: loadApps }));
      return;
    }
    if (!ctx.alive()) return;
    if (rows == null) { replaceChildren(appsBox, comingSoon('El historial de servicios')); return; }
    if (!rows.length) {
      replaceChildren(appsBox, emptyState({
        iconName: 'sliders', title: 'Todavía no hay servicios aplicados',
        text: 'Aquí verás cada cambio hecho a este equipo, quién lo hizo y si quedó confirmado.',
        action: canWrite ? h('a', { class: 'btn btn-primary', href: `#/servicios?equipo=${encodeURIComponent(id)}` }, 'Aplicar un servicio') : null,
      }));
      return;
    }
    replaceChildren(appsBox, h('ul', { class: 'app-list' }, rows.map((a) => appRow(a))));
  }

  function appRow(a) {
    const meta = APP_ESTADO[a.estado] || { label: a.estado || 'Sin estado', cls: 'muted', icon: 'info' };
    const target = a.estado === 'aplicada' ? parseTarget(a.revertir_en) : null;
    const live = target && target > Date.now();
    const timer = h('span', { class: 'app-timer' });
    const li = h('li', { class: 'app-row' },
      h('div', { class: 'app-main' },
        h('strong', null, a.plantilla || 'Servicio'),
        h('span', { class: 'muted' }, `${fmtDate(a.creado_en)}${a.usuario ? ` · ${a.usuario}` : ''}`)),
      h('span', { class: `pill pill-${meta.cls}` }, icon(meta.icon, { size: 14 }), meta.label),
      live ? timer : null);
    if (live) {
      const btn = h('button', { class: 'btn btn-primary btn-sm', type: 'button' }, 'Confirmar que todo funciona');
      btn.addEventListener('click', async () => {
        setBusy(btn, true, 'Confirmando…');
        try { await api.post(`/applications/${encodeURIComponent(a.id)}/confirm`, {}); toast('Listo, el cambio quedó confirmado.', { type: 'ok' }); loadApps(); }
        catch (e) { setBusy(btn, false); toast(e.message, { type: 'error' }); }
      });
      li.appendChild(btn);
      stoppers.push(startCountdown(target, (s) => { timer.textContent = `Se revierte solo en ${fmtClock(s)}`; }, () => loadApps()));
    }
    return li;
  }
  ctx.onCleanup(() => stoppers.forEach((s) => s()));

  /* ----- baja ----- */
  const canBaja = puedeDarDeBaja(session.role());
  const bajaBtn = h('button', { class: 'btn btn-danger-ghost', type: 'button' }, icon('trash', { size: 18 }), 'Dar de baja');
  bajaBtn.addEventListener('click', async () => {
    setBusy(bajaBtn, true, 'Dando de baja…');
    if (await bajaEquipo(dev)) { ctx.navigate('/equipos'); return; }
    if (ctx.alive()) setBusy(bajaBtn, false);
  });

  /* ----- armado ----- */
  const actions = [];
  if (canWrite) {
    actions.push(h('a', { class: 'btn btn-primary', href: `#/servicios?equipo=${encodeURIComponent(id)}` }, icon('sliders', { size: 18 }), 'Aplicar un servicio'));
    actions.push(scriptBtn);
  }
  if (canBaja) actions.push(bajaBtn);
  replaceChildren(body,
    pageHeader({ title: dev.nombre, subtitle: dev.sede ? `Sede: ${dev.sede}` : null, back: { href: '#/equipos', label: 'Volver a equipos' }, actions }),
    h('div', { class: 'detail-grid' },
      h('section', { class: 'card', 'aria-labelledby': 'h-tunel' },
        h('div', { class: 'card-head' }, h('h2', { class: 'card-title', id: 'h-tunel' }, icon('link', { size: 18 }), 'Túnel seguro AVZ'), refreshBtn),
        h('div', { class: 'kv' }, h('span', { class: 'kv-k' }, 'Estado'), h('span', { class: 'kv-v' }, pillSlot)),
        h('div', { class: 'kv kv-block' }, h('span', { class: 'kv-k' }, 'Último contacto'), contactEl, contactSub),
        h('div', { class: 'kv-pair' },
          h('div', { class: 'kv' }, h('span', { class: 'kv-k' }, 'Recibido'), rx),
          h('div', { class: 'kv' }, h('span', { class: 'kv-k' }, 'Enviado'), tx)),
        degradedNote),
      h('section', { class: 'card', 'aria-labelledby': 'h-red' },
        h('h2', { class: 'card-title', id: 'h-red' }, icon('globe', { size: 18 }), 'Datos de red'),
        h('dl', { class: 'dl' },
          dlRow('IP del túnel', dev.tunnel_ip),
          dlRow('Red local (LAN)', dev.lan_cidr),
          dlRow('Sede', dev.sede),
          dlRow('Tipo', dev.tipo === 'router' || !dev.tipo ? 'Router' : dev.tipo),
          dlRow('Número de equipo', String(dev.id))))),
    h('section', { class: 'card', 'aria-labelledby': 'h-serv' },
      h('h2', { class: 'card-title', id: 'h-serv' }, icon('sliders', { size: 18 }), 'Servicios aplicados'),
      appsBox));

  refreshStatus();
  loadApps();
}

function dlRow(k, v) {
  return h('div', { class: 'dl-row' }, h('dt', null, k), h('dd', null, v || '—'));
}
