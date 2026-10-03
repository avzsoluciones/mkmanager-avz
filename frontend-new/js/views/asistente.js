// Asistente "Nuevo router": 5 pasos. El script contiene claves: vive solo en memoria y se borra al salir.
import { h, replaceChildren, clear, toast, fmtClock } from '../dom.js';
import { icon } from '../icons.js';
import { api, ApiError } from '../api.js';
import {
  pageHeader, field, radioCards, notice, codeViewer, errorState, skeletonList, setBusy, applyFieldErrors, focusFirstError, emptyState,
} from '../components.js';
import { listEmpresas, listSedes, listDevices, getDeviceStatus } from '../data.js';
import * as net from '../net.js';
import { topology, installIllustration } from './wizard-art.js';
import { openSedeDialog } from './sede-dialog.js';
import { openEmpresaDialog } from './empresa-dialog.js';

const STEPS = ['Sede y nombre', 'Internet', 'Red local', 'Revisión', 'Instalar'];
const IFACE_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$/;
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9 _.-]{1,59}$/;
const WAN_MODES = [
  { value: 'dhcp', title: 'Automático (DHCP)', desc: 'El proveedor de internet entrega la dirección solo. Es lo más común.', icon: 'refresh' },
  { value: 'static', title: 'IP fija', desc: 'Tienes una dirección y una puerta de enlace que te dio el proveedor.', icon: 'pin' },
  { value: 'pppoe', title: 'PPPoE ya configurado', desc: 'La conexión con usuario y clave ya existe en el router.', icon: 'link' },
];

export default async function asistente(ctx) {
  const S = {
    step: 0, empresas: [], sedes: [], devices: [],
    empresa_id: '', sede_id: ctx.query.get('sede') || '', nombre: '',
    wan: { interface: 'ether1', mode: 'dhcp', address: '', gateway: '' },
    lan: { interface: 'bridge', cidr: '', gateway: '', dhcp_start: '', dhcp_end: '', dns: '1.1.1.1, 8.8.8.8', gwTouched: false, dhcpTouched: false },
    preview: null, created: null, serverErrors: {},
  };
  let stopPolling = null;
  let hideTimer = null;
  let viewer = null;
  const wipe = () => {
    if (viewer) viewer.wipe();
    if (S.created) S.created.script = '';
    S.created = null; S.preview = null;
    clearTimeout(hideTimer);
    if (stopPolling) stopPolling();
  };
  ctx.onCleanup(wipe);

  const host = h('div', { class: 'stack-lg wizard' });
  ctx.container.append(host);
  replaceChildren(host, skeletonList(4));

  try {
    const [empresas, sedes, devices] = await Promise.all([listEmpresas(), listSedes(), listDevices().catch(() => [])]);
    S.empresas = empresas; S.sedes = sedes; S.devices = devices;
    // Si llega con una sede preseleccionada, la empresa sale de ella.
    const pre = sedes.find((x) => String(x.id) === String(S.sede_id));
    if (pre) S.empresa_id = String(pre.empresa_id);
    else if (empresas.length === 1) S.empresa_id = String(empresas[0].id);
  } catch (e) {
    if (!ctx.alive()) return;
    replaceChildren(host, pageHeader({ title: 'Nuevo router' }), errorState({ message: e.message, onRetry: () => ctx.navigate('/nuevo-router') }));
    return;
  }
  if (!ctx.alive()) return;
  S.lan.cidr = net.suggestFreeLan(S.devices.map((d) => d.lan_cidr).filter(Boolean));
  syncLanDefaults(true);

  /* ---------- armazón ---------- */
  const progressFill = h('span', { class: 'progress-fill' });
  const stepper = h('ol', { class: 'stepper' });
  const stage = h('div', { class: 'wizard-stage' });
  const diagram = topology();

  function paintStepper() {
    replaceChildren(stepper, STEPS.map((label, i) => h('li', {
      class: `step${i < S.step ? ' is-done' : ''}${i === S.step ? ' is-current' : ''}`,
      'aria-current': i === S.step ? 'step' : null,
    }, h('span', { class: 'step-dot' }, i < S.step ? icon('check', { size: 14 }) : String(i + 1)), h('span', { class: 'step-label' }, label))));
    progressFill.style.setProperty('--w', `${(S.step / (STEPS.length - 1)) * 100}%`);
  }

  replaceChildren(host,
    pageHeader({ title: 'Nuevo router', subtitle: 'Te guiamos paso a paso. Al final tendrás un script para pegar en el router.', back: { href: '#/equipos', label: 'Volver a equipos' } }),
    h('div', { class: 'progress', role: 'progressbar', 'aria-valuemin': '1', 'aria-valuemax': String(STEPS.length), 'aria-label': 'Progreso del asistente' }, progressFill),
    stepper, stage);

  function go(step, { keepErrors = false } = {}) {
    if (!keepErrors) S.serverErrors = {};
    S.step = step;
    paintStepper();
    document.querySelector('.progress')?.setAttribute('aria-valuenow', String(step + 1));
    renderStep();
    const first = stage.querySelector('h2');
    if (first) { first.setAttribute('tabindex', '-1'); first.focus({ preventScroll: false }); }
  }

  function renderStep() {
    clear(stage);
    ([step1, step2, step3, step4, step5][S.step])();
  }

  const withDiagram = (form) => h('div', { class: 'wizard-cols' }, h('div', { class: 'card wizard-form' }, form), h('aside', { class: 'card wizard-aside', 'aria-label': 'Vista del diagrama' }, h('h3', { class: 'card-title' }, icon('layers', { size: 18 }), 'Así quedará la red'), diagram.el));

  const navBar = ({ back, next, nextLabel = 'Continuar', nextIcon = 'arrow-right' }) => {
    const nextBtn = h('button', { class: 'btn btn-primary btn-lg', type: 'submit' }, nextLabel, icon(nextIcon, { size: 18 }));
    return { el: h('div', { class: 'wizard-nav' }, back ? h('button', { class: 'btn btn-secondary btn-lg', type: 'button', onClick: back }, icon('arrow-left', { size: 18 }), 'Atrás') : h('span'), nextBtn), nextBtn };
  };

  const serverErr = (map) => { applyFieldErrors({ fields: S.serverErrors }, map); };

  /* ---------- paso 1: empresa, sede y nombre ---------- */
  function step1() {
    const sedesDe = (eid) => S.sedes.filter((x) => String(x.empresa_id) === String(eid));
    const multi = new Set(S.empresas.map((e) => e.aliado)).size > 1;
    const empresaSel = field({
      id: 'w-empresa', label: 'Empresa', type: 'select', required: true,
      hint: 'Primero la empresa (el cliente). Después elegirás una de sus sedes.',
      options: [{ value: '', label: S.empresas.length ? 'Elige una empresa…' : 'Todavía no hay empresas' }, ...S.empresas.map((e) => ({ value: String(e.id), label: multi && e.aliado ? `${e.nombre} · ${e.aliado}` : e.nombre }))],
      value: S.empresa_id,
    });
    const sedeSel = field({
      id: 'w-sede', label: 'Sede', type: 'select', required: true,
      hint: 'El lugar donde se instalará el router.',
      options: [{ value: '', label: 'Elige una sede…' }],
      value: '',
    });
    function fillSedes() {
      const list = S.empresa_id ? sedesDe(S.empresa_id) : [];
      sedeSel.input.replaceChildren(...[{ value: '', label: !S.empresa_id ? 'Primero elige la empresa' : list.length ? 'Elige una sede…' : 'Esta empresa no tiene sedes' }, ...list.map((x) => ({ value: String(x.id), label: `${x.nombre} · ${x.ciudad}` }))]
        .map((o) => h('option', { value: o.value }, o.label)));
      sedeSel.input.disabled = !S.empresa_id;
      if (!list.some((x) => String(x.id) === String(S.sede_id))) S.sede_id = list.length === 1 ? String(list[0].id) : '';
      sedeSel.input.value = S.sede_id;
    }
    const nombre = field({ id: 'w-nombre', label: 'Nombre del router', required: true, value: S.nombre, placeholder: 'Ej. Router tienda principal', maxlength: 60, autocomplete: 'off', hint: 'Letras, números, espacios, punto, guion o guion bajo.' });
    const newEmpresa = h('button', { class: 'btn btn-secondary', type: 'button', onClick: () => openEmpresaDialog(async (emp) => {
      S.empresas = await listEmpresas().catch(() => S.empresas);
      S.empresa_id = String(emp.id); S.sede_id = '';
      renderStep();
    }) }, icon('plus', { size: 16 }), 'Crear una empresa nueva');
    const newSede = h('button', { class: 'btn btn-secondary', type: 'button', onClick: () => {
      if (!S.empresa_id) { empresaSel.setError('Elige primero la empresa a la que pertenecerá la sede.'); empresaSel.focus(); return; }
      openSedeDialog(async (sede) => {
        S.sedes = await listSedes().catch(() => S.sedes);
        S.sede_id = String(sede.id);
        renderStep();
      }, { empresaId: S.empresa_id });
    } }, icon('plus', { size: 16 }), 'Crear una sede nueva');
    const nav = navBar({ back: null });
    const form = h('form', { class: 'stack', novalidate: '' },
      h('h2', { class: 'step-title' }, '¿Para quién, dónde y cómo se llama?'),
      h('p', { class: 'step-intro' }, 'Elige la empresa, luego su sede, y ponle un nombre fácil de reconocer al router.'),
      empresaSel.el, S.empresas.length ? null : emptyState({ iconName: 'building', title: 'Primero necesitas una empresa', text: 'Crea la empresa del cliente; después podrás crear sus sedes y sus routers.' }),
      newEmpresa, sedeSel.el, newSede, nombre.el, nav.el);
    fillSedes();
    empresaSel.input.addEventListener('change', () => { S.empresa_id = empresaSel.value; S.sede_id = ''; fillSedes(); diagram.update(S); });
    sedeSel.input.addEventListener('change', () => { S.sede_id = sedeSel.value; diagram.update(S); });
    nombre.input.addEventListener('input', () => { S.nombre = nombre.value; diagram.update(S); });
    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      empresaSel.setError(''); sedeSel.setError(''); nombre.setError('');
      if (!empresaSel.value) empresaSel.setError('Elige la empresa. Si no existe, créala con el botón de abajo.');
      else if (!sedeSel.value) sedeSel.setError('Elige la sede donde se instalará. Si no existe, créala con el botón de abajo.');
      const n = nombre.value.trim();
      if (!NAME_RE.test(n)) nombre.setError(n.length < 2 ? 'Escribe un nombre de al menos 2 caracteres.' : 'Usa solo letras, números, espacios, punto, guion o guion bajo, y empieza con una letra o número.');
      if (focusFirstError([empresaSel, sedeSel, nombre])) return;
      S.empresa_id = empresaSel.value; S.sede_id = sedeSel.value; S.nombre = n;
      go(1);
    });
    stage.appendChild(withDiagram(form));
    serverErr({ sede_id: sedeSel, nombre });
    diagram.update(S);
  }

  /* ---------- paso 2: internet ---------- */
  function step2() {
    const iface = field({ id: 'w-wan-if', label: 'Puerto donde llega internet', required: true, value: S.wan.interface, list: 'w-wan-list', autocomplete: 'off', spellcheck: false, maxlength: 32 });
    const dl = h('datalist', { id: 'w-wan-list' }, ['ether1', 'ether2', 'sfp1', 'sfp-sfpplus1', 'pppoe-out1', 'lte1'].map((v) => h('option', { value: v })));
    const addr = field({ id: 'w-wan-addr', label: 'Dirección IP fija con máscara', required: true, value: S.wan.address, placeholder: '203.0.113.10/29', autocomplete: 'off', spellcheck: false, hint: 'Se escribe como 203.0.113.10/29. La máscara viene después de la barra.' });
    const gw = field({ id: 'w-wan-gw', label: 'Puerta de enlace', required: true, value: S.wan.gateway, placeholder: '203.0.113.9', autocomplete: 'off', spellcheck: false, hint: 'La dirección del equipo del proveedor al que se conecta tu router.' });
    const extra = h('div', { class: 'stack' });
    const modes = radioCards({ name: 'wan-mode', legend: '¿Cómo recibe internet este router?', options: WAN_MODES, value: S.wan.mode, onChange: (v) => { S.wan.mode = v; paintExtra(); diagram.update(S); } });

    function paintExtra() {
      if (S.wan.mode === 'static') replaceChildren(extra, addr.el, gw.el);
      else if (S.wan.mode === 'pppoe') replaceChildren(extra, notice({ kind: 'info', title: 'Antes de instalar', text: 'La conexión PPPoE (usuario y clave del proveedor) debe estar creada en el router con el nombre que escribas arriba, por ejemplo «pppoe-out1». Este asistente no la crea.' }));
      else replaceChildren(extra, notice({ kind: 'ok', title: 'No tienes que hacer nada más', text: 'El router pedirá su dirección al proveedor automáticamente.' }));
      iface.el.querySelector('label').firstChild.textContent = S.wan.mode === 'pppoe' ? 'Nombre de la conexión PPPoE' : 'Puerto donde llega internet';
    }
    iface.input.addEventListener('input', () => { S.wan.interface = iface.value.trim(); diagram.update(S); });
    addr.input.addEventListener('input', () => { S.wan.address = addr.value.trim(); });
    gw.input.addEventListener('input', () => { S.wan.gateway = gw.value.trim(); });
    const nav = navBar({ back: () => go(0) });
    const form = h('form', { class: 'stack', novalidate: '' },
      h('h2', { class: 'step-title' }, 'Conexión a internet'),
      h('p', { class: 'step-intro' }, 'Indica por dónde entra internet al local y cómo la entrega el proveedor.'),
      modes.el, iface.el, dl, extra, nav.el);
    paintExtra();
    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      [iface, addr, gw].forEach((f) => f.setError(''));
      const fields = [iface];
      if (!IFACE_RE.test(iface.value.trim())) iface.setError('Escribe el nombre del puerto, por ejemplo ether1. No uses espacios ni símbolos raros.');
      if (S.wan.mode === 'static') {
        fields.push(addr, gw);
        const c = net.parseCidr(addr.value);
        if (!c || c.prefix < 1 || c.prefix > 32) addr.setError('Escribe la IP con su máscara, por ejemplo 203.0.113.10/29.');
        const gerr = net.checkIp(gw.value, 'La puerta de enlace');
        if (gerr) gw.setError(gerr);
        else if (c && !net.inNet({ network: c.network, broadcast: c.broadcast }, net.ipToInt(gw.value))) gw.setError('La puerta de enlace debería estar dentro de la misma red que tu IP. Revisa los datos del proveedor.');
      }
      if (focusFirstError(fields)) return;
      S.wan.interface = iface.value.trim(); S.wan.address = addr.value.trim(); S.wan.gateway = gw.value.trim();
      go(2);
    });
    stage.appendChild(withDiagram(form));
    serverErr({ 'wan.interface': iface, 'wan.address': addr, 'wan.gateway': gw, 'wan.mode': iface });
    diagram.update(S);
  }

  /* ---------- paso 3: red local ---------- */
  function syncLanDefaults(force = false) {
    const c = net.parseCidr(S.lan.cidr);
    if (!c || !c.isNetwork) return;
    if (force || !S.lan.gwTouched) S.lan.gateway = net.intToIp(net.firstHost(c));
    if (force || !S.lan.dhcpTouched) {
      const [a, b] = net.defaultDhcp(c);
      S.lan.dhcp_start = net.intToIp(a); S.lan.dhcp_end = net.intToIp(b);
    }
  }

  function lanErrors() {
    const e = {};
    const L = S.lan;
    if (!IFACE_RE.test(L.interface.trim())) e.interface = 'Escribe el nombre del bridge o puerto, por ejemplo bridge o ether2.';
    const c = net.parseCidr(L.cidr);
    if (!c) e.cidr = 'Escribe la red con su máscara, por ejemplo 192.168.10.0/24.';
    else if (!c.isNetwork) e.cidr = `Debe ser la dirección de la red, no de un equipo. Prueba con ${net.intToIp(c.network)}/${c.prefix}.`;
    else if (!net.isPrivateNet(c)) e.cidr = 'Usa una red privada: empieza por 192.168., 10. o 172.16. a 172.31.';
    else if (c.prefix < 16 || c.prefix > 29) e.cidr = 'La máscara debe estar entre /16 y /29. Lo habitual es /24.';
    else {
      const res = net.RESERVED.find((r) => net.overlaps(c, r.net));
      if (res) e.cidr = `Esta red choca con ${res.label} (${res.cidr}). Elige otra, por ejemplo ${net.suggestFreeLan(S.devices.map((d) => d.lan_cidr).filter(Boolean))}.`;
      else {
        const other = S.devices.find((d) => d.lan_cidr && net.parseCidr(d.lan_cidr) && net.overlaps(c, net.parseCidr(d.lan_cidr)));
        if (other) e.cidr = `Esta red se cruza con la del equipo «${other.nombre}» (${other.lan_cidr}). Si dos sedes usan la misma red, no se podrán ver por el túnel. Usa otra.`;
      }
    }
    if (c && !e.cidr) {
      const g = net.ipToInt(L.gateway);
      if (g == null) e.gateway = 'Escribe una IP válida, por ejemplo ' + net.intToIp(net.firstHost(c)) + '.';
      else if (!net.inNet(c, g) || g === c.network || g === c.broadcast) e.gateway = `La IP del router debe estar dentro de ${L.cidr.trim()}.`;
      const a = net.ipToInt(L.dhcp_start), b = net.ipToInt(L.dhcp_end);
      if (a == null) e.dhcp_start = 'Escribe una IP válida.';
      else if (!net.inNet(c, a)) e.dhcp_start = 'Debe estar dentro de la red local.';
      if (b == null) e.dhcp_end = 'Escribe una IP válida.';
      else if (!net.inNet(c, b)) e.dhcp_end = 'Debe estar dentro de la red local.';
      if (a != null && b != null && !e.dhcp_start && !e.dhcp_end) {
        if (a > b) e.dhcp_end = 'El final del rango debe ser mayor que el inicio.';
        else if (g != null && g >= a && g <= b) e.dhcp_start = 'El rango no debe incluir la IP del router.';
      }
    }
    const dns = parseDns(L.dns);
    if (!dns.length || dns.length > 3) e.dns = 'Indica entre 1 y 3 servidores DNS separados por coma, por ejemplo 1.1.1.1, 8.8.8.8.';
    else if (dns.some((d) => net.ipToInt(d) == null)) e.dns = 'Hay una dirección DNS que no es válida. Usa el formato 1.1.1.1.';
    return e;
  }
  const parseDns = (t) => String(t || '').split(/[\s,;]+/).filter(Boolean);

  function step3() {
    const F = {
      interface: field({ id: 'w-lan-if', label: 'Puerto o bridge de la red local', required: true, value: S.lan.interface, list: 'w-lan-list', autocomplete: 'off', spellcheck: false, maxlength: 32, hint: 'Un bridge junta varios puertos en una sola red. Si no sabes, deja «bridge».' }),
      cidr: field({ id: 'w-lan-cidr', label: 'Red local (subred)', required: true, value: S.lan.cidr, placeholder: '192.168.10.0/24', autocomplete: 'off', spellcheck: false, hint: 'Cada sede necesita una red distinta para poder verse por el túnel.' }),
      gateway: field({ id: 'w-lan-gw', label: 'IP del router en la red local', required: true, value: S.lan.gateway, autocomplete: 'off', spellcheck: false, hint: 'Es la dirección que usarán los equipos como puerta de salida.' }),
      dhcp_start: field({ id: 'w-lan-ds', label: 'Direcciones automáticas: desde', required: true, value: S.lan.dhcp_start, autocomplete: 'off', spellcheck: false }),
      dhcp_end: field({ id: 'w-lan-de', label: 'hasta', required: true, value: S.lan.dhcp_end, autocomplete: 'off', spellcheck: false }),
      dns: field({ id: 'w-lan-dns', label: 'Servidores DNS', required: true, value: S.lan.dns, autocomplete: 'off', spellcheck: false, hint: 'Traducen nombres de páginas a direcciones. Separa con coma. Puedes dejar los sugeridos.' }),
    };
    const dl = h('datalist', { id: 'w-lan-list' }, ['bridge', 'bridge-lan', 'ether2', 'ether3'].map((v) => h('option', { value: v })));
    const touched = new Set();

    function recompute(changedKey) {
      if (changedKey) touched.add(changedKey);
      S.lan.interface = F.interface.value.trim();
      S.lan.cidr = F.cidr.value.trim();
      if (changedKey === 'gateway') S.lan.gwTouched = true;
      if (changedKey === 'dhcp_start' || changedKey === 'dhcp_end') S.lan.dhcpTouched = true;
      if (changedKey === 'cidr') {
        syncLanDefaults();
        F.gateway.value = S.lan.gateway; F.dhcp_start.value = S.lan.dhcp_start; F.dhcp_end.value = S.lan.dhcp_end;
      } else {
        S.lan.gateway = F.gateway.value.trim(); S.lan.dhcp_start = F.dhcp_start.value.trim(); S.lan.dhcp_end = F.dhcp_end.value.trim();
      }
      S.lan.dns = F.dns.value;
      diagram.update(S);
      const errs = lanErrors();
      for (const [k, f] of Object.entries(F)) f.setError(touched.has(k) || touched.has('all') ? errs[k] || '' : '');
      return errs;
    }
    for (const [k, f] of Object.entries(F)) f.input.addEventListener('input', () => recompute(k));

    const suggest = h('button', { class: 'btn btn-secondary btn-sm', type: 'button', onClick: () => {
      F.cidr.value = net.suggestFreeLan(S.devices.map((d) => d.lan_cidr).filter(Boolean));
      S.lan.gwTouched = false; S.lan.dhcpTouched = false;
      recompute('cidr');
    } }, icon('sparkle', { size: 16 }), 'Sugerir una red libre');
    const nav = navBar({ back: () => go(1) });
    const form = h('form', { class: 'stack', novalidate: '' },
      h('h2', { class: 'step-title' }, 'Red local del cliente'),
      h('p', { class: 'step-intro' }, 'Es la red a la que se conectan los equipos del local (computadores, teléfonos, cámaras). Ya sugerimos valores seguros.'),
      F.interface.el, dl,
      h('div', null, F.cidr.el, h('div', { class: 'field-extra' }, suggest)),
      F.gateway.el,
      h('div', { class: 'form-grid' }, F.dhcp_start.el, F.dhcp_end.el),
      F.dns.el, nav.el);
    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      touched.add('all');
      const errs = recompute();
      if (Object.keys(errs).length) { focusFirstError(Object.values(F)); return; }
      S.lan.dns = parseDns(F.dns.value).join(', ');
      go(3);
    });
    stage.appendChild(withDiagram(form));
    recompute();
    // con el primer pintado no se muestran errores hasta que el usuario toque el campo
    Object.values(F).forEach((f) => f.setError(''));
    serverErr({ 'lan.interface': F.interface, 'lan.cidr': F.cidr, 'lan.gateway': F.gateway, 'lan.dhcp_start': F.dhcp_start, 'lan.dhcp_end': F.dhcp_end, 'lan.dns': F.dns });
  }

  /* ---------- paso 4: revisión ---------- */
  function buildPayload() {
    const wan = { interface: S.wan.interface, mode: S.wan.mode };
    if (S.wan.mode === 'static') { wan.address = S.wan.address; wan.gateway = S.wan.gateway; }
    const lan = { interface: S.lan.interface, cidr: S.lan.cidr, dns: parseDns(S.lan.dns) };
    if (S.lan.gateway) lan.gateway = S.lan.gateway;
    if (S.lan.dhcp_start && S.lan.dhcp_end) { lan.dhcp_start = S.lan.dhcp_start; lan.dhcp_end = S.lan.dhcp_end; }
    return { sede_id: Number(S.sede_id), nombre: S.nombre, wan, lan };
  }

  function stepForError(err) {
    const keys = Object.keys(err.fields || {});
    if (keys.some((k) => k.startsWith('lan'))) return 2;
    if (keys.some((k) => k.startsWith('wan'))) return 1;
    if (keys.some((k) => k === 'sede_id' || k === 'nombre')) return 0;
    const m = String(err.message || '').toLowerCase();
    if (/wan|internet/.test(m)) return 1;
    if (/sede|nombre/.test(m)) return 0;
    return 2;
  }

  function localWarnings() {
    const w = [];
    const c = net.parseCidr(S.lan.cidr);
    if (c && c.prefix >= 26) w.push(`La red local es pequeña: caben unos ${Math.max(c.size - 2, 0)} equipos.`);
    if (c && c.prefix <= 20) w.push('La red local es muy grande. Para un local normal alcanza con una /24.');
    if (S.wan.mode === 'pppoe') w.push(`Recuerda: la conexión PPPoE «${S.wan.interface}» debe existir ya en el router, si no el local se quedará sin internet.`);
    if (S.wan.mode === 'static') w.push('Con IP fija, confirma con el proveedor la dirección y la puerta de enlace antes de pegar el script.');
    return w;
  }

  async function step4() {
    const box = h('div', { class: 'card stack' }, h('h2', { class: 'step-title' }, 'Revisa antes de crear'), skeletonList(3));
    stage.appendChild(box);
    let res;
    try { res = await api.post('/provision/preview', buildPayload()); } catch (e) {
      if (!ctx.alive() || S.step !== 3) return;
      const backTo = e instanceof ApiError && (e.status === 422 || e.status === 404) ? stepForError(e) : null;
      if (e instanceof ApiError && e.status === 422) S.serverErrors = e.fields || {};
      replaceChildren(box,
        h('h2', { class: 'step-title' }, 'Hay algo que corregir'),
        notice({ kind: 'bad', title: backTo != null ? 'No pudimos preparar el router' : 'No pudimos generar la vista previa', text: friendlyMessage(e) }),
        h('div', { class: 'wizard-nav' },
          h('button', { class: 'btn btn-secondary btn-lg', type: 'button', onClick: () => go(2, { keepErrors: true }) }, icon('arrow-left', { size: 18 }), 'Atrás'),
          backTo != null
            ? h('button', { class: 'btn btn-primary btn-lg', type: 'button', onClick: () => go(backTo, { keepErrors: true }) }, 'Corregir ahora', icon('arrow-right', { size: 18 }))
            : h('button', { class: 'btn btn-primary btn-lg', type: 'button', onClick: () => go(3) }, icon('refresh', { size: 18 }), 'Reintentar')));
      return;
    }
    if (!ctx.alive() || S.step !== 3) return;
    S.preview = res;
    const avisos = [...localWarnings(), ...(Array.isArray(res.avisos) ? res.avisos : [])];
    const sede = S.sedes.find((x) => String(x.id) === String(S.sede_id));
    const modeLabel = { dhcp: 'Automático (DHCP)', static: `IP fija ${S.wan.address}`, pppoe: 'PPPoE ya configurado' }[S.wan.mode];
    const dhcp = Array.isArray(res.dhcp) ? `${res.dhcp[0]} a ${res.dhcp[1]}` : '—';
    const prev = codeViewer({ text: res.script || '', filename: 'vista-previa.rsc', label: 'Vista previa del script', allowCopy: false, allowDownload: false });
    const nav = navBar({ back: () => go(2), nextLabel: 'Crear router', nextIcon: 'check' });
    const form = h('form', { class: 'stack-lg', novalidate: '' },
      h('div', { class: 'card stack' },
        h('h2', { class: 'step-title' }, 'Revisa antes de crear'),
        h('p', { class: 'step-intro' }, 'Este es el plan. Todavía no se ha creado nada: hasta que pulses «Crear router» puedes volver y cambiar lo que quieras.'),
        h('div', { class: 'plan-grid' },
          planItem('Sede', sede ? sede.nombre : '—', 'building', () => go(0)),
          planItem('Nombre', S.nombre, 'router', () => go(0)),
          planItem('IP de túnel asignada', res.tunnel_ip, 'link', null, true),
          planItem('Internet', `${S.wan.interface} · ${modeLabel}`, 'globe', () => go(1)),
          planItem('Red local', `${S.lan.cidr} (${S.lan.interface})`, 'layers', () => go(2)),
          planItem('IP del router en la red local', res.lan_gateway || S.lan.gateway, 'pin', () => go(2)),
          planItem('Direcciones automáticas', dhcp, 'users', () => go(2)),
          planItem('DNS', S.lan.dns, 'server', () => go(2))),
        avisos.length ? notice({ kind: 'warn', title: avisos.length === 1 ? 'Un aviso' : `${avisos.length} avisos`, children: h('ul', { class: 'notice-list' }, avisos.map((a) => h('li', null, a))) }) : notice({ kind: 'ok', title: 'Todo se ve bien', text: 'No encontramos choques de red ni datos raros.' })),
      h('section', { class: 'card stack', 'aria-labelledby': 'h-prev' },
        h('h2', { class: 'card-title', id: 'h-prev' }, icon('terminal', { size: 18 }), 'Vista previa del script'),
        h('p', { class: 'field-hint' }, 'Las partes entre < > son marcadores: las claves reales se generan al crear el router.'),
        prev.el),
      h('div', { class: 'card' }, nav.el));
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      setBusy(nav.nextBtn, true, 'Creando…');
      try {
        const out = await api.post('/provision', buildPayload());
        if (!ctx.alive()) return;
        S.created = { device: out.device || {}, script: out.script || '' };
        S.preview = null;
        go(4);
      } catch (e) {
        if (!ctx.alive()) return;
        setBusy(nav.nextBtn, false);
        if (e instanceof ApiError && e.status === 422) { S.serverErrors = e.fields || {}; toast(friendlyMessage(e), { type: 'error' }); go(stepForError(e), { keepErrors: true }); return; }
        toast(friendlyMessage(e), { type: 'error' });
      }
    });
    replaceChildren(stage, form);
  }

  function planItem(k, v, ic, onEdit, accent = false) {
    return h('div', { class: `plan-item${accent ? ' is-accent' : ''}` },
      h('span', { class: 'plan-ico' }, icon(ic, { size: 18 })),
      h('div', { class: 'plan-text' }, h('span', { class: 'plan-k' }, k), h('strong', { class: 'plan-v' }, v || '—')),
      onEdit ? h('button', { class: 'link-btn', type: 'button', onClick: onEdit, 'aria-label': `Cambiar: ${k}` }, 'Cambiar') : null);
  }

  /* ---------- paso 5: crear e instalar ---------- */
  function step5() {
    const c = S.created;
    if (!c) { go(0); return; }
    const devId = c.device.id;
    const filename = `${(c.device.nombre || S.nombre).replace(/[^A-Za-z0-9_.-]+/g, '-')}.rsc`;
    viewer = codeViewer({ text: c.script, filename, label: 'Script de instalación', maxHeight: true });
    const scriptBox = h('div', { class: 'stack' }, viewer.el);
    let visible = true;
    const toggle = h('button', { class: 'btn btn-secondary btn-sm', type: 'button', 'aria-pressed': 'false' }, icon('eye-off', { size: 16 }), 'Ocultar script');
    const hiddenMsg = h('div', { hidden: true }, notice({ kind: 'info', title: 'Script oculto', text: 'Por seguridad lo ocultamos. Si lo necesitas otra vez, descárgalo desde el detalle del equipo.' }));
    toggle.addEventListener('click', () => {
      visible = !visible;
      viewer.el.hidden = !visible;
      hiddenMsg.hidden = visible;
      toggle.replaceChildren(icon(visible ? 'eye-off' : 'eye', { size: 16 }), visible ? 'Ocultar script' : 'Mostrar script');
      if (!visible) { viewer.wipe(); } else { viewer.setText(S.created ? S.created.script : ''); }
    });
    // se oculta solo tras 10 minutos
    hideTimer = setTimeout(() => { if (visible) toggle.click(); }, 10 * 60 * 1000);

    const steps = [
      ['Abre Winbox y conéctate al router', 'Escribe su IP o su dirección MAC, el usuario y la contraseña, y pulsa «Conectar».'],
      ['Abre un terminal nuevo', 'En el menú de la izquierda elige «New Terminal».'],
      ['Pega el script', 'Pulsa «Copiar» aquí, vuelve a Winbox y pega con Ctrl + V (o clic derecho, Pegar). Luego pulsa Enter.'],
      ['Espera a que termine', 'No lo apagues ni lo desconectes. En un par de minutos el router debería conectarse solo.'],
    ];

    const waiter = connectionWaiter(devId);
    const again = h('button', { class: 'btn btn-secondary', type: 'button', onClick: () => { wipe(); ctx.navigate('/nuevo-router'); } }, icon('plus', { size: 16 }), 'Crear otro router');

    replaceChildren(stage, h('div', { class: 'stack-lg' },
      h('section', { class: 'card success-card' },
        h('div', { class: 'success-ico' }, icon('check', { size: 28 })),
        h('div', null,
          h('h2', { class: 'step-title' }, `Router «${c.device.nombre || S.nombre}» creado`),
          h('p', { class: 'step-intro' }, `Se le asignó la IP de túnel ${c.device.tunnel_ip || '—'} y la red local ${c.device.lan_cidr || S.lan.cidr}. Falta un último paso: instalar el script en el router.`))),
      notice({ kind: 'warn', title: 'Este script contiene claves secretas', text: 'Pégalo solo en este router. No lo envíes por chat ni correo, ni lo guardes en documentos compartidos. No lo guardamos en tu navegador y se borra de esta pantalla cuando sales.' }),
      h('section', { class: 'card stack', 'aria-labelledby': 'h-script' },
        h('div', { class: 'card-head' }, h('h2', { class: 'card-title', id: 'h-script' }, icon('terminal', { size: 18 }), 'Script de instalación'), toggle),
        scriptBox, hiddenMsg),
      h('section', { class: 'card stack', 'aria-labelledby': 'h-how' },
        h('h2', { class: 'card-title', id: 'h-how' }, icon('laptop', { size: 18 }), 'Cómo instalarlo en el router'),
        h('ol', { class: 'howto' }, steps.map(([t, d], i) => h('li', { class: 'howto-step' },
          h('div', { class: 'howto-art' }, installIllustration(i + 1)),
          h('div', { class: 'howto-text' }, h('span', { class: 'howto-n' }, `Paso ${i + 1}`), h('strong', null, t), h('p', null, d)))))),
      waiter.el,
      h('div', { class: 'wizard-nav' }, again, h('a', { class: 'btn btn-primary btn-lg', href: devId ? `#/equipos/${devId}` : '#/equipos' }, 'Ir al detalle del equipo', icon('arrow-right', { size: 18 })))));
    waiter.start();
  }

  function connectionWaiter(devId) {
    const state = { phase: 'wait', started: Date.now(), fails: 0 };
    const title = h('h2', { class: 'card-title', id: 'h-wait' });
    const msg = h('p', { class: 'wait-msg' });
    const clock = h('span', { class: 'wait-clock', 'aria-hidden': 'true' }, '00:00');
    const art = h('div', { class: 'wait-art' });
    const extra = h('div', { class: 'stack' });
    const el = h('section', { class: 'card wait-card', 'aria-labelledby': 'h-wait', 'aria-live': 'polite' }, art, h('div', { class: 'wait-body' }, title, msg, extra), clock);
    let timer = null, tick = null;

    function paint() {
      el.classList.toggle('is-ok', state.phase === 'ok');
      if (state.phase === 'ok') {
        title.textContent = '¡Conectado! El router ya habla con AVZ';
        msg.textContent = 'El túnel seguro está activo. Ya puedes aplicar servicios y dar soporte a este equipo.';
        replaceChildren(art, h('div', { class: 'celebrate' }, ...Array.from({ length: 10 }, (_, i) => h('i', { class: `confetti c${i}` })), h('span', { class: 'celebrate-ico' }, icon('check', { size: 34 }))));
        replaceChildren(extra, h('a', { class: 'btn btn-primary', href: `#/servicios?equipo=${devId}` }, icon('sliders', { size: 16 }), 'Aplicar un servicio'));
      } else if (state.phase === 'late') {
        title.textContent = 'Todavía no vemos al router';
        msg.textContent = 'Pasaron varios minutos sin conexión. Seguimos revisando, pero conviene comprobar lo siguiente:';
        replaceChildren(art, h('div', { class: 'wait-ring is-late' }, icon('clock', { size: 28 })));
        replaceChildren(extra, h('ul', { class: 'notice-list' },
          h('li', null, 'Que el script se pegó completo y sin errores en rojo.'),
          h('li', null, 'Que el router tiene internet (prueba abrir una página desde un equipo conectado a él).'),
          h('li', null, 'Que el reloj del router es correcto; si no, la conexión segura no se establece.')));
      } else {
        title.textContent = 'Esperando conexión del router…';
        msg.textContent = state.fails > 2 ? 'No pudimos consultar el estado, pero seguimos intentándolo.' : 'Cuando el router termine de instalar el script, aquí aparecerá la confirmación. Revisamos cada pocos segundos.';
        replaceChildren(art, h('div', { class: 'wait-ring' }, h('span', { class: 'spinner spinner-lg', 'aria-hidden': 'true' })));
        clear(extra);
      }
    }
    async function check() {
      try {
        const s = await getDeviceStatus(devId);
        if (!ctx.alive()) return;
        state.fails = 0;
        if (s.estado === 'online') { state.phase = 'ok'; stop(); paint(); return; }
      } catch { state.fails++; }
      if (state.phase === 'wait' && Date.now() - state.started > 6 * 60 * 1000) state.phase = 'late';
      paint();
    }
    function stop() { clearInterval(timer); clearInterval(tick); }
    stopPolling = stop;
    return {
      el,
      start() {
        paint();
        tick = setInterval(() => { if (state.phase !== 'ok') clock.textContent = fmtClock((Date.now() - state.started) / 1000); }, 1000);
        timer = setInterval(check, 5000);
        check();
      },
    };
  }

  go(0);
}

function friendlyMessage(e) {
  if (e instanceof ApiError && e.status === 422) {
    const parts = Array.isArray(e.raw) && e.raw.length ? e.raw : [];
    const fieldMsgs = Object.values(e.fields || {});
    if (!parts.length && !fieldMsgs.length) return e.message;
    const text = parts.length ? parts.join(' ') : fieldMsgs.join(' ');
    return text || 'Hay datos que no son válidos. Vuelve al paso indicado y revísalos.';
  }
  return e && e.message ? e.message : 'Ocurrió un error inesperado.';
}
