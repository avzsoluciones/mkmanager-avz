// Acceso a datos compartido por varias pantallas.
import { api } from './api.js';
import { confirmDialog, toast } from './dom.js';

export function normEstado(e) {
  const v = String(e || '').toLowerCase();
  if (['online', 'activo', 'en_linea', 'up'].includes(v)) return 'online';
  if (['pendiente', 'pending', 'nuevo'].includes(v)) return 'pendiente';
  if (['offline', 'inactivo', 'down', 'caido'].includes(v)) return 'offline';
  return v || 'offline';
}

/** Estado del túnel. Si /status aún no existe, se degrada al estado que trae /devices/{id}. */
export async function getDeviceStatus(id) {
  try {
    const s = await api.get(`/devices/${id}/status`);
    return { ...s, estado: normEstado(s.estado), degraded: false };
  } catch (e) {
    if (!e.notFound) throw e;
    const d = await api.get(`/devices/${id}`);
    return { estado: normEstado(d.estado), ultimo_contacto: d.ultimo_heartbeat, rx_bytes: null, tx_bytes: null, degraded: true };
  }
}

export async function listDevices() {
  const rows = await api.get('/devices');
  return (Array.isArray(rows) ? rows : []).map((d) => ({ ...d, estado: normEstado(d.estado) }));
}

export const listSedes = async () => {
  const rows = await api.get('/sedes');
  return Array.isArray(rows) ? rows : [];
};

export const CATEGORIAS = [
  { key: 'seguridad', label: 'Seguridad', icon: 'shield', color: 'seg', desc: 'Protege la red del local.' },
  { key: 'red', label: 'Red', icon: 'globe', color: 'red', desc: 'Direcciones, puertos y nombres.' },
  { key: 'acceso remoto', label: 'Acceso remoto', icon: 'key', color: 'acc', desc: 'Entrar al local desde fuera, con seguridad.' },
  { key: 'calidad de servicio', label: 'Calidad de servicio', icon: 'gauge', color: 'qos', desc: 'Reparte el internet entre usuarios y usos.' },
  { key: 'wi-fi', label: 'Wi-Fi', icon: 'wifi', color: 'wifi', desc: 'Redes inalámbricas y puntos de acceso.' },
];

export function categoriaDe(nombre) {
  const k = String(nombre || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/^wifi$/, 'wi-fi');
  return CATEGORIAS.find((c) => c.key.normalize('NFD').replace(/[̀-ͯ]/g, '') === k)
    || { key: k, label: nombre || 'Otros', icon: 'sliders', color: 'red', desc: '' };
}

export const puedeDarDeBaja = (role) => ['superadmin', 'partner'].includes(role);

export const borrarEquipo = (id) => api.del(`/devices/${encodeURIComponent(id)}`, { query: { confirmar: 'true' } });

/** Confirma y da de baja un equipo. Devuelve true si se eliminó. */
export async function bajaEquipo(dev) {
  const ok = await confirmDialog({
    title: `¿Dar de baja «${dev.nombre}»?`,
    message: 'Se elimina el equipo y su conexión al túnel seguro AVZ, y dejará de poder administrarse desde aquí. El router físico conserva su configuración. Esta acción no se puede deshacer.',
    confirmLabel: 'Sí, dar de baja', danger: true,
  });
  if (!ok) return false;
  try {
    await borrarEquipo(dev.id);
    toast(`«${dev.nombre}» se dio de baja.`, { type: 'ok' });
    return true;
  } catch (e) {
    toast(e.message, { type: 'error' });
    return false;
  }
}

/** Estado de una sede a partir de sus equipos: online, parcial, offline, pendiente o vacia. */
export function estadoSede(equipos) {
  if (!equipos.length) return 'vacia';
  const on = equipos.filter((e) => e.estado === 'online').length;
  if (on === equipos.length) return 'online';
  if (on > 0) return 'parcial';
  return equipos.every((e) => e.estado === 'pendiente') ? 'pendiente' : 'offline';
}

export const listEmpresas = async () => {
  const rows = await api.get('/empresas');
  return Array.isArray(rows) ? rows : [];
};

/** Estado agregado de una lista de elementos con `estado` de sede (online, parcial, offline, pendiente, vacia). */
export function estadoAgregado(estados) {
  const xs = estados.filter((e) => e !== 'vacia');
  if (!xs.length) return 'vacia';
  if (xs.every((e) => e === 'online')) return 'online';
  if (xs.every((e) => e === 'offline')) return 'offline';
  if (xs.every((e) => e === 'pendiente')) return 'pendiente';
  return 'parcial';
}

/** Une empresas, sedes y equipos: una entrada por empresa, con sus sedes (cada una con sus equipos y estado). */
export function agruparEmpresas(empresas, sedes, equipos) {
  return empresas.map((e) => {
    const ss = sedes.filter((s) => s.empresa_id === e.id).map((s) => {
      const mine = equipos.filter((q) => String(q.sede_id) === String(s.id));
      return { ...s, mine, estado: estadoSede(mine) };
    });
    return {
      id: e.id, nombre: e.nombre, partner_id: e.partner_id, aliado: e.aliado || 'AVZ Cloud',
      sedes: ss, equipos: ss.flatMap((s) => s.mine),
      online: ss.filter((s) => s.estado === 'online').length,
      estado: estadoAgregado(ss.map((s) => s.estado)),
    };
  }).sort((a, b) => a.nombre.localeCompare(b.nombre, 'es'));
}

/** Agrupa las empresas por aliado (la cartera que administra cada uno). */
export function agruparAliados(grupos) {
  const map = new Map();
  for (const g of grupos) {
    const key = g.partner_id == null ? 'avz' : String(g.partner_id);
    if (!map.has(key)) map.set(key, { id: key, nombre: g.aliado, empresas: [] });
    map.get(key).empresas.push(g);
  }
  return [...map.values()].map((a) => ({
    ...a,
    sedes: a.empresas.reduce((n, e) => n + e.sedes.length, 0),
    equipos: a.empresas.flatMap((e) => e.equipos),
    estado: estadoAgregado(a.empresas.flatMap((e) => e.sedes.map((x) => x.estado))),
  })).sort((a, b) => a.nombre.localeCompare(b.nombre, 'es'));
}
