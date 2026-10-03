// Cliente de la API. Con ?mock=1 las llamadas se resuelven con datos simulados (js/mock.js).
import { session } from './session.js';

export const MOCK = new URLSearchParams(location.search).get('mock') === '1';
const BASE = '/api/v1';

export class ApiError extends Error {
  constructor(status, message, { fields = {}, raw = null } = {}) {
    super(message);
    this.status = status;
    this.fields = fields;   // errores 422 por campo: { 'wan.address': 'texto' }
    this.raw = raw;
  }
  get notFound() { return this.status === 404; }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

const VALIDATION_TEXT = {
  missing: 'Este campo es obligatorio.',
  string_too_short: 'Es demasiado corto.',
  string_too_long: 'Es demasiado largo.',
  int_parsing: 'Escribe un número entero.',
  float_parsing: 'Escribe un número.',
  value_error: 'El valor no es válido.',
  string_pattern_mismatch: 'El formato no es válido.',
  literal_error: 'Elige una de las opciones disponibles.',
  string_type: 'Escribe un texto.',
  greater_than_equal: 'El valor es demasiado bajo.',
  less_than_equal: 'El valor es demasiado alto.',
};

function cleanMsg(item) {
  const raw = String(item.msg || '').replace(/^Value error,\s*/i, '');
  if (item.type && VALIDATION_TEXT[item.type]) return VALIDATION_TEXT[item.type];
  return raw || 'Valor no válido.';
}

function parseError(status, data) {
  const detail = data && data.detail;
  if (status === 422 && Array.isArray(detail)) {
    const fields = {};
    const lines = [];
    for (const item of detail) {
      const loc = (item.loc || []).filter((x) => x !== 'body' && x !== 'query');
      const key = loc.join('.');
      const msg = cleanMsg(item);
      if (key && !fields[key]) fields[key] = msg;
      lines.push(msg);
    }
    return new ApiError(422, 'Hay datos que revisar antes de continuar.', { fields, raw: lines });
  }
  if (status === 401) return new ApiError(401, typeof detail === 'string' ? detail : 'Tu sesión terminó. Entra de nuevo.');
  if (status === 403) return new ApiError(403, 'Tu perfil no tiene permiso para hacer esto.');
  if (status === 404) return new ApiError(404, typeof detail === 'string' ? detail : 'No encontramos lo que buscas.');
  if (status === 409) return new ApiError(409, typeof detail === 'string' ? detail : 'Esta acción choca con algo que ya existe.');
  if (status === 429) return new ApiError(429, 'Demasiados intentos. Espera unos minutos y vuelve a probar.');
  if (status === 502 || status === 503 || status === 504) {
    return new ApiError(status, 'No pudimos comunicarnos con la red de túneles. Espera un momento y vuelve a intentarlo.');
  }
  if (status >= 500) return new ApiError(status, 'Algo falló de nuestro lado. Vuelve a intentarlo y, si sigue igual, avisa a soporte.');
  return new ApiError(status, typeof detail === 'string' ? detail : 'No se pudo completar la acción.');
}

/** as: 'json' | 'blob' | 'text' */
export async function request(method, path, { body, query, as = 'json', auth = true } = {}) {
  let result;
  if (MOCK) {
    const { mockRequest } = await import('./mock.js');
    try {
      result = await mockRequest(method, path, { body, query, as });
    } catch (e) {
      if (e && e.mockStatus) {
        const err = parseError(e.mockStatus, e.mockData);
        if (err.status === 401 && auth) onUnauthorized();
        throw err;
      }
      throw e;
    }
    return result;
  }

  const url = new URL(BASE + path, location.origin);
  if (query) for (const [k, v] of Object.entries(query)) if (v != null && v !== '') url.searchParams.set(k, v);
  const headers = { Accept: as === 'json' ? 'application/json' : '*/*' };
  const token = session.token();
  if (auth && token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }

  let res;
  try {
    res = await fetch(url, { method, headers, body: payload, cache: 'no-store' });
  } catch {
    throw new ApiError(0, 'No hay conexión con el servidor. Revisa tu internet e inténtalo otra vez.');
  }
  if (!res.ok) {
    let data = null;
    try { data = await res.json(); } catch { /* sin cuerpo JSON */ }
    const err = parseError(res.status, data);
    if (res.status === 401 && auth) onUnauthorized();
    throw err;
  }
  if (res.status === 204) return null;
  if (as === 'blob') return res.blob();
  if (as === 'text') return res.text();
  try { return await res.json(); } catch { return null; }
}

export const api = {
  get: (path, opts) => request('GET', path, opts),
  post: (path, body, opts) => request('POST', path, { ...opts, body }),
  patch: (path, body, opts) => request('PATCH', path, { ...opts, body }),
  del: (path, opts) => request('DELETE', path, opts),
};

/** Ejecuta la llamada y devuelve null si el endpoint aún no existe (404), para degradar con elegancia. */
export async function optional(promiseFactory) {
  try {
    return await promiseFactory();
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 405 || e.status === 501)) return null;
    throw e;
  }
}
