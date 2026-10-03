import { h, openDialog, toast } from '../dom.js';
import { api, ApiError } from '../api.js';
import { field, applyFieldErrors, setBusy, notice } from '../components.js';
import { listEmpresas } from '../data.js';
import { openEmpresaDialog } from './empresa-dialog.js';

/**
 * Diálogo "Nueva sede". La empresa es obligatoria y se pregunta primero.
 * Si todavía no hay ninguna empresa, se crea antes y luego se abre la sede.
 * opts.empresaId: empresa preseleccionada. Llama onCreated(sede) cuando el servidor la crea.
 */
export async function openSedeDialog(onCreated, { empresaId = null, sede = null } = {}) {
  const editing = !!sede;
  let empresas = [];
  try { empresas = await listEmpresas(); } catch (e) { toast(e.message, { type: 'error' }); return; }
  // Al editar solo se ofrecen las empresas del mismo aliado que la sede.
  if (editing) empresas = empresas.filter((e) => e.partner_id === sede.partner_id || e.id === sede.empresa_id);
  if (!editing && !empresas.length) {
    openEmpresaDialog(() => openSedeDialog(onCreated), { intro: 'Antes de crear una sede necesitas la empresa a la que pertenece. Créala ahora y seguimos con la sede.' });
    return;
  }

  const f = {
    empresa_id: field({
      label: 'Empresa', type: 'select', required: true, hint: editing ? 'Solo puedes pasar la sede a otra empresa del mismo aliado.' : 'Primero la empresa: la sede siempre pertenece a una.',
      options: [...(editing ? [] : [{ value: '', label: 'Elige una empresa…' }]), ...empresas.map((e) => ({ value: String(e.id), label: e.aliado && new Set(empresas.map((x) => x.aliado)).size > 1 ? `${e.nombre} · ${e.aliado}` : e.nombre }))],
      value: editing ? String(sede.empresa_id) : empresaId ? String(empresaId) : (empresas.length === 1 ? String(empresas[0].id) : ''),
    }),
    nombre: field({ label: 'Nombre de la sede', required: true, placeholder: 'Ej. Tienda Centro', maxlength: 150, autocomplete: 'off', value: sede?.nombre || '' }),
    ciudad: field({ label: 'Ciudad', required: true, placeholder: 'Ej. Medellín', maxlength: 100, autocomplete: 'off', value: sede?.ciudad || '' }),
    direccion: field({ label: 'Dirección', maxlength: 255, placeholder: 'Calle, número, local', autocomplete: 'off', value: sede?.direccion || '' }),
    contacto_nombre: field({ label: 'Persona de contacto', maxlength: 150, autocomplete: 'off', value: sede?.contacto_nombre || '' }),
    contacto_telefono: field({ label: 'Teléfono de contacto', type: 'tel', maxlength: 50, autocomplete: 'off', inputmode: 'tel', value: sede?.contacto_telefono || '' }),
    contacto_email: field({ label: 'Correo de contacto', type: 'email', maxlength: 150, autocomplete: 'off', spellcheck: false, value: sede?.contacto_email || '' }),
  };
  const newEmpresa = h('button', { class: 'btn btn-secondary btn-sm', type: 'button' }, 'Crear otra empresa');
  const slot = h('div', { 'aria-live': 'assertive' });
  const moveNote = h('div', { 'aria-live': 'polite' });
  const empName = (id) => (empresas.find((e) => String(e.id) === String(id)) || {}).nombre || '';
  const initial = Object.fromEntries(Object.entries(f).map(([k, x]) => [k, x.value]));
  const isDirty = () => Object.entries(f).some(([k, x]) => x.value !== initial[k]);
  if (editing) {
    if (empresas.length <= 1) {
      f.empresa_id.input.disabled = true;
      f.empresa_id.setHint('Este aliado no tiene otras empresas. Créala en Empresas y vuelve.');
    }
    f.empresa_id.input.addEventListener('change', () => {
      moveNote.replaceChildren(f.empresa_id.value !== String(sede.empresa_id)
        ? notice({ kind: 'info', title: 'Se moverá a otra empresa', text: `“${sede.nombre}” pasará de ${empName(sede.empresa_id)} a ${empName(f.empresa_id.value)}, con todos sus equipos. Puedes volver a cambiarla cuando quieras.` }) : '');
    });
  }
  const form = h('form', { class: 'stack', novalidate: '' },
    f.empresa_id.el, editing ? moveNote : h('div', null, newEmpresa),
    f.nombre.el, f.ciudad.el, f.direccion.el,
    h('div', { class: 'form-grid' }, f.contacto_nombre.el, f.contacto_telefono.el),
    f.contacto_email.el, slot);

  async function submit({ close, button }) {
    slot.replaceChildren();
    Object.values(f).forEach((x) => x.setError(''));
    let bad = false;
    if (!f.empresa_id.value) { f.empresa_id.setError('Elige la empresa a la que pertenece la sede.'); bad = true; }
    if (f.nombre.value.trim().length < 2) { f.nombre.setError('Escribe un nombre de al menos 2 caracteres.'); bad = true; }
    if (f.ciudad.value.trim().length < 2) { f.ciudad.setError('Escribe la ciudad (mínimo 2 caracteres).'); bad = true; }
    const mail = f.contacto_email.value.trim();
    if (mail && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(mail)) { f.contacto_email.setError('Revisa el correo: debería verse como nombre@empresa.com.'); bad = true; }
    if (bad) { Object.values(f).find((x) => x.el.classList.contains('has-error')).focus(); return false; }
    const body = {};
    if (editing) {
      if (Number(f.empresa_id.value) !== sede.empresa_id) body.empresa_id = Number(f.empresa_id.value);
      for (const [k, x] of Object.entries(f)) {
        if (k === 'empresa_id') continue;
        const v = x.value.trim();
        if (v === String(sede[k] ?? '')) continue;
        body[k] = v === '' && k !== 'nombre' && k !== 'ciudad' ? null : v;
      }
      if (!Object.keys(body).length) { toast('No hiciste cambios.', { type: 'info' }); close(); return true; }
    } else {
      body.empresa_id = Number(f.empresa_id.value);
      for (const [k, x] of Object.entries(f)) if (k !== 'empresa_id' && x.value.trim()) body[k] = x.value.trim();
    }
    setBusy(button, true, 'Guardando…');
    try {
      const saved = editing ? await api.patch(`/sedes/${encodeURIComponent(sede.id)}`, body) : await api.post('/sedes', body);
      const moved = editing && body.empresa_id !== undefined;
      toast(editing
        ? (moved ? `Sede “${saved.nombre}” movida a “${saved.empresa || empName(body.empresa_id)}”.` : `Sede “${saved.nombre}” actualizada.`)
        : `Sede «${saved.nombre}» creada.`, { type: 'ok' });
      close();
      if (onCreated) onCreated(saved);
    } catch (e) {
      setBusy(button, false);
      if (e instanceof ApiError && e.status === 409) {
        if (editing && body.nombre === undefined) {
          f.empresa_id.setError(`Ya hay una sede llamada “${sede.nombre}” en esa empresa. Elige otra empresa o cambia el nombre.`);
          f.empresa_id.focus();
        } else {
          f.nombre.setError(editing ? 'Ya existe una sede con ese nombre en esa empresa. Prueba con otro.' : 'Ya existe una sede con ese nombre en esa empresa.');
          f.nombre.focus();
        }
        return false;
      }
      if (editing && e instanceof ApiError && e.status === 404) {
        slot.replaceChildren(notice({ kind: 'bad', title: 'No se pudo guardar la sede', text: 'Esta sede ya no existe. Puede que otra persona la haya eliminado. Cierra esta ventana y actualiza la lista.' }));
        return false;
      }
      if (editing && e instanceof ApiError && e.status === 422 && /otro aliado/i.test(e.message || '')) {
        f.empresa_id.setError('Esa empresa es de otro aliado. Elige una de la lista.');
        f.empresa_id.focus();
        return false;
      }
      if (e instanceof ApiError && e.status === 422) {
        const rest = applyFieldErrors(e, f);
        if (!rest.length && e.message && !Object.values(f).some((x) => x.el.classList.contains('has-error'))) rest.push(e.message);
        if (rest.length) slot.replaceChildren(notice({ kind: 'bad', title: 'Revisa los datos', text: rest.join(' ') }));
        return false;
      }
      slot.replaceChildren(notice({ kind: 'bad', title: editing ? 'No se pudo guardar la sede' : 'No se pudo crear la sede', text: e.message }));
      return false;
    }
    return true;
  }

  const { dialog, close } = openDialog({
    title: editing ? 'Editar sede' : 'Nueva sede',
    body: [editing ? null : h('p', { class: 'dialog-text' }, 'Una sede es el lugar donde está instalado el equipo: una tienda, una oficina, una casa.'), form],
    actions: [
      { label: 'Cancelar', variant: 'secondary', value: false },
      { label: editing ? 'Guardar cambios' : 'Crear sede', variant: 'primary', onClick: submit },
    ],
  });
  newEmpresa.addEventListener('click', () => {
    if (close) close();
    openEmpresaDialog((emp) => openSedeDialog(onCreated, { empresaId: emp.id }));
  });
  // Un clic en el fondo no cierra el diálogo si hay cambios sin guardar.
  dialog.addEventListener('click', (ev) => { if (ev.target === dialog && isDirty()) ev.stopImmediatePropagation(); }, true);
  form.addEventListener('submit', (ev) => { ev.preventDefault(); dialog.querySelector('.btn-primary').click(); });
  (editing || f.empresa_id.value ? f.nombre : f.empresa_id).focus();
}
