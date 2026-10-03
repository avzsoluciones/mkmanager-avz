// Capa de datos simulados para la demostración (?mock=1). Imita el contrato de /api/v1 con latencia.
// Opciones extra en la URL: &vacio=1 (arranca sin sedes, equipos ni accesos VPN).
import { session } from './session.js';
import * as net from './net.js';

const Q = new URLSearchParams(location.search);
const EMPTY = Q.get('vacio') === '1';
const T0 = Date.now();
const iso = (ms) => new Date(ms).toISOString();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const MIN = 60000;

function fail(status, detail) {
  const e = new Error('mock');
  e.mockStatus = status; e.mockData = { detail };
  return e;
}
const v422 = (items) => fail(422, items.map(([loc, msg, type]) => ({ loc: ['body', ...loc], msg, type: type || 'value_error' })));

/* ---------- estado ---------- */
const db = {
  empresas: EMPTY ? [] : [
    { id: 1, nombre: 'AFS', partner_id: 1, aliado: 'Víctor Aliado', contacto_email: null },
    { id: 2, nombre: 'Cardioprevent', partner_id: 1, aliado: 'Víctor Aliado', contacto_email: null },
    { id: 3, nombre: 'Notaría Central', partner_id: 2, aliado: 'Solución TIC', contacto_email: null },
    { id: 4, nombre: 'Laboratorio Miami', partner_id: null, aliado: 'AVZ Cloud', contacto_email: null },
  ],
  sedes: EMPTY ? [] : [
    { id: 1, nombre: 'Tienda Centro', ciudad: 'Medellín', empresa_id: 1, direccion: 'Cra 49 #52-10, Local 3', contacto_nombre: 'Laura Gómez', contacto_telefono: '+57 300 123 4567', contacto_email: 'centro@ejemplo.co', estado: 'activa', partner_id: 1 },
    { id: 2, nombre: 'Oficina Norte', ciudad: 'Bogotá', empresa_id: 1, direccion: 'Calle 127 #15-20, Of. 402', contacto_nombre: 'Andrés Rojas', contacto_telefono: '+57 310 765 4321', contacto_email: 'norte@ejemplo.co', estado: 'activa', partner_id: 1 },
    { id: 3, nombre: 'Bodega Sur', ciudad: 'Cali', empresa_id: 2, direccion: 'Zona Industrial, Bodega 7', contacto_email: null, estado: 'activa', partner_id: 1 },
    { id: 4, nombre: 'Notaría Sede 1', ciudad: 'Pereira', empresa_id: 3, direccion: 'Cra 8 #20-15', contacto_email: null, estado: 'activa', partner_id: 2 },
    { id: 5, nombre: 'Rack Miami', ciudad: 'Miami', empresa_id: 4, direccion: 'Colocation', contacto_email: null, estado: 'activa', partner_id: null },
  ],
  devices: EMPTY ? [] : [
    { id: 1, nombre: 'Router Tienda Centro', tipo: 'router', sede_id: 1, tunnel_ip: '10.254.0.2', lan_cidr: '192.168.10.0/24', estado: 'online', hb: T0 - 20000, rx: 842_113_220, tx: 213_450_981 },
    { id: 2, nombre: 'Router Oficina Norte', tipo: 'router', sede_id: 2, tunnel_ip: '10.254.0.3', lan_cidr: '192.168.20.0/24', estado: 'online', hb: T0 - 70000, rx: 3_412_908_114, tx: 1_120_004_551 },
    { id: 3, nombre: 'Router Bodega Sur', tipo: 'router', sede_id: 3, tunnel_ip: '10.254.0.4', lan_cidr: '192.168.30.0/24', estado: 'offline', hb: T0 - 3 * 3600000, rx: 90_112_400, tx: 12_000_113 },
    { id: 4, nombre: 'Router Caja 2', tipo: 'router', sede_id: 1, tunnel_ip: '10.254.0.5', lan_cidr: '192.168.40.0/24', estado: 'pendiente', hb: null, rx: 0, tx: 0 },
  ],
  apps: [],
  vpn: EMPTY ? [] : [
    { id: 1, nombre: 'Carlos Pérez (técnico)', tunnel_ip: '10.254.0.20', creado_en: iso(T0 - 5 * 86400000), revocado: false },
    { id: 2, nombre: 'Laura Gómez', tunnel_ip: '10.254.0.21', creado_en: iso(T0 - 12 * 86400000), revocado: false },
    { id: 3, nombre: 'Proveedor externo', tunnel_ip: '10.254.0.22', creado_en: iso(T0 - 30 * 86400000), revocado: true },
  ],
  audit: [
    { fecha: iso(T0 - 12 * MIN), usuario_email: 'admin@avzcloud.com', accion: 'login', detalles: '' },
    { fecha: iso(T0 - 2 * 3600000), usuario_email: 'soporte@avzcloud.com', accion: 'script_consultado', detalles: 'device=1' },
    { fecha: iso(T0 - 5 * 3600000), usuario_email: 'partner@demo.com', accion: 'router_aprovisionado', detalles: 'id=4 sede=1 tunnel=10.254.0.5 lan=192.168.40.0/24' },
    { fecha: iso(T0 - 26 * 3600000), usuario_email: 'partner@demo.com', accion: 'sede_creada', detalles: 'id=3 nombre=Bodega Sur' },
    { fecha: iso(T0 - 30 * 3600000), usuario_email: 'unknown@x.com', accion: 'login_fallido', detalles: '' },
  ],
  nextId: { empresa: 5, sede: 6, device: 5, app: 1, vpn: 4 },
  created: {},
};
// La demo conserva los cambios al recargar (solo en esta pestaña; se borra al cerrarla).
const KEY = 'avz_mock_db5' + (EMPTY ? '_vacio' : '');
try { const saved = sessionStorage.getItem(KEY); if (saved) Object.assign(db, JSON.parse(saved)); } catch { /* sin almacenamiento */ }
const persist = () => { try { sessionStorage.setItem(KEY, JSON.stringify(db)); } catch { /* sin almacenamiento */ } };
const sedeName = (id) => (db.sedes.find((s) => s.id === id) || {}).nombre || '';
const user = () => session.user() || { email: 'demo@avzcloud.com', full_name: 'Demo' };
function log(accion, detalles = '') { db.audit.unshift({ fecha: iso(Date.now()), usuario_email: user().email, accion, detalles }); }

const myPartner = () => (user().role === 'partner' ? 1 : null);
const empresaName = (id) => (db.empresas.find((e) => e.id === id) || {}).nombre || '';
const visibleSedes = () => db.sedes.filter((x) => myPartner() == null || x.partner_id === myPartner());
const visibleEmpresas = () => db.empresas.filter((x) => myPartner() == null || x.partner_id === myPartner());

function devView(d) {
  refreshDevice(d);
  return { id: d.id, nombre: d.nombre, tipo: d.tipo, marca: '', modelo: '', sede_id: d.sede_id, sede: sedeName(d.sede_id), tunnel_ip: d.tunnel_ip, lan_cidr: d.lan_cidr, estado: d.estado, ultimo_heartbeat: d.hb ? iso(d.hb) : null };
}
function refreshDevice(d) {
  // un router recién creado se "conecta" a los 15 segundos
  if (d.estado === 'pendiente' && d.createdAt && Date.now() - d.createdAt > 15000) { d.estado = 'online'; d.hb = Date.now(); }
  if (d.estado === 'online') { d.hb = Math.max(d.hb || 0, Date.now() - 15000); d.rx += 40_000; d.tx += 12_000; }
}

/* ---------- plantillas ---------- */
const TPL = [
  {
    id: 'firewall-seguro', nombre: 'Firewall seguro', categoria: 'Seguridad', icono: 'shield', riesgo: 'alto', revierte: true,
    descripcion: 'Bloquea todo lo que llega desde internet y deja pasar solo lo necesario.',
    campos: [
      { name: 'ips_admin', label: 'Redes que siempre pueden administrar el equipo', type: 'list', required: true, default: ['10.254.0.0/24'], help: 'Una por línea. Incluye el túnel seguro para no perder el acceso.' },
      { name: 'permitir_ping', label: 'Responder al ping desde internet', type: 'bool', default: false, help: 'Útil para diagnosticar, pero hace al equipo más visible.' },
    ],
    validate: (p) => (p.ips_admin || []).filter((c) => !net.parseCidr(c)).map((c) => [['params', 'ips_admin'], `«${c}» no es una red válida.`]),
    script: (p) => ['/ip firewall filter', 'add chain=input action=accept connection-state=established,related comment="avz: established"', 'add chain=input action=drop connection-state=invalid comment="avz: invalid"', ...(p.ips_admin || []).map((c) => `add chain=input action=accept src-address=${c} comment="avz: admin"`), ...(p.permitir_ping ? ['add chain=input action=accept protocol=icmp comment="avz: ping"'] : []), 'add chain=input action=drop in-interface-list=WAN comment="avz: drop wan"'].join('\n'),
    avisos: (p) => ((p.ips_admin || []).includes('10.254.0.0/24') ? [] : ['No incluiste el túnel seguro (10.254.0.0/24). Podrías perder el acceso de soporte.']),
  },
  {
    id: 'bloquear-sitios', nombre: 'Bloquear páginas', categoria: 'Seguridad', icono: 'ban', riesgo: 'medio', revierte: false,
    descripcion: 'Impide entrar a páginas concretas desde la red del local.',
    campos: [{ name: 'dominios', label: 'Páginas a bloquear', type: 'list', required: true, help: 'Una por línea, sin http://. Ej. ejemplo.com' }],
    validate: () => [],
    script: (p) => ['/ip dns static', ...(p.dominios || []).map((d) => `add name=${d} address=0.0.0.0 comment="avz: bloqueo"`)].join('\n'),
    avisos: () => ['Se bloquea el nombre exacto; los subdominios pueden seguir abriendo.'],
  },
  {
    id: 'port-forward', nombre: 'Abrir un puerto hacia un equipo', categoria: 'Red', icono: 'globe', riesgo: 'medio', revierte: true,
    descripcion: 'Permite que algo del local (cámara, servidor) sea alcanzable desde internet.',
    campos: [
      { name: 'nombre', label: 'Nombre de la regla', type: 'text', required: true, default: 'camara-entrada' },
      { name: 'protocolo', label: 'Protocolo', type: 'select', required: true, default: 'tcp', options: [{ value: 'tcp', label: 'TCP' }, { value: 'udp', label: 'UDP' }] },
      { name: 'puerto_externo', label: 'Puerto de internet', type: 'number', required: true, default: 8080, help: 'Entre 1 y 65535.' },
      { name: 'ip_destino', label: 'Equipo de destino', type: 'ip', required: true, help: 'La IP del equipo en la red local.' },
      { name: 'puerto_destino', label: 'Puerto del equipo', type: 'number', required: true, default: 80 },
    ],
    validate: (p) => [['puerto_externo', p.puerto_externo], ['puerto_destino', p.puerto_destino]].filter(([, v]) => !(v >= 1 && v <= 65535)).map(([k]) => [['params', k], 'El puerto debe estar entre 1 y 65535.']),
    script: (p) => `/ip firewall nat add chain=dstnat action=dst-nat protocol=${p.protocolo} dst-port=${p.puerto_externo} to-addresses=${p.ip_destino} to-ports=${p.puerto_destino} comment="avz: ${p.nombre}"`,
    avisos: (p) => (p.puerto_externo === 23 || p.puerto_externo === 3389 ? ['Abrir este puerto a internet es peligroso. Considera usar una VPN para personas.'] : []),
  },
  {
    id: 'reserva-dhcp', nombre: 'Reservar una IP para un equipo', categoria: 'Red', icono: 'pin', riesgo: 'bajo', revierte: false,
    descripcion: 'Hace que un equipo reciba siempre la misma dirección.',
    campos: [
      { name: 'mac', label: 'Dirección MAC del equipo', type: 'text', required: true, help: 'Formato AA:BB:CC:DD:EE:FF' },
      { name: 'ip', label: 'IP que tendrá', type: 'ip', required: true },
    ],
    validate: (p) => (/^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/.test(p.mac || '') ? [] : [[['params', 'mac'], 'La dirección MAC no tiene el formato correcto (AA:BB:CC:DD:EE:FF).']]),
    script: (p) => `/ip dhcp-server lease add mac-address=${p.mac} address=${p.ip} comment="avz: reserva"`,
    avisos: () => [],
  },
  {
    id: 'dns-ntp', nombre: 'Servidores DNS y hora', categoria: 'Red', icono: 'server', riesgo: 'bajo', revierte: false,
    descripcion: 'Cambia los servidores que traducen nombres de páginas y la fuente de hora.',
    campos: [
      { name: 'dns', label: 'Servidores DNS', type: 'list', required: true, default: ['1.1.1.1', '8.8.8.8'] },
      { name: 'ntp', label: 'Servidor de hora', type: 'text', default: 'pool.ntp.org' },
    ],
    validate: () => [],
    script: (p) => [`/ip dns set servers=${(p.dns || []).join(',')}`, `/system ntp client set enabled=yes server-dns-names=${p.ntp || 'pool.ntp.org'}`].join('\n'),
    avisos: () => [],
  },
  {
    id: 'vlan', nombre: 'Separar una red (VLAN)', categoria: 'Red', icono: 'layers', riesgo: 'alto', revierte: true,
    descripcion: 'Crea una red aparte, por ejemplo para invitados o cámaras.',
    campos: [
      { name: 'vlan_id', label: 'Número de VLAN', type: 'number', required: true, default: 20, help: 'Entre 2 y 4094.' },
      { name: 'interfaz', label: 'Puerto base', type: 'select', required: true, default: 'bridge', options: ['bridge', 'ether2', 'ether3'] },
      { name: 'subred', label: 'Subred de la VLAN', type: 'cidr', required: true, default: '192.168.50.0/24' },
    ],
    validate: (p) => (p.vlan_id >= 2 && p.vlan_id <= 4094 ? [] : [[['params', 'vlan_id'], 'El número de VLAN debe estar entre 2 y 4094.']]),
    script: (p) => [`/interface vlan add name=vlan${p.vlan_id} vlan-id=${p.vlan_id} interface=${p.interfaz}`, `/ip address add address=${p.subred} interface=vlan${p.vlan_id}`].join('\n'),
    avisos: () => ['Los equipos conectados a ese puerto pueden perder la conexión mientras se aplica.'],
  },
  {
    id: 'limite-velocidad', nombre: 'Limitar la velocidad', categoria: 'Calidad de servicio', icono: 'gauge', riesgo: 'medio', revierte: true,
    descripcion: 'Reparte el internet para que nadie lo acapare.',
    campos: [
      { name: 'objetivo', label: 'Equipo o red a limitar', type: 'cidr', required: true, default: '192.168.10.0/24' },
      { name: 'bajada', label: 'Bajada máxima (Mbps)', type: 'number', required: true, default: 20 },
      { name: 'subida', label: 'Subida máxima (Mbps)', type: 'number', required: true, default: 5 },
    ],
    validate: (p) => [['bajada', p.bajada], ['subida', p.subida]].filter(([, v]) => !(v > 0)).map(([k]) => [['params', k], 'Debe ser mayor que cero.']),
    script: (p) => `/queue simple add name="avz-limite" target=${p.objetivo} max-limit=${p.subida}M/${p.bajada}M`,
    avisos: () => [],
  },
  {
    id: 'priorizar-voz', nombre: 'Priorizar llamadas', categoria: 'Calidad de servicio', icono: 'phone', riesgo: 'bajo', revierte: false,
    descripcion: 'Da preferencia a las llamadas por internet sobre las descargas.',
    campos: [{ name: 'activar', label: 'Activar prioridad para voz', type: 'bool', default: true }],
    validate: () => [],
    script: (p) => (p.activar ? '/ip firewall mangle add chain=prerouting protocol=udp dst-port=5060,10000-20000 action=mark-packet new-packet-mark=voz comment="avz: voz"' : '# Sin cambios'),
    avisos: () => [],
  },
  {
    id: 'wifi-invitados', nombre: 'Wi-Fi para invitados', categoria: 'Wi-Fi', icono: 'wifi', riesgo: 'medio', revierte: true,
    descripcion: 'Una red Wi-Fi separada que no ve los equipos del local.',
    campos: [
      { name: 'ssid', label: 'Nombre de la red Wi-Fi', type: 'text', required: true, default: 'Invitados' },
      { name: 'clave', label: 'Contraseña del Wi-Fi', type: 'text', required: true, help: 'Mínimo 8 caracteres.' },
      { name: 'aislar', label: 'Aislar a los invitados entre sí', type: 'bool', default: true },
    ],
    validate: (p) => ((p.clave || '').length >= 8 ? [] : [[['params', 'clave'], 'La contraseña debe tener al menos 8 caracteres.']]),
    script: (p) => [`/interface wifi add name=wifi-invitados ssid="${p.ssid}" security.passphrase="<CLAVE_WIFI>"`, p.aislar ? '/interface wifi set wifi-invitados configuration.client-isolation=yes' : ''].filter(Boolean).join('\n'),
    avisos: () => [],
  },
  {
    id: 'adoptar-ap', nombre: 'Conectar un punto de acceso', categoria: 'Wi-Fi', icono: 'wifi', riesgo: 'bajo', revierte: false,
    descripcion: 'Guarda el comando para adoptar un punto de acceso Wi-Fi nuevo.',
    campos: [{ name: 'ip_ap', label: 'IP del punto de acceso', type: 'ip', required: true }],
    validate: () => [],
    script: (p) => `# Adoptar punto de acceso en ${p.ip_ap}\n/ip dhcp-server lease add address=${p.ip_ap} comment="avz: ap"`,
    avisos: () => [],
  },
  {
    id: 'usuario-router', nombre: 'Crear usuario de administración', categoria: 'Acceso remoto', icono: 'user', riesgo: 'medio', revierte: false,
    descripcion: 'Crea una cuenta para que una persona entre al equipo.',
    campos: [
      { name: 'usuario', label: 'Nombre de usuario', type: 'text', required: true },
      { name: 'grupo', label: 'Permisos', type: 'select', required: true, default: 'read', options: [{ value: 'read', label: 'Solo ver' }, { value: 'write', label: 'Ver y cambiar' }, { value: 'full', label: 'Todo' }] },
    ],
    validate: () => [],
    script: (p) => `/user add name=${p.usuario} group=${p.grupo} password="<SE_GENERA_AL_APLICAR>"`,
    avisos: (p) => (p.grupo === 'full' ? ['El permiso «Todo» da control total. Úsalo solo con personal de confianza.'] : []),
  },
];
const tplPublic = (t) => ({ id: t.id, nombre: t.nombre, descripcion: t.descripcion, categoria: t.categoria, icono: t.icono, riesgo: t.riesgo, revierte: t.revierte, campos: t.campos });

/* ---------- utilidades ---------- */
function nextTunnel() {
  const used = new Set(db.devices.map((d) => d.tunnel_ip).concat(db.vpn.map((v) => v.tunnel_ip)));
  for (let i = 2; i <= 250; i++) if (!used.has(`10.254.0.${i}`)) return `10.254.0.${i}`;
  return '10.254.0.250';
}

function provisionPlan(body) {
  const errs = [];
  if (!body || typeof body !== 'object') throw fail(422, 'Solicitud inválida.');
  if (!db.sedes.find((s) => s.id === Number(body.sede_id))) throw fail(404, 'Sede no encontrada.');
  if (!/^[A-Za-z0-9][A-Za-z0-9 _.\-]{1,59}$/.test(String(body.nombre || '').trim())) errs.push([['nombre'], 'Nombre inválido: use letras, números, espacios, punto, guion o guion bajo.']);
  const wan = body.wan || {}, lan = body.lan || {};
  if (!/^[A-Za-z0-9][A-Za-z0-9_.\-]{0,31}$/.test(wan.interface || '')) errs.push([['wan', 'interface'], 'Nombre de interfaz WAN inválido.']);
  if (wan.mode === 'static' && (!wan.address || !wan.gateway)) errs.push([['wan'], 'WAN estática requiere address (CIDR) y gateway.']);
  if (errs.length) throw v422(errs);
  const c = net.parseCidr(lan.cidr);
  if (!c || !c.isNetwork) throw fail(422, `Subred LAN inválida: '${lan.cidr}'. Use el formato 192.168.10.0/24.`);
  if (!net.isPrivateNet(c)) throw fail(422, 'La subred LAN debe ser privada (RFC 1918).');
  if (c.prefix > 29 || c.prefix < 16) throw fail(422, 'La máscara de la LAN debe estar entre /16 y /29.');
  for (const r of net.RESERVED) if (net.overlaps(c, r.net)) throw fail(422, `La subred ${lan.cidr} solapa con la red reservada ${r.cidr}.`);
  for (const d of db.devices) if (d.lan_cidr && net.overlaps(c, net.parseCidr(d.lan_cidr))) throw fail(422, `La subred ${lan.cidr} solapa con otra sede ya registrada (${d.lan_cidr}).`);
  const gw = lan.gateway || net.intToIp(net.firstHost(c));
  const [a, b] = lan.dhcp_start && lan.dhcp_end ? [net.ipToInt(lan.dhcp_start), net.ipToInt(lan.dhcp_end)] : net.defaultDhcp(c);
  if (!net.inNet(c, net.ipToInt(gw))) throw fail(422, 'El gateway de la LAN no pertenece a la subred indicada.');
  if (!net.inNet(c, a) || !net.inNet(c, b)) throw fail(422, 'El rango DHCP debe estar dentro de la subred LAN.');
  return { c, gw, start: net.intToIp(a), end: net.intToIp(b), tunnel: nextTunnel() };
}

function provisionScript(body, plan, secret) {
  const key = secret ? 'DEMO+CLAVE+PRIVADA+SOLO+EJEMPLO+NO+REAL=' : '<CLAVE_PRIVADA_SE_GENERA_AL_CREAR>';
  const api = secret ? 'demo-clave-api-no-real' : '<CLAVE_API_SE_GENERA_AL_CREAR>';
  const { wan, lan } = body;
  return [
    `# AVZ Cloud Central Manager - ${sedeName(Number(body.sede_id))} / ${body.nombre}`,
    '# Pegar completo en un terminal del router.',
    '/system identity', `set name="${body.nombre}"`,
    '# --- Internet (WAN) ---',
    wan.mode === 'dhcp' ? `/ip dhcp-client add interface=${wan.interface} disabled=no` : wan.mode === 'static' ? `/ip address add address=${wan.address} interface=${wan.interface}\n/ip route add gateway=${wan.gateway}` : `# PPPoE ya configurado en ${wan.interface}`,
    '/ip firewall nat add chain=srcnat action=masquerade out-interface-list=WAN',
    '# --- Red local (LAN) ---',
    `/ip address add address=${plan.gw}/${plan.c.prefix} interface=${lan.interface}`,
    `/ip pool add name=avz-pool ranges=${plan.start}-${plan.end}`,
    `/ip dhcp-server network add address=${lan.cidr} gateway=${plan.gw} dns-server=${(lan.dns || []).join(',')}`,
    '# --- Túnel seguro AVZ ---',
    `/interface wireguard add name=avz-tunel private-key="${key}"`,
    `/ip address add address=${plan.tunnel}/24 interface=avz-tunel`,
    '/interface wireguard peers add interface=avz-tunel public-key="HUB+CLAVE+PUBLICA+DE+EJEMPLO=" endpoint-address=hub.ejemplo.avz endpoint-port=13231 allowed-address=10.254.0.0/24 persistent-keepalive=25s',
    '# --- Acceso de gestión ---',
    `/user add name=avz-api group=full password="${api}"`,
  ].join('\n');
}

/** Pseudo-QR dibujado en un canvas: solo para demostrar la interfaz. */
function fakeQr(seed) {
  const N = 29, S = 8, pad = 24, size = N * S + pad * 2;
  const cv = document.createElement('canvas');
  cv.width = size; cv.height = size;
  const g = cv.getContext('2d');
  g.fillStyle = '#ffffff'; g.fillRect(0, 0, size, size);
  g.fillStyle = '#0d1b2a';
  let s = (seed * 9301 + 49297) % 233280;
  const rnd = () => { s = (s * 9301 + 49297) % 233280; return s / 233280; };
  const inFinder = (x, y) => (x < 8 && y < 8) || (x >= N - 8 && y < 8) || (x < 8 && y >= N - 8);
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) if (!inFinder(x, y) && rnd() > 0.52) g.fillRect(pad + x * S, pad + y * S, S, S);
  for (const [fx, fy] of [[0, 0], [N - 7, 0], [0, N - 7]]) {
    g.fillStyle = '#0d1b2a'; g.fillRect(pad + fx * S, pad + fy * S, 7 * S, 7 * S);
    g.fillStyle = '#ffffff'; g.fillRect(pad + (fx + 1) * S, pad + (fy + 1) * S, 5 * S, 5 * S);
    g.fillStyle = '#0d1b2a'; g.fillRect(pad + (fx + 2) * S, pad + (fy + 2) * S, 3 * S, 3 * S);
  }
  return new Promise((res) => cv.toBlob((b) => res(b), 'image/png'));
}

/* ---------- rutas ---------- */
const NEED_WRITE = new Set(['POST', 'DELETE', 'PATCH']);
const routes = [];
const on = (method, rx, fn) => routes.push({ method, rx, fn });

on('POST', /^\/auth\/login$/, ({ body }) => {
  const email = String(body.email || '').trim().toLowerCase();
  if (body.password === 'bloqueo') throw fail(429, 'Demasiados intentos. Espere unos minutos.');
  if (body.password === 'incorrecta') throw fail(401, 'Credenciales incorrectas o usuario inactivo.');
  const local = email.split('@')[0];
  const role = ['partner', 'soporte', 'cliente'].includes(local) ? local : 'superadmin';
  const names = { superadmin: 'Ana Restrepo', partner: 'Víctor Aliado', soporte: 'Sofía Soporte', cliente: 'Camila Cliente' };
  return {
    access_token: `demo-token-${Date.now()}`, token_type: 'bearer',
    user: { id: 1, email: email.includes('@') ? email : `${email}@demo.com`, full_name: names[role], role, partner_id: role === 'partner' ? 1 : null },
    require_password_change: local === 'nuevo',
  };
});
on('GET', /^\/auth\/me$/, () => ({ user: user() }));
on('POST', /^\/auth\/change-password$/, ({ body }) => {
  if (body.current_password === 'incorrecta') throw fail(400, 'La contraseña actual no es correcta.');
  log('cambio_password');
  return { status: 'success' };
});

on('GET', /^\/empresas$/, () => visibleEmpresas().map((e) => ({ ...e, sedes: db.sedes.filter((x) => x.empresa_id === e.id).length })));
on('POST', /^\/empresas$/, ({ body }) => {
  const nombre = String(body.nombre || '').trim();
  if (nombre.length < 2) throw v422([[['nombre'], 'String should have at least 2 characters', 'string_too_short']]);
  const partner = myPartner() ?? Number(body.partner_id || 1);
  if (db.empresas.some((e) => e.partner_id === partner && e.nombre.toLowerCase() === nombre.toLowerCase())) throw fail(409, 'Ya existe una empresa con ese nombre.');
  const aliado = (db.empresas.find((e) => e.partner_id === partner) || {}).aliado || (partner === 1 ? 'Víctor Aliado' : 'Aliado');
  const e = { id: db.nextId.empresa++, nombre, partner_id: partner, aliado, contacto_email: (body.contacto_email || '').trim() || null };
  db.empresas.push(e);
  log('empresa_creada', `id=${e.id} nombre=${e.nombre}`);
  return { ...e, sedes: 0 };
});
on('PATCH', /^\/empresas\/(\d+)$/, ({ m, body }) => {
  const e = visibleEmpresas().find((x) => x.id === Number(m[1]));
  if (!e) throw fail(404, 'Empresa no encontrada.');
  const nombre = String(body.nombre ?? e.nombre).trim();
  if (nombre.length < 2) throw v422([[['nombre'], 'String should have at least 2 characters', 'string_too_short']]);
  if (db.empresas.some((x) => x.id !== e.id && x.partner_id === e.partner_id && x.nombre.toLowerCase() === nombre.toLowerCase())) throw fail(409, 'Ya existe una empresa con ese nombre.');
  e.nombre = nombre;
  if (body.contacto_email !== undefined) e.contacto_email = String(body.contacto_email || '').trim() || null;
  log('empresa_editada', `id=${e.id} nombre=${e.nombre}`);
  return { ...e };
});
on('DELETE', /^\/empresas\/(\d+)$/, ({ m }) => {
  if (user().role !== 'superadmin') throw fail(403, 'Acceso denegado.');
  const e = db.empresas.find((x) => x.id === Number(m[1]));
  if (!e) throw fail(404, 'Empresa no encontrada.');
  const n = db.sedes.filter((x) => x.empresa_id === e.id).length;
  if (n) throw fail(409, `La empresa tiene ${n} sede(s). Elimina las sedes antes de eliminarla.`);
  db.empresas.splice(db.empresas.indexOf(e), 1);
  log('empresa_eliminada', `id=${e.id} nombre=${e.nombre}`);
  return { ok: true };
});
on('GET', /^\/sedes$/, () => visibleSedes().map((s) => ({ ...s, empresa: empresaName(s.empresa_id), dispositivos: db.devices.filter((d) => d.sede_id === s.id).length })));
on('DELETE', /^\/sedes\/(\d+)$/, ({ m }) => {
  const id = Number(m[1]);
  const s = db.sedes.find((x) => x.id === id);
  if (!s) throw fail(404, 'Sede no encontrada.');
  const eq = db.devices.filter((d) => d.sede_id === id).length;
  if (eq) throw fail(409, `La sede tiene ${eq} equipo(s). Da de baja los equipos antes de eliminarla.`);
  const vpn = db.vpn.filter((v) => v.sede_id === id && !v.revocado).length;
  if (vpn) throw fail(409, `La sede tiene ${vpn} acceso(s) VPN activo(s). Revócalos antes de eliminarla.`);
  db.sedes.splice(db.sedes.indexOf(s), 1);
  log('sede_eliminada', `id=${id} nombre=${s.nombre}`);
  return { ok: true };
});
on('PATCH', /^\/sedes\/(\d+)$/, ({ m, body }) => {
  if (!['superadmin', 'soporte', 'partner'].includes(user().role)) throw fail(403, 'Sin permiso.');
  const s = visibleSedes().find((x) => x.id === Number(m[1]));
  if (!s) throw fail(404, 'Sede no encontrada.');
  const errs = [];
  const CTRL = /[\u0000-\u001f\u007f]/;
  const MAX = { nombre: 150, ciudad: 100, direccion: 255, contacto_nombre: 150, contacto_telefono: 50, contacto_email: 150 };
  for (const k of ['nombre', 'ciudad', 'empresa_id']) {
    if (k in body && body[k] === null) errs.push([[k], `El campo ${k} no puede ser nulo.`, 'value_error']);
  }
  const clean = {};
  for (const k of Object.keys(MAX)) {
    if (!(k in body) || body[k] === null) continue;
    const v = String(body[k]).trim();
    if (CTRL.test(v)) { errs.push([[k], 'No se permiten caracteres de control.', 'value_error']); continue; }
    if (v.length > MAX[k]) { errs.push([[k], `String should have at most ${MAX[k]} characters`, 'string_too_long']); continue; }
    if ((k === 'nombre' || k === 'ciudad') && v.length < 2) { errs.push([[k], 'String should have at least 2 characters', 'string_too_short']); continue; }
    if (k === 'contacto_email' && v && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) { errs.push([[k], 'El correo no tiene un formato válido.', 'value_error']); continue; }
    clean[k] = v;
  }
  if (errs.length) throw v422(errs);
  const prev = db.empresas.find((x) => x.id === s.empresa_id);
  let emp = prev;
  if (body.empresa_id != null && Number(body.empresa_id) !== s.empresa_id) {
    emp = visibleEmpresas().find((x) => x.id === Number(body.empresa_id));
    if (!emp) throw fail(404, 'Empresa no encontrada.');
    if (emp.partner_id !== s.partner_id) throw fail(422, 'La empresa pertenece a otro aliado.');
  }
  const nombre = clean.nombre !== undefined ? clean.nombre : s.nombre;
  if ((emp.id !== s.empresa_id || nombre.toLowerCase() !== s.nombre.toLowerCase())
    && db.sedes.some((x) => x.id !== s.id && x.empresa_id === emp.id && x.nombre.toLowerCase() === nombre.toLowerCase())) throw fail(409, 'Ya existe una sede con ese nombre en esa empresa.');
  s.nombre = nombre; s.empresa_id = emp.id;
  if (clean.ciudad !== undefined) s.ciudad = clean.ciudad;
  for (const k of ['direccion', 'contacto_nombre', 'contacto_telefono', 'contacto_email']) if (k in body) s[k] = clean[k] || null;
  log('sede_editada', `id=${s.id} nombre=${s.nombre} empresa_anterior=${prev ? prev.nombre : ''} empresa=${emp.nombre}`);
  return { id: s.id, nombre: s.nombre, ciudad: s.ciudad, empresa_id: emp.id, empresa: emp.nombre, estado: s.estado };
});
on('POST', /^\/sedes$/, ({ body }) => {
  const errs = [];
  if (body.empresa_id == null) errs.push([['empresa_id'], 'Field required', 'missing']);
  if (String(body.nombre || '').trim().length < 2) errs.push([['nombre'], 'String should have at least 2 characters', 'string_too_short']);
  if (String(body.ciudad || '').trim().length < 2) errs.push([['ciudad'], 'String should have at least 2 characters', 'string_too_short']);
  if (errs.length) throw v422(errs);
  const emp = visibleEmpresas().find((x) => x.id === Number(body.empresa_id));
  if (!emp) throw fail(404, 'Empresa no encontrada.');
  if (db.sedes.some((s) => s.empresa_id === emp.id && s.nombre.toLowerCase() === body.nombre.trim().toLowerCase())) throw fail(409, 'Ya existe una sede con ese nombre en esa empresa.');
  const s = { id: db.nextId.sede++, nombre: body.nombre.trim(), ciudad: body.ciudad.trim(), empresa_id: emp.id, direccion: body.direccion || null, contacto_email: body.contacto_email || null, estado: 'activa', partner_id: emp.partner_id };
  db.sedes.push(s);
  log('sede_creada', `id=${s.id} nombre=${s.nombre}`);
  return { id: s.id, nombre: s.nombre, ciudad: s.ciudad, empresa_id: s.empresa_id, empresa: emp.nombre, estado: s.estado };
});

on('GET', /^\/devices$/, () => { const ok = new Set(visibleSedes().map((x) => x.id)); return db.devices.filter((d) => ok.has(d.sede_id)).map(devView); });
on('DELETE', /^\/devices\/(\d+)$/, ({ m, query }) => {
  if (!['superadmin', 'partner'].includes(user().role)) throw fail(403, 'Sin permiso.');
  if (!query || String(query.confirmar) !== 'true') throw fail(400, 'Confirme la baja con confirmar=true.');
  const d = db.devices.find((x) => x.id === Number(m[1]));
  if (!d) throw fail(404, 'Dispositivo no encontrado.');
  db.devices.splice(db.devices.indexOf(d), 1);
  log('equipo_baja', `id=${d.id} nombre=${d.nombre} tunnel=${d.tunnel_ip}`);
  return { status: 'success' };
});
on('GET', /^\/devices\/(\d+)$/, ({ m }) => {
  const d = db.devices.find((x) => x.id === Number(m[1]));
  if (!d) throw fail(404, 'Dispositivo no encontrado.');
  return devView(d);
});
on('GET', /^\/devices\/(\d+)\/status$/, ({ m }) => {
  const d = db.devices.find((x) => x.id === Number(m[1]));
  if (!d) throw fail(404, 'Dispositivo no encontrado.');
  refreshDevice(d);
  return { estado: d.estado, ultimo_contacto: d.hb ? iso(d.hb) : null, rx_bytes: d.rx, tx_bytes: d.tx };
});

on('POST', /^\/provision\/preview$/, ({ body }) => {
  const plan = provisionPlan(body);
  return { tunnel_ip: plan.tunnel, lan_gateway: plan.gw, dhcp: [plan.start, plan.end], script: provisionScript(body, plan, false) };
});
on('POST', /^\/provision$/, ({ body }) => {
  const plan = provisionPlan(body);
  const d = { id: db.nextId.device++, nombre: body.nombre.trim(), tipo: 'router', sede_id: Number(body.sede_id), tunnel_ip: plan.tunnel, lan_cidr: String(plan.c.network === plan.c.ip ? body.lan.cidr : body.lan.cidr), estado: 'pendiente', hb: null, rx: 0, tx: 0, createdAt: Date.now(), body };
  db.devices.push(d);
  log('router_aprovisionado', `id=${d.id} sede=${d.sede_id} tunnel=${d.tunnel_ip} lan=${d.lan_cidr}`);
  return { status: 'success', device: { id: d.id, nombre: d.nombre, tunnel_ip: d.tunnel_ip, lan_cidr: d.lan_cidr }, script: provisionScript(body, plan, true) };
});
on('GET', /^\/provision\/(\d+)\/script$/, ({ m }) => {
  const d = db.devices.find((x) => x.id === Number(m[1]));
  if (!d) throw fail(404, 'Dispositivo no encontrado o sin datos de aprovisionamiento.');
  const body = d.body || { sede_id: d.sede_id, nombre: d.nombre, wan: { interface: 'ether1', mode: 'dhcp' }, lan: { interface: 'bridge', cidr: d.lan_cidr, dns: ['1.1.1.1', '8.8.8.8'] } };
  const c = net.parseCidr(d.lan_cidr);
  const [a, b] = net.defaultDhcp(c);
  log('script_consultado', `device=${d.id}`);
  return { script: provisionScript(body, { c, gw: net.intToIp(net.firstHost(c)), start: net.intToIp(a), end: net.intToIp(b), tunnel: d.tunnel_ip }, true) };
});

on('GET', /^\/dashboard$/, () => {
  db.devices.forEach(refreshDevice);
  const used = db.devices.length + db.vpn.length;
  return {
    sedes: db.sedes.length, equipos: db.devices.length,
    online: db.devices.filter((d) => d.estado === 'online').length,
    offline: db.devices.filter((d) => d.estado === 'offline').length,
    pendientes: db.devices.filter((d) => d.estado === 'pendiente').length,
    ips_libres: 198 - used,
    ips_total: 198,
    actividad: db.audit.slice(0, 8).map((a) => ({ fecha: a.fecha, usuario: a.usuario_email.split('@')[0], accion: a.accion, detalle: a.detalles })),
  };
});

on('GET', /^\/templates$/, () => TPL.map(tplPublic));
const findTpl = (id) => { const t = TPL.find((x) => x.id === id); if (!t) throw fail(404, 'Plantilla no encontrada.'); return t; };
const findDev = (id) => { const d = db.devices.find((x) => x.id === Number(id)); if (!d) throw fail(404, 'Dispositivo no encontrado.'); return d; };
on('POST', /^\/devices\/(\d+)\/templates\/([\w-]+)\/preview$/, ({ m, body }) => {
  findDev(m[1]); const t = findTpl(m[2]);
  const p = body.params || {};
  const errs = [];
  for (const c of t.campos) if (c.required && (p[c.name] == null || p[c.name] === '' || (Array.isArray(p[c.name]) && !p[c.name].length))) errs.push([['params', c.name], 'Field required', 'missing']);
  errs.push(...t.validate(p));
  if (errs.length) throw v422(errs);
  return { script: `# Servicio: ${t.nombre}\n${t.script(p)}`, avisos: t.avisos(p) };
});
on('POST', /^\/devices\/(\d+)\/templates\/([\w-]+)\/apply$/, ({ m, body }) => {
  const d = findDev(m[1]); const t = findTpl(m[2]);
  const p = body.params || {};
  const errs = t.validate(p);
  if (errs.length) throw v422(errs);
  if (d.estado !== 'online') throw fail(502, 'El equipo no responde por el túnel.');
  const mins = t.revierte && body.revertir_minutos ? Number(body.revertir_minutos) : 0;
  const app = { id: db.nextId.app++, device_id: d.id, plantilla: t.nombre, estado: 'aplicada', creado_en: iso(Date.now()), revertir_en: mins ? iso(Date.now() + mins * MIN) : null, usuario: user().email };
  db.apps.unshift(app);
  log('plantilla_aplicada', `device=${d.id} plantilla=${t.id}`);
  return { aplicacion_id: app.id, estado: app.estado, revertir_en: app.revertir_en };
});
on('POST', /^\/applications\/(\d+)\/confirm$/, ({ m }) => {
  const a = db.apps.find((x) => x.id === Number(m[1]));
  if (!a) throw fail(404, 'Aplicación no encontrada.');
  syncApp(a);
  if (a.estado === 'revertida') throw fail(409, 'Este cambio ya se revirtió porque no se confirmó a tiempo.');
  a.estado = 'confirmada';
  log('plantilla_confirmada', `app=${a.id}`);
  return { estado: 'confirmada' };
});
function syncApp(a) { if (a.estado === 'aplicada' && a.revertir_en && Date.parse(a.revertir_en) < Date.now()) a.estado = 'revertida'; }
on('GET', /^\/devices\/(\d+)\/applications$/, ({ m }) => {
  findDev(m[1]);
  return db.apps.filter((a) => a.device_id === Number(m[1])).map((a) => { syncApp(a); const { device_id, ...rest } = a; void device_id; return rest; });
});

on('GET', /^\/vpn\/users$/, () => db.vpn.map((v) => ({ ...v })));
on('POST', /^\/vpn\/users$/, ({ body }) => {
  const errs = [];
  if (String(body.nombre || '').trim().length < 2) errs.push([['nombre'], 'String should have at least 2 characters', 'string_too_short']);
  for (const r of body.rutas || []) if (!net.parseCidr(r)) errs.push([['rutas'], `Ruta inválida: ${r}`]);
  if (errs.length) throw v422(errs);
  if (body.sede_id == null && db.sedes.length > 1) throw fail(422, 'Indica la sede (sede_id) a la que tendrá acceso la persona.');
  if (body.sede_id != null && !db.sedes.find((x) => x.id === Number(body.sede_id))) throw fail(404, 'Sede no encontrada.');
  const u = { id: db.nextId.vpn++, sede_id: body.sede_id != null ? Number(body.sede_id) : (db.sedes[0] && db.sedes[0].id), nombre: body.nombre.trim(), tunnel_ip: nextTunnel(), creado_en: iso(Date.now()), revocado: false, rutas: body.rutas || [] };
  db.vpn.push(u);
  log('vpn_creada', `id=${u.id} nombre=${u.nombre}`);
  return { id: u.id, nombre: u.nombre, tunnel_ip: u.tunnel_ip, conf_text: confFor(u) };
});
const confFor = (u) => ['[Interface]', 'PrivateKey = DEMO+CLAVE+PRIVADA+SOLO+EJEMPLO+NO+REAL=', `Address = ${u.tunnel_ip}/32`, 'DNS = 1.1.1.1', '', '[Peer]', 'PublicKey = HUB+CLAVE+PUBLICA+DE+EJEMPLO=', `AllowedIPs = ${(u.rutas && u.rutas.length ? u.rutas : ['10.254.0.0/24']).join(', ')}`, 'Endpoint = hub.ejemplo.avz:13231', 'PersistentKeepalive = 25'].join('\n');
on('GET', /^\/vpn\/users\/(\d+)\/conf$/, ({ m, as }) => {
  const u = db.vpn.find((x) => x.id === Number(m[1]));
  if (!u) throw fail(404, 'Usuario VPN no encontrado.');
  return as === 'text' ? { __text: confFor(u) } : confFor(u);
});
on('GET', /^\/vpn\/users\/(\d+)\/qr\.png$/, async ({ m }) => {
  const u = db.vpn.find((x) => x.id === Number(m[1]));
  if (!u) throw fail(404, 'Usuario VPN no encontrado.');
  return { __blob: await fakeQr(u.id) };
});
on('DELETE', /^\/vpn\/users\/(\d+)$/, ({ m }) => {
  const u = db.vpn.find((x) => x.id === Number(m[1]));
  if (!u) throw fail(404, 'Usuario VPN no encontrado.');
  u.revocado = true;
  log('vpn_revocada', `id=${u.id}`);
  return { status: 'success' };
});

on('GET', /^\/audit$/, ({ query }) => {
  if (user().role !== 'superadmin') throw fail(403, 'Sin permiso.');
  return db.audit.slice(0, Number(query && query.limit) || 50).map((a) => ({ ...a }));
});

/* ---------- entrada ---------- */
export async function mockRequest(method, path, { body, query, as } = {}) {
  await sleep(220 + Math.random() * 420);
  const isLogin = path === '/auth/login';
  if (!isLogin && !session.token()) throw fail(401, 'No autenticado.');
  if (!isLogin && NEED_WRITE.has(method) && user().role === 'cliente' && path !== '/auth/change-password') throw fail(403, 'Sin permiso.');
  for (const r of routes) {
    if (r.method !== method) continue;
    const m = r.rx.exec(path);
    if (!m) continue;
    const out = await r.fn({ m, body: body || {}, query, as });
    if (method !== 'GET') persist();
    if (out && out.__blob) return out.__blob;
    if (out && out.__text != null) return out.__text;
    if (as === 'text' && typeof out === 'string') return out;
    return JSON.parse(JSON.stringify(out));
  }
  throw fail(404, 'No encontrado.');
}
