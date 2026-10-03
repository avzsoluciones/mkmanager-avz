// Utilidades de direccionamiento IPv4 para validar en vivo (el servidor vuelve a validar todo).
export function ipToInt(s) {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(String(s || '').trim());
  if (!m) return null;
  let n = 0;
  for (let i = 1; i <= 4; i++) {
    const o = Number(m[i]);
    if (o > 255) return null;
    n = n * 256 + o;
  }
  return n;
}

export function intToIp(n) {
  return [n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255].join('.');
}

export function parseCidr(s) {
  const m = /^(\d{1,3}(?:\.\d{1,3}){3})\/(\d{1,2})$/.exec(String(s || '').trim());
  if (!m) return null;
  const ip = ipToInt(m[1]);
  const prefix = Number(m[2]);
  if (ip == null || prefix > 32) return null;
  const size = 2 ** (32 - prefix);
  const network = Math.floor(ip / size) * size;
  return { ip, prefix, network, size, broadcast: network + size - 1, isNetwork: network === ip };
}

export function overlaps(a, b) {
  return a.network <= b.broadcast && b.network <= a.broadcast;
}

export function inNet(net, ip) {
  return ip >= net.network && ip <= net.broadcast;
}

export function isPrivateNet(net) {
  const ranges = [['10.0.0.0', 8], ['172.16.0.0', 12], ['192.168.0.0', 16]];
  return ranges.some(([base, p]) => {
    const r = parseCidr(`${base}/${p}`);
    return net.network >= r.network && net.broadcast <= r.broadcast;
  });
}

export const RESERVED = [
  { cidr: '10.254.0.0/24', label: 'la red de túneles seguros de AVZ' },
  { cidr: '10.10.10.0/24', label: 'la red interna de AVZ' },
  { cidr: '10.40.0.0/18', label: 'una red reservada de AVZ' },
].map((r) => ({ ...r, net: parseCidr(r.cidr) }));

export function firstHost(net) { return net.network + 1; }

/** Mismo criterio que el servidor: /24 o mayor, 100 a 199; si no, de .2 al último host. */
export function defaultDhcp(net) {
  const hosts = net.size - 2;
  if (hosts >= 254) return [net.network + 100, net.network + 199];
  return [net.network + 2, net.broadcast - 1];
}

export function suggestFreeLan(existing) {
  const used = existing.map(parseCidr).filter(Boolean);
  const blocked = [...RESERVED.map((r) => r.net), ...used];
  const order = [];
  for (let n = 10; n <= 250; n += 10) order.push(n);
  for (let n = 1; n <= 254; n++) if (!order.includes(n)) order.push(n);
  for (const n of order) {
    const cand = parseCidr(`192.168.${n}.0/24`);
    if (!blocked.some((b) => overlaps(cand, b))) return `192.168.${n}.0/24`;
  }
  return '172.16.0.0/24';
}

/** Valida una IP (texto). Devuelve mensaje de error o ''. */
export function checkIp(s, label = 'La dirección') {
  if (!String(s || '').trim()) return `${label} es obligatoria.`;
  return ipToInt(s) == null ? `${label} no es válida. Usa el formato 192.168.10.1.` : '';
}

export function checkCidr(s, label = 'La red') {
  if (!String(s || '').trim()) return `${label} es obligatoria.`;
  const c = parseCidr(s);
  return c ? '' : `${label} no es válida. Usa el formato 192.168.10.0/24.`;
}
