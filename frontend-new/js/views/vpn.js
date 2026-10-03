// VPN para personas: crear accesos con código QR y archivo de configuración.
import { h, replaceChildren, toast, fmtDate, openDialog, confirmDialog, copyText, downloadBlob } from '../dom.js';
import { icon } from '../icons.js';
import { api, optional, ApiError } from '../api.js';
import { pageHeader, skeletonList, errorState, comingSoon, emptyState, field, notice, setBusy, applyFieldErrors, focusFirstError } from '../components.js';
import * as net from '../net.js';
import { listSedes } from '../data.js';

/** Panel con QR y botones. El texto de configuración solo vive en memoria mientras el panel está abierto. */
function accessPanel(user, confText) {
  let conf = confText || null;
  let blobUrl = null;
  const qrSlot = h('div', { class: 'qr-slot' }, h('span', { class: 'spinner spinner-lg', 'aria-hidden': 'true' }), h('span', { class: 'sr-only' }, 'Cargando código QR'));

  api.get(`/vpn/users/${encodeURIComponent(user.id)}/qr.png`, { as: 'blob' }).then((blob) => {
    blobUrl = URL.createObjectURL(blob);
    replaceChildren(qrSlot, h('img', { class: 'qr-img', src: blobUrl, alt: `Código QR para configurar el acceso de ${user.nombre}`, width: 240, height: 240 }));
  }).catch((e) => {
    replaceChildren(qrSlot, h('p', { class: 'muted qr-fail' }, icon('alert', { size: 18 }), e.notFound ? 'El código QR todavía no está disponible. Usa el archivo de configuración.' : 'No pudimos cargar el código QR. Usa el archivo de configuración.'));
  });

  async function getConf() {
    if (conf) return conf;
    conf = await api.get(`/vpn/users/${encodeURIComponent(user.id)}/conf`, { as: 'text' });
    return conf;
  }
  const fileName = `${user.nombre.replace(/[^A-Za-z0-9_.-]+/g, '-')}.conf`;
  const dl = h('button', { class: 'btn btn-primary', type: 'button' }, icon('download', { size: 18 }), 'Descargar archivo .conf');
  dl.addEventListener('click', async () => {
    setBusy(dl, true, 'Preparando…');
    try { downloadBlob(fileName, await getConf()); } catch (e) { toast(e.message, { type: 'error' }); }
    setBusy(dl, false);
  });
  const cp = h('button', { class: 'btn btn-secondary', type: 'button' }, icon('copy', { size: 18 }), 'Copiar');
  cp.addEventListener('click', async () => {
    try { toast((await copyText(await getConf())) ? 'Configuración copiada.' : 'No se pudo copiar. Descarga el archivo.', { type: 'ok' }); } catch (e) { toast(e.message, { type: 'error' }); }
  });
  const el = h('div', { class: 'access' },
    qrSlot,
    h('div', { class: 'stack' },
      h('dl', { class: 'dl' },
        h('div', { class: 'dl-row' }, h('dt', null, 'Persona'), h('dd', null, user.nombre)),
        h('div', { class: 'dl-row' }, h('dt', null, 'IP del túnel'), h('dd', null, user.tunnel_ip || '—'))),
      notice({ kind: 'warn', title: 'Contiene una clave personal', text: 'Compártelo solo con esa persona y por un canal privado. Quien lo tenga puede entrar a la red.' }),
      h('ol', { class: 'plain-steps' },
        h('li', null, h('strong', null, 'En el teléfono: '), 'abre la app de túnel seguro y escanea el código QR.'),
        h('li', null, h('strong', null, 'En el computador: '), 'descarga el archivo .conf e impórtalo en la app.')),
      h('div', { class: 'btn-row' }, dl, cp)));
  return {
    el,
    destroy() { if (blobUrl) URL.revokeObjectURL(blobUrl); blobUrl = null; conf = null; replaceChildren(qrSlot); },
  };
}

export default async function vpn(ctx) {
  const body = h('div', { class: 'stack-lg' });
  ctx.container.append(pageHeader({ title: 'VPN para personas', subtitle: 'Da acceso seguro a una persona (por ejemplo, un técnico en terreno) para entrar a la red por el túnel seguro AVZ.' }), body);
  replaceChildren(body, skeletonList(4));

  const panels = new Set();
  ctx.onCleanup(() => { panels.forEach((p) => p.destroy()); panels.clear(); });

  const resultSlot = h('div');
  const listSlot = h('div', { class: 'stack' });

  /* ----- alta ----- */
  let sedes = [];
  try { sedes = await listSedes(); } catch { /* sin sedes: el servidor responderá si hace falta */ }
  if (!ctx.alive()) return;
  const sedeSel = sedes.length > 1 ? field({
    id: 'vpn-sede', label: 'Sede', type: 'select', required: true,
    hint: 'La red de esta sede es la que podrá ver la persona.',
    options: [{ value: '', label: 'Elige una sede…' }, ...sedes.map((x) => ({ value: String(x.id), label: x.ciudad ? `${x.nombre} · ${x.ciudad}` : x.nombre }))],
  }) : null;
  const nombre = field({ id: 'vpn-nombre', label: 'Nombre de la persona', required: true, placeholder: 'Ej. Carlos Pérez', maxlength: 60, autocomplete: 'off', hint: 'Así la reconocerás en la lista.' });
  const rutas = field({ id: 'vpn-rutas', label: 'Redes a las que podrá llegar', type: 'textarea', rows: 3, placeholder: '192.168.10.0/24', spellcheck: false, hint: 'Una por línea, con el formato 192.168.10.0/24. Si lo dejas vacío, usará el acceso básico definido por AVZ.' });
  const slot = h('div', { 'aria-live': 'assertive' });
  const submit = h('button', { class: 'btn btn-primary', type: 'submit' }, icon('plus', { size: 18 }), 'Crear acceso');
  const form = h('form', { class: 'card stack', novalidate: '' },
    h('h2', { class: 'card-title' }, icon('key', { size: 18 }), 'Nueva VPN para una persona'),
    h('div', { class: 'form-grid' }, sedeSel ? sedeSel.el : null, nombre.el, rutas.el), slot, h('div', null, submit));
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    slot.replaceChildren(); nombre.setError(''); rutas.setError(''); if (sedeSel) sedeSel.setError('');
    const n = nombre.value.trim();
    if (n.length < 2) nombre.setError('Escribe el nombre de la persona (mínimo 2 letras).');
    const list = rutas.value.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
    const badRoute = list.find((r) => !net.parseCidr(r));
    if (badRoute) rutas.setError(`«${badRoute}» no es una red válida. Usa el formato 192.168.10.0/24.`);
    if (sedeSel && !sedeSel.value) sedeSel.setError('Elige la sede a la que tendrá acceso.');
    if (focusFirstError([sedeSel, nombre, rutas].filter(Boolean))) return;
    setBusy(submit, true, 'Creando…');
    try {
      const body2 = { nombre: n };
      if (list.length) body2.rutas = list;
      if (sedeSel) body2.sede_id = Number(sedeSel.value);
      const created = await api.post('/vpn/users', body2);
      if (!ctx.alive()) return;
      nombre.value = ''; rutas.value = '';
      const panel = accessPanel(created, created.conf_text);
      created.conf_text = null;
      panels.add(panel);
      const done = h('button', { class: 'btn btn-secondary', type: 'button' }, icon('check', { size: 16 }), 'Listo, ya lo guardé');
      done.addEventListener('click', () => { panel.destroy(); panels.delete(panel); replaceChildren(resultSlot); });
      replaceChildren(resultSlot, h('section', { class: 'card stack success-card-col', 'aria-labelledby': 'h-vpn-ok' },
        h('div', { class: 'card-head' }, h('h2', { class: 'card-title', id: 'h-vpn-ok' }, icon('check', { size: 18 }), `Acceso creado para ${created.nombre}`), done),
        panel.el));
      resultSlot.scrollIntoView({ behavior: 'smooth', block: 'start' });
      toast('Acceso creado.', { type: 'ok' });
      loadList();
    } catch (e) {
      if (!ctx.alive()) return;
      if (e instanceof ApiError && (e.status === 422 || e.status === 409)) {
        const rest = applyFieldErrors(e, { nombre, rutas, ...(sedeSel ? { sede_id: sedeSel } : {}) });
        if (rest.length || !Object.keys(e.fields).length) slot.replaceChildren(notice({ kind: 'bad', title: e.status === 409 ? 'No se pudo crear el acceso' : 'Revisa los datos', text: rest.join(' ') || e.message }));
      } else slot.replaceChildren(notice({ kind: 'bad', title: 'No se pudo crear el acceso', text: e.message }));
    }
    if (ctx.alive()) setBusy(submit, false);
  });

  /* ----- lista ----- */
  async function loadList() {
    let rows;
    try { rows = await optional(() => api.get('/vpn/users')); } catch (e) {
      if (ctx.alive()) replaceChildren(listSlot, errorState({ message: e.message, onRetry: loadList }));
      return;
    }
    if (!ctx.alive()) return;
    if (rows == null) { replaceChildren(listSlot, comingSoon('Los accesos VPN para personas')); form.hidden = true; return; }
    if (!rows.length) {
      replaceChildren(listSlot, emptyState({ iconName: 'key', title: 'Aún no hay accesos', text: 'Crea el primero con el formulario de arriba. Recibirás un código QR y un archivo para la persona.' }));
      return;
    }
    replaceChildren(listSlot, h('h2', { class: 'card-title' }, icon('users', { size: 18 }), 'Accesos creados'),
      h('ul', { class: 'rows' }, rows.map((u) => h('li', null,
        h('div', { class: `row-card row-static${u.revocado ? ' is-revoked' : ''}` },
          h('span', { class: 'row-ico' }, icon('user', { size: 22 })),
          h('span', { class: 'row-main' }, h('strong', { class: 'row-title' }, u.nombre), h('span', { class: 'row-sub' }, `IP ${u.tunnel_ip || '—'} · creado ${fmtDate(u.creado_en)}`)),
          h('span', { class: 'row-status' }, u.revocado ? h('span', { class: 'pill pill-muted' }, icon('ban', { size: 14 }), 'Revocado') : h('span', { class: 'pill pill-ok' }, icon('check', { size: 14 }), 'Activo')),
          u.revocado ? h('span') : h('span', { class: 'row-actions' },
            h('button', { class: 'btn btn-secondary btn-sm', type: 'button', onClick: () => viewAccess(u) }, icon('qr', { size: 16 }), 'Ver acceso'),
            h('button', { class: 'btn btn-danger-ghost btn-sm', type: 'button', onClick: () => revoke(u) }, icon('trash', { size: 16 }), 'Revocar')))))));
  }

  function viewAccess(u) {
    const panel = accessPanel(u, null);
    panels.add(panel);
    openDialog({
      title: `Acceso de ${u.nombre}`, wide: true, body: panel.el,
      actions: [{ label: 'Cerrar', variant: 'primary', value: true }],
      onClose: () => { panel.destroy(); panels.delete(panel); },
    });
  }

  async function revoke(u) {
    const ok = await confirmDialog({
      title: `¿Revocar el acceso de ${u.nombre}?`,
      message: 'La persona dejará de poder entrar a la red de inmediato. Esta acción no se puede deshacer; si más adelante la necesita, tendrás que crear un acceso nuevo.',
      confirmLabel: 'Sí, revocar acceso', danger: true,
    });
    if (!ok) return;
    try { await api.del(`/vpn/users/${encodeURIComponent(u.id)}`); toast(`Acceso de ${u.nombre} revocado.`, { type: 'ok' }); loadList(); } catch (e) { toast(e.message, { type: 'error' }); }
  }

  replaceChildren(body, form, resultSlot, listSlot);
  replaceChildren(listSlot, skeletonList(3));
  await loadList();
}
