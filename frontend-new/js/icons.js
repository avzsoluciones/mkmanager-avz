// Iconos propios de trazo (viewBox 24). Solo datos estáticos del proyecto.
const P = {
  home: ['M3 11l9-8 9 8', 'M5 10v10h14V10', 'M10 20v-6h4v6'],
  building: ['M4 21V5a1 1 0 011-1h8a1 1 0 011 1v16', 'M14 9h5a1 1 0 011 1v11', 'M8 8h2M8 12h2M8 16h2M17 13h1M17 17h1', 'M3 21h18'],
  router: ['M4 14h16a1 1 0 011 1v4a1 1 0 01-1 1H4a1 1 0 01-1-1v-4a1 1 0 011-1z', 'M7 17.5h.01M11 17.5h.01', 'M6 11a8 8 0 0112 0', 'M9 8.5a4 4 0 016 0'],
  plus: ['M12 5v14M5 12h14'],
  shield: ['M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z', 'M9 12l2 2 4-4'],
  globe: ['M12 21a9 9 0 100-18 9 9 0 000 18z', 'M3 12h18', 'M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18'],
  key: ['M8 15a4 4 0 100-8 4 4 0 000 8z', 'M12 11h9M18 11v3'],
  gauge: ['M4 17a8 8 0 1116 0', 'M12 17l4-5', 'M12 17h.01'],
  wifi: ['M2 9a15 15 0 0120 0', 'M5 12.5a10 10 0 0114 0', 'M8.5 16a5 5 0 017 0', 'M12 19.5h.01'],
  user: ['M12 12a4 4 0 100-8 4 4 0 000 8z', 'M4 21a8 8 0 0116 0'],
  users: ['M9 12a3.5 3.5 0 100-7 3.5 3.5 0 000 7z', 'M2 20a7 7 0 0114 0', 'M16 5.5a3.5 3.5 0 010 6.5', 'M18 14a7 7 0 013 6'],
  logout: ['M9 4H5a1 1 0 00-1 1v14a1 1 0 001 1h4', 'M16 8l4 4-4 4', 'M20 12H9'],
  sun: ['M12 16a4 4 0 100-8 4 4 0 000 8z', 'M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4'],
  moon: ['M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z'],
  check: ['M5 12.5l4.5 4.5L19 7'],
  x: ['M6 6l12 12M18 6L6 18'],
  alert: ['M12 3l10 18H2z', 'M12 10v5M12 18h.01'],
  info: ['M12 21a9 9 0 100-18 9 9 0 000 18z', 'M12 11v5M12 8h.01'],
  copy: ['M9 9h10a1 1 0 011 1v10a1 1 0 01-1 1H9a1 1 0 01-1-1V10a1 1 0 011-1z', 'M5 15H4a1 1 0 01-1-1V4a1 1 0 011-1h10a1 1 0 011 1v1'],
  download: ['M12 4v11', 'M7 11l5 5 5-5', 'M4 20h16'],
  refresh: ['M20 11a8 8 0 10-2 6', 'M20 4v7h-7'],
  search: ['M11 18a7 7 0 100-14 7 7 0 000 14z', 'M21 21l-5-5'],
  'arrow-right': ['M5 12h14M13 6l6 6-6 6'],
  'arrow-left': ['M19 12H5M11 6l-6 6 6 6'],
  chevron: ['M9 6l6 6-6 6'],
  lock: ['M6 11h12a1 1 0 011 1v8a1 1 0 01-1 1H6a1 1 0 01-1-1v-8a1 1 0 011-1z', 'M8 11V8a4 4 0 018 0v3'],
  clock: ['M12 21a9 9 0 100-18 9 9 0 000 18z', 'M12 7v5l3 2'],
  activity: ['M3 12h4l3-8 4 16 3-8h4'],
  qr: ['M4 4h6v6H4z', 'M14 4h6v6h-6z', 'M4 14h6v6H4z', 'M14 14h2v2h-2zM18 14h2M14 18h2v2M18 18h2v2'],
  trash: ['M4 7h16', 'M9 7V4h6v3', 'M6 7l1 13h10l1-13', 'M10 11v6M14 11v6'],
  dots: ['M5 12h.01M12 12h.01M19 12h.01'],
  cloud: ['M7 18a4.5 4.5 0 01-.5-9A6 6 0 0118 10a4 4 0 01-1 8z'],
  link: ['M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1', 'M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1'],
  eye: ['M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z', 'M12 15a3 3 0 100-6 3 3 0 000 6z'],
  'eye-off': ['M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z', 'M4 4l16 16'],
  server: ['M4 4h16v6H4z', 'M4 14h16v6H4z', 'M8 7h.01M8 17h.01'],
  sliders: ['M4 6h10M18 6h2M4 12h2M10 12h10M4 18h12M20 18h0', 'M16 4v4', 'M8 10v4', 'M18 16v4'],
  terminal: ['M4 5h16a1 1 0 011 1v12a1 1 0 01-1 1H4a1 1 0 01-1-1V6a1 1 0 011-1z', 'M7 10l3 2-3 2M12 15h5'],
  grid: ['M4 4h7v7H4z', 'M13 4h7v7h-7z', 'M4 13h7v7H4z', 'M13 13h7v7h-7z'],
  expand: ['M4 9V4h5', 'M20 9V4h-5', 'M4 15v5h5', 'M20 15v5h-5'],
  list: ['M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01'],
  sparkle: ['M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z'],
  pin: ['M12 21s7-6 7-11a7 7 0 10-14 0c0 5 7 11 7 11z', 'M12 12a2 2 0 100-4 2 2 0 000 4z'],
  layers: ['M12 3l9 5-9 5-9-5z', 'M3 13l9 5 9-5'],
  bolt: ['M13 2L4 14h7l-1 8 9-12h-7z'],
  laptop: ['M5 5h14v10H5z', 'M2 19h20'],
  phone: ['M8 3h8a1 1 0 011 1v16a1 1 0 01-1 1H8a1 1 0 01-1-1V4a1 1 0 011-1z', 'M11 18h2'],
  more: ['M4 6h16M4 12h16M4 18h16'],
  undo: ['M9 14L4 9l5-5', 'M4 9h10a6 6 0 010 12h-3'],
  ban: ['M12 21a9 9 0 100-18 9 9 0 000 18z', 'M5.6 5.6l12.8 12.8'],
};

const NS = 'http://www.w3.org/2000/svg';

export function hasIcon(name) { return Object.prototype.hasOwnProperty.call(P, name); }

export function icon(name, { size = 20, cls = '' } = {}) {
  const paths = P[name] || P.info;
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.setAttribute('class', ('icon ' + cls).trim());
  for (const d of paths) {
    const p = document.createElementNS(NS, 'path');
    p.setAttribute('d', d);
    svg.appendChild(p);
  }
  return svg;
}
