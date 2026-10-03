// Sesión en sessionStorage (nunca localStorage). Si el navegador lo bloquea, queda en memoria.
const mem = {};
const get = (k) => { try { return sessionStorage.getItem(k); } catch { return mem[k] ?? null; } };
const set = (k, v) => { try { sessionStorage.setItem(k, v); } catch { mem[k] = v; } };
const del = (k) => { try { sessionStorage.removeItem(k); } catch { delete mem[k]; } };

export const ROLE_LABELS = {
  superadmin: 'Administrador AVZ',
  partner: 'Aliado comercial',
  soporte: 'Soporte técnico',
  cliente: 'Cliente (solo lectura)',
};

export const session = {
  token: () => get('avz_token'),
  user() {
    try { return JSON.parse(get('avz_user') || 'null'); } catch { return null; }
  },
  setLogin(resp) {
    set('avz_token', resp.access_token);
    set('avz_user', JSON.stringify(resp.user || {}));
    set('avz_must_change', resp.require_password_change ? '1' : '');
  },
  setUser(user) { set('avz_user', JSON.stringify(user || {})); },
  mustChange: () => get('avz_must_change') === '1',
  clearMustChange: () => del('avz_must_change'),
  clear() { del('avz_token'); del('avz_user'); del('avz_must_change'); },
  isAuthed: () => !!get('avz_token'),
  role: () => (session.user() || {}).role || '',
  canWrite: () => session.role() !== 'cliente',
  isSuper: () => session.role() === 'superadmin',
};
