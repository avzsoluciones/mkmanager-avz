// Ilustraciones del asistente: diagrama de topología y pasos de instalación (SVG creados con nodos).
import { svg } from '../dom.js';

const txt = (attrs, t) => { const e = svg('text', attrs); e.textContent = t; return e; };
const short = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

export function topology() {
  const wanLabel = txt({ x: 184, y: 92, class: 'dg-label', 'text-anchor': 'start' }, 'Internet (WAN)');
  const routerName = txt({ x: 170, y: 152, class: 'dg-title', 'text-anchor': 'middle' }, 'Router nuevo');
  const routerSite = txt({ x: 170, y: 174, class: 'dg-sub', 'text-anchor': 'middle' }, 'Sede sin elegir');
  const lanLabel = txt({ x: 184, y: 228, class: 'dg-label', 'text-anchor': 'start' }, 'Red local (LAN)');
  const lanNet = txt({ x: 170, y: 286, class: 'dg-title', 'text-anchor': 'middle' }, 'Sin definir');
  const lanDhcp = txt({ x: 170, y: 306, class: 'dg-sub', 'text-anchor': 'middle' }, 'Rango de direcciones: —');
  const lanGw = txt({ x: 170, y: 324, class: 'dg-sub', 'text-anchor': 'middle' }, 'IP del router: —');

  const el = svg('svg', { viewBox: '0 0 480 350', class: 'diagram', role: 'img', 'aria-labelledby': 'dg-t dg-d' },
    svg('title', { id: 'dg-t' }, 'Diagrama de la red del cliente'),
    svg('desc', { id: 'dg-d' }, 'Internet llega al router; el router reparte la red local y mantiene un túnel seguro con el centro AVZ.'),
    // Internet
    svg('rect', { x: 100, y: 12, width: 140, height: 46, rx: 23, class: 'dg-node dg-cloud' }),
    txt({ x: 170, y: 40, class: 'dg-title', 'text-anchor': 'middle' }, 'Internet'),
    svg('path', { d: 'M170 58V122', class: 'dg-line' }),
    wanLabel,
    // router
    svg('rect', { x: 80, y: 122, width: 180, height: 68, rx: 14, class: 'dg-node dg-router' }),
    routerName, routerSite,
    // túnel
    svg('path', { d: 'M260 156H352', class: 'dg-line dg-tunnel' }),
    txt({ x: 306, y: 146, class: 'dg-label', 'text-anchor': 'middle' }, 'Túnel seguro'),
    svg('rect', { x: 352, y: 126, width: 112, height: 60, rx: 14, class: 'dg-node dg-avz' }),
    txt({ x: 408, y: 152, class: 'dg-title', 'text-anchor': 'middle' }, 'Centro AVZ'),
    txt({ x: 408, y: 171, class: 'dg-sub', 'text-anchor': 'middle' }, 'Soporte remoto'),
    // LAN
    svg('path', { d: 'M170 190V258', class: 'dg-line' }),
    lanLabel,
    svg('rect', { x: 20, y: 258, width: 300, height: 76, rx: 14, class: 'dg-node dg-lan' }),
    lanNet, lanDhcp, lanGw);

  return {
    el,
    update(s) {
      const wanMode = { dhcp: 'automático', static: 'IP fija', pppoe: 'PPPoE' }[s.wan.mode] || '';
      wanLabel.textContent = `${short(s.wan.interface || 'WAN', 14)} · ${wanMode}`;
      routerName.textContent = short(s.nombre.trim() || 'Router nuevo', 24);
      const sede = s.sedes.find((x) => String(x.id) === String(s.sede_id));
      routerSite.textContent = sede ? short(sede.nombre, 28) : 'Sede sin elegir';
      lanLabel.textContent = `Red local · ${short(s.lan.interface || 'LAN', 14)}`;
      lanNet.textContent = s.lan.cidr.trim() || 'Sin definir';
      lanDhcp.textContent = s.lan.dhcp_start && s.lan.dhcp_end ? `Direcciones: ${s.lan.dhcp_start} a ${s.lan.dhcp_end}` : 'Direcciones: —';
      lanGw.textContent = `IP del router: ${s.lan.gateway || '—'}`;
    },
  };
}

/* Paso a paso de instalación: ilustraciones simples de la ventana de Winbox. */
function win(children, w = 200, h = 120) {
  return svg('svg', { viewBox: `0 0 ${w} ${h}`, class: 'illus', 'aria-hidden': 'true', focusable: 'false' },
    svg('rect', { x: 2, y: 2, width: w - 4, height: h - 4, rx: 10, class: 'il-win' }),
    svg('rect', { x: 2, y: 2, width: w - 4, height: 18, rx: 10, class: 'il-bar' }),
    svg('circle', { cx: 14, cy: 11, r: 3, class: 'il-dot' }), svg('circle', { cx: 25, cy: 11, r: 3, class: 'il-dot' }), svg('circle', { cx: 36, cy: 11, r: 3, class: 'il-dot' }),
    ...children);
}

export function installIllustration(n) {
  if (n === 1) {
    return win([
      txt({ x: 100, y: 38, class: 'il-text', 'text-anchor': 'middle' }, 'Winbox'),
      svg('rect', { x: 20, y: 48, width: 160, height: 16, rx: 5, class: 'il-field' }),
      txt({ x: 28, y: 60, class: 'il-small' }, 'IP o MAC del router'),
      svg('rect', { x: 20, y: 70, width: 160, height: 16, rx: 5, class: 'il-field' }),
      txt({ x: 28, y: 82, class: 'il-small' }, 'admin · contraseña'),
      svg('rect', { x: 60, y: 94, width: 80, height: 18, rx: 9, class: 'il-hot' }),
      txt({ x: 100, y: 107, class: 'il-hot-text', 'text-anchor': 'middle' }, 'Conectar'),
    ]);
  }
  if (n === 2) {
    return win([
      svg('rect', { x: 8, y: 26, width: 62, height: 86, rx: 6, class: 'il-side' }),
      txt({ x: 16, y: 41, class: 'il-small' }, 'Interfaces'),
      txt({ x: 16, y: 56, class: 'il-small' }, 'IP'),
      txt({ x: 16, y: 71, class: 'il-small' }, 'System'),
      svg('rect', { x: 11, y: 79, width: 56, height: 16, rx: 5, class: 'il-hot' }),
      txt({ x: 16, y: 91, class: 'il-hot-text' }, 'New Terminal'),
      svg('path', { d: 'M76 87h28', class: 'il-arrow' }),
      svg('path', { d: 'M98 81l7 6-7 6', class: 'il-arrow' }),
      svg('rect', { x: 112, y: 40, width: 78, height: 56, rx: 6, class: 'il-term' }),
      txt({ x: 120, y: 62, class: 'il-code' }, '[admin@router]'),
    ]);
  }
  if (n === 3) {
    return win([
      svg('rect', { x: 10, y: 26, width: 180, height: 86, rx: 6, class: 'il-term' }),
      txt({ x: 18, y: 42, class: 'il-code il-code-dim' }, '# Script de AVZ Cloud'),
      txt({ x: 18, y: 56, class: 'il-code' }, '/interface add …'),
      txt({ x: 18, y: 70, class: 'il-code' }, '/ip address add …'),
      txt({ x: 18, y: 84, class: 'il-code' }, '/ip dhcp-server add …'),
      svg('rect', { x: 18, y: 92, width: 6, height: 11, class: 'il-cursor' }),
      svg('rect', { x: 118, y: 30, width: 66, height: 18, rx: 9, class: 'il-hot' }),
      txt({ x: 151, y: 43, class: 'il-hot-text', 'text-anchor': 'middle' }, 'Ctrl + V'),
    ]);
  }
  return win([
    svg('rect', { x: 10, y: 26, width: 180, height: 50, rx: 6, class: 'il-term' }),
    txt({ x: 18, y: 46, class: 'il-code' }, '[admin@router] > _'),
    svg('circle', { cx: 100, cy: 92, r: 14, class: 'il-ok' }),
    svg('path', { d: 'M93 92l5 5 9-10', class: 'il-check' }),
    txt({ x: 124, y: 96, class: 'il-small' }, 'Listo'),
  ]);
}
