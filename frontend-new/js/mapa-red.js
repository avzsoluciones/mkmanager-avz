// Mapa de la red: la empresa arriba y, debajo, sus sedes interconectadas con sus equipos (disposición tipo enjambre).
// Las posiciones se calculan una sola vez con una simulación de fuerzas determinista; luego solo se dibujan.
import { h, svg } from './dom.js';
import { icon } from './icons.js';
import { estadoSede } from './data.js';

const W = 860;
const H = 400;
const TOP_Y = 56;

function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
}

function layout(nodes, links) {
  const rand = rng(7);
  for (const n of nodes) if (!n.fixed) { n.vx = 0; n.vy = 0; }
  const iters = 320;
  for (let t = 0; t < iters; t++) {
    const alpha = 1 - t / iters;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = rand() - 0.5; dy = rand() - 0.5; d2 = 1; }
        const d = Math.sqrt(d2);
        const strength = (a.type === 'sede' && b.type === 'sede' ? 70000 : 2500) / d2;
        const fx = (dx / d) * strength, fy = (dy / d) * strength;
        if (!a.fixed) { a.vx -= fx; a.vy -= fy; }
        if (!b.fixed) { b.vx += fx; b.vy += fy; }
      }
    }
    for (const l of links) {
      const a = l.a, b = l.b;
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (d - l.len) * l.k;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      if (!a.fixed) { a.vx += fx; a.vy += fy; }
      if (!b.fixed) { b.vx -= fx; b.vy -= fy; }
    }
    for (const n of nodes) {
      if (n.fixed) continue;
      n.vy += ((n.type === 'sede' ? 215 : 300) - n.y) * 0.006;
      n.vx += (W / 2 - n.x) * 0.0004;
      n.x += n.vx * 0.6 * alpha + n.vx * 0.1; n.y += n.vy * 0.6 * alpha + n.vy * 0.1;
      n.vx *= 0.82; n.vy *= 0.82;
      const pad = n.type === 'sede' ? 80 : 22;
      n.x = Math.min(W - pad, Math.max(pad, n.x));
      n.y = Math.min(H - (n.type === 'sede' ? 90 : 24), Math.max(n.type === 'sede' ? 150 : 110, n.y));
    }
  }
}

const trunc = (s, n) => (String(s).length > n ? `${String(s).slice(0, n - 1)}…` : String(s));

const ESTADO_TXT = { online: 'en línea', parcial: 'con atención', offline: 'fuera de línea', pendiente: 'pendiente de conexión', vacia: 'sin equipos' };

/**
 * Mapa genérico: una raíz arriba, nodos medios (círculos) conectados entre sí y hojas pequeñas alrededor de cada medio.
 * medios: [{ id, nombre, titulo, cuenta, estado, href, hojas: [{ id, nombre, estado, href }] }]
 */
function buildMap({ raiz, medios, etiqueta, leyendaMalla, noc = true, herramientas = true }) {
  const rand = rng(11);
  const root = { id: 'raiz', type: 'empresa', x: W / 2, y: TOP_Y, fixed: true };
  const midNodes = medios.map((m, i) => ({ id: `m${m.id}`, type: 'sede', ref: m, x: (W * (i + 1)) / (medios.length + 1), y: 230 + (rand() - 0.5) * 60 }));
  const leafNodes = [];
  for (const m of midNodes) {
    for (const l of m.ref.hojas) {
      const ang = rand() * Math.PI * 2;
      leafNodes.push({ id: `l${m.ref.id}-${l.id}`, type: 'equipo', ref: l, parent: m, x: m.x + Math.cos(ang) * 60, y: m.y + 20 + Math.sin(ang) * 60 });
    }
  }
  const nodes = [root, ...midNodes, ...leafNodes];
  const links = [];
  for (const m of midNodes) links.push({ a: root, b: m, len: 160, k: 0.012, kind: 'empresa' });
  // Nodos medios entre sí: malla completa si son pocos; si no, cada uno con sus dos vecinos.
  for (let i = 0; i < midNodes.length; i++) {
    for (let j = i + 1; j < midNodes.length; j++) {
      const adj = j === i + 1 || (i === 0 && j === midNodes.length - 1);
      if (midNodes.length <= 6 || adj) links.push({ a: midNodes[i], b: midNodes[j], len: 260, k: 0.002, kind: 'malla' });
    }
  }
  for (const d of leafNodes) links.push({ a: d.parent, b: d, len: 74, k: 0.05, kind: 'equipo' });
  layout(nodes, links);

  const defs = svg('defs', null,
    svg('linearGradient', { id: 'nm-grad', x1: '0', y1: '0', x2: '1', y2: '1' },
      svg('stop', { offset: '0%', 'stop-color': 'var(--brand-1)' }),
      svg('stop', { offset: '100%', 'stop-color': 'var(--brand-2)' })));

  const linkEls = links.map((l) => svg('line', {
    class: `nm-link nm-link-${l.kind}${l.kind === 'equipo' ? ` nm-st-${l.b.ref.estado}` : ''}`,
    x1: l.a.x.toFixed(1), y1: l.a.y.toFixed(1), x2: l.b.x.toFixed(1), y2: l.b.y.toFixed(1),
  }));

  const rootEl = svg('g', { class: 'nm-company', transform: `translate(${root.x} ${root.y})` },
    svg('rect', { x: -130, y: -24, width: 260, height: 48, rx: 24, fill: 'url(#nm-grad)' }),
    svg('text', { class: 'nm-company-text', y: 5, 'text-anchor': 'middle' }, trunc(raiz, 26)));

  const midEls = midNodes.map((m) => svg('a', { href: m.ref.href, class: 'nm-node' },
    svg('g', { class: `nm-sede nm-st-${m.ref.estado}`, transform: `translate(${m.x.toFixed(1)} ${m.y.toFixed(1)})` },
      svg('title', null, `${m.ref.titulo}: ${ESTADO_TXT[m.ref.estado] || ''}`),
      svg('circle', { r: 26 }),
      svg('text', { class: 'nm-sede-n', y: 5, 'text-anchor': 'middle' }, String(m.ref.cuenta)),
      svg('text', { class: 'nm-label', y: 46, 'text-anchor': 'middle' }, trunc(m.ref.nombre, 18)))));

  const leafEls = leafNodes.map((d) => svg('a', { href: d.ref.href, class: 'nm-node' },
    svg('g', { class: `nm-dev nm-st-${d.ref.estado}`, transform: `translate(${d.x.toFixed(1)} ${d.y.toFixed(1)})` },
      svg('title', null, `${d.ref.nombre} (${ESTADO_TXT[d.ref.estado] || ''})`),
      svg('circle', { r: 8 }))));

  const viewH = Math.min(H, Math.max(...nodes.map((n) => n.y + (n.type === 'sede' ? 62 : 30))));
  const chart = svg('svg', { class: 'nm-svg', viewBox: `0 0 ${W} ${viewH.toFixed(0)}`, role: 'img', 'aria-label': etiqueta, preserveAspectRatio: 'xMidYMid meet' },
    defs, svg('g', null, linkEls), svg('g', null, leafEls), svg('g', null, midEls), rootEl);

  const legend = h('ul', { class: 'nm-legend', 'aria-label': 'Leyenda' },
    h('li', null, h('span', { class: 'nm-dot nm-dot-online' }), 'En línea'),
    h('li', null, h('span', { class: 'nm-dot nm-dot-pendiente' }), 'Atención / pendiente'),
    h('li', null, h('span', { class: 'nm-dot nm-dot-offline' }), 'Fuera de línea'),
    h('li', null, h('span', { class: 'nm-dash' }), leyendaMalla));
  const box = h('div', { class: 'nm' });
  const fs = h('button', { class: 'nm-fs', type: 'button' });
  const paintFs = () => {
    const on = document.fullscreenElement === box;
    fs.replaceChildren(icon(on ? 'x' : 'expand', { size: 18 }));
    fs.title = on ? 'Salir de pantalla completa' : 'Pantalla completa';
    fs.setAttribute('aria-label', fs.title);
  };
  fs.addEventListener('click', () => {
    if (document.fullscreenElement === box) document.exitFullscreen();
    else box.requestFullscreen().catch(() => {});
  });
  document.addEventListener('fullscreenchange', paintFs);
  paintFs();
  // Herramientas arriba a la derecha del gráfico: Vista NOC (TV) y pantalla completa.
  const tools = h('div', { class: 'nm-tools' },
    noc ? h('a', { class: 'nm-noc', href: '#/noc', title: 'Abrir la vista NOC para proyectar en un televisor' }, icon('activity', { size: 18 }), h('span', null, 'Vista NOC (TV)')) : null,
    fs);
  if (herramientas) box.append(tools);
  box.append(chart, legend);
  return box;
}

const plural = (n, a, b) => `${n} ${n === 1 ? a : b}`;

/** Mapa de una empresa: la empresa arriba, sus sedes interconectadas y los equipos de cada sede. */
export function networkMap({ empresa, sedes, equipos, noc = true, herramientas = true }) {
  const medios = sedes.map((s) => {
    const mine = s.mine || equipos.filter((e) => String(e.sede_id) === String(s.id));
    return {
      id: s.id, nombre: s.nombre, titulo: `${s.nombre}${s.ciudad ? ` · ${s.ciudad}` : ''} (${plural(mine.length, 'equipo', 'equipos')})`,
      cuenta: mine.length, estado: s.estado || estadoSede(mine), href: `#/equipos?sede=${encodeURIComponent(s.id)}`,
      hojas: mine.map((e) => ({ id: e.id, nombre: e.nombre, estado: e.estado, href: `#/equipos/${encodeURIComponent(e.id)}` })),
    };
  });
  const total = medios.reduce((n, m) => n + m.cuenta, 0);
  const online = equipos.filter((e) => e.estado === 'online').length;
  return buildMap({
    raiz: empresa, medios, leyendaMalla: 'Enlace entre sedes', noc, herramientas,
    etiqueta: `Mapa de la red de ${empresa}: ${plural(sedes.length, 'sede', 'sedes')} y ${plural(total, 'equipo', 'equipos')}, ${online} en línea.`,
  });
}

/** Mapa de la cartera de un aliado: el aliado arriba, sus empresas interconectadas y las sedes de cada una. */
export function carteraMap({ aliado, empresas, noc = true, herramientas = true }) {
  const medios = empresas.map((e) => ({
    id: e.id, nombre: e.nombre, titulo: `Empresa ${e.nombre} (${plural(e.sedes.length, 'sede', 'sedes')})`,
    cuenta: e.sedes.length, estado: e.estado, href: `#/empresas/${encodeURIComponent(e.id)}`,
    hojas: e.sedes.map((s) => ({ id: s.id, nombre: s.nombre, estado: s.estado, href: `#/equipos?sede=${encodeURIComponent(s.id)}` })),
  }));
  const sedes = empresas.reduce((n, e) => n + e.sedes.length, 0);
  return buildMap({
    raiz: aliado, medios, leyendaMalla: 'Enlace entre empresas', noc, herramientas,
    etiqueta: `Mapa de la cartera de ${aliado}: ${plural(empresas.length, 'empresa', 'empresas')} y ${plural(sedes, 'sede', 'sedes')}.`,
  });
}

/** Constelación pequeña para la vista rápida: un punto central y una sede por punto, coloreadas por estado. */
export function miniMap(sedes) {
  const w = 132, hh = 92, cx = w / 2, cy = hh / 2;
  const n = sedes.length;
  const rad = Math.min(34, 22 + n * 2);
  const pts = sedes.map((s, i) => {
    // Con 1 o 2 sedes se reparten en horizontal; con más, en círculo desde arriba.
    const start = n <= 2 ? Math.PI : -Math.PI / 2;
    const ang = n === 1 ? 0 : start + (i * 2 * Math.PI) / n;
    return { s, x: cx + Math.cos(ang) * rad * 1.6, y: cy + Math.sin(ang) * rad };
  });
  return svg('svg', { class: 'mini-map', viewBox: `0 0 ${w} ${hh}`, 'aria-hidden': 'true' },
    ...pts.map((p) => svg('line', { class: 'nm-link nm-link-empresa', x1: cx, y1: cy, x2: p.x.toFixed(1), y2: p.y.toFixed(1) })),
    svg('circle', { class: 'mini-core', cx, cy, r: 7 }),
    ...pts.map((p) => svg('circle', { class: `mini-dot nm-st-${p.s.estado}`, cx: p.x.toFixed(1), cy: p.y.toFixed(1), r: 8 }, svg('title', null, p.s.nombre))));
}

/** Mapa de todos los aliados (vista de AVZ Cloud): AVZ arriba, cada aliado como nodo y sus empresas alrededor. */
export function aliadosMap({ raiz, aliados, noc = true, herramientas = true }) {
  const medios = aliados.map((a) => ({
    id: a.id, nombre: a.nombre, titulo: `Aliado ${a.nombre} (atiende ${plural(a.empresas.length, 'empresa', 'empresas')})`,
    cuenta: a.empresas.length, estado: a.estado, href: `#/aliados/${encodeURIComponent(a.id)}`,
    hojas: a.empresas.map((e) => ({ id: e.id, nombre: e.nombre, estado: e.estado, href: `#/empresas/${encodeURIComponent(e.id)}` })),
  }));
  return buildMap({
    raiz, medios, leyendaMalla: 'Enlace entre aliados', noc, herramientas,
    etiqueta: `Mapa de aliados de ${raiz}: ${plural(aliados.length, 'aliado', 'aliados')}.`,
  });
}
