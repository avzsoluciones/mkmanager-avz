// Piezas compartidas de la vista por aliado: mapa de la cartera y tarjetas de empresa.
import { h } from '../dom.js';
import { icon } from '../icons.js';
import { carteraMap, miniMap } from '../mapa-red.js';

const ESTADO_TXT = { online: 'Todo en línea', parcial: 'Atención', offline: 'Sin conexión', pendiente: 'Pendiente de conexión', vacia: 'Sin equipos' };
const PILL = { online: 'ok', offline: 'bad', parcial: 'warn', pendiente: 'warn', vacia: 'muted' };

/** Una tarjeta por empresa: constelación de sus sedes por color y «X de Y sedes en línea». */
export function empresaCards(empresas, { mostrarAliado = false } = {}) {
  return h('div', { class: 'emp-grid' }, empresas.map((g) => h('a', { class: `emp-card card card-link emp-${g.estado}`, href: `#/empresas/${encodeURIComponent(g.id)}` },
    miniMap(g.sedes),
    h('span', { class: 'emp-text' },
      h('span', { class: 'kicker' }, 'Empresa'),
      h('strong', { class: 'emp-name' }, g.nombre),
      mostrarAliado ? h('span', { class: 'emp-by' }, `Atendida por ${g.aliado}`) : null,
      h('span', { class: 'emp-sub' }, g.sedes.length ? `${g.online} de ${g.sedes.length} ${g.sedes.length === 1 ? 'sede en línea' : 'sedes en línea'}` : 'Aún sin sedes'),
      h('span', { class: `pill pill-${PILL[g.estado] || 'muted'}` }, g.estado === 'vacia' && !g.sedes.length ? 'Sin sedes' : (ESTADO_TXT[g.estado] || ''))),
    icon('chevron', { size: 18, cls: 'row-chev' }))));
}

/** Bloque de un aliado: su mapa (aliado → empresas → sedes) y debajo sus empresas. */
export function carteraBlock(aliado, { titulo = null } = {}) {
  return h('section', { class: 'stack', 'aria-labelledby': `h-cart-${aliado.id}` },
    h('div', { class: 'card' },
      h('h2', { class: 'card-title', id: `h-cart-${aliado.id}` }, icon('globe', { size: 18 }), titulo || `Empresas que atiende ${aliado.nombre}`),
      h('p', { class: 'muted' }, 'El aliado arriba, las empresas que atiende conectadas entre sí y las sedes de cada una. Toca una empresa o una sede para abrirla.'),
      carteraMap({ aliado: aliado.nombre, empresas: aliado.empresas })),
    h('h3', { class: 'card-title' }, icon('building', { size: 18 }), aliado.empresas.length === 1 ? 'Empresa' : 'Empresas'),
    empresaCards(aliado.empresas));
}

/** Tarjetas de aliados (vista de AVZ Cloud): cada aliado con sus empresas, para entrar a su cartera. */
export function aliadoCards(aliados) {
  return h('div', { class: 'emp-grid' }, aliados.map((a) => h('a', { class: `emp-card card card-link emp-${a.estado}`, href: `#/aliados/${encodeURIComponent(a.id)}` },
    miniMap(a.empresas),
    h('span', { class: 'emp-text' },
      h('span', { class: 'kicker kicker-aliado' }, 'Aliado'),
      h('strong', { class: 'emp-name' }, a.nombre),
      h('span', { class: 'emp-sub' }, `Atiende ${a.empresas.length} ${a.empresas.length === 1 ? 'empresa' : 'empresas'} · ${a.sedes} ${a.sedes === 1 ? 'sede' : 'sedes'}`),
      h('span', { class: `pill pill-${PILL[a.estado] || 'muted'}` }, ESTADO_TXT[a.estado] || '')),
    icon('chevron', { size: 18, cls: 'row-chev' }))));
}
