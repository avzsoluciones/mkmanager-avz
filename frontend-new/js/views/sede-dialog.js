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
export async function openSedeDialog(onCreated, { empresaId = null } = {}) {
  let empresas = [];
  try { empresas = await listEmpresas(); } catch (e) { toast(e.message, { type: 'error' }); return; }
  if (!empresas.length) {
    openEmpresaDialog(() => openSedeDialog(onCreated), { intro: 'Antes de crear una sede necesitas la empresa a la que pertenece. Créala ahora y seguimos con la sede.' });
    return;
  }

  const f = {
    empresa_id: field({
      label: 'Empresa', type: 'select', required: true, hint: 'Primero la empresa: la sede siempre pertenece a una.',
      options: [{ value: '', label: 'Elige una empresa…' }, ...empresas.map((e) => ({ value: String(e.id), label: e.aliado && new Set(empresas.map((x) => x.aliado)).size > 1 ? `${e.nombre} · ${e.aliado}` : e.nombre }))],
      value: empresaId ? String(empresaId) : (empresas.length === 1 ? String(empresas[0].id) : ''),
    }),
    nombre: field({ label: 'Nombre de la sede', required: true, placeholder: 'Ej. Tienda Centro', maxlength: 150, autocomplete: 'off' }),
    ciudad: field({ label: 'Ciudad', required: true, placeholder: 'Ej. Medellín', maxlength: 100, autocomplete: 'off' }),
    direccion: field({ label: 'Dirección', placeholder: 'Calle, número, local', autocomplete: 'off' }),
    contacto_nombre: field({ label: 'Persona de contacto', autocomplete: 'off' }),
    contacto_telefono: field({ label: 'Teléfono de contacto', type: 'tel', autocomplete: 'off', inputmode: 'tel' }),
    contacto_email: field({ label: 'Correo de contacto', type: 'email', autocomplete: 'off', spellcheck: false }),
  };
  const newEmpresa = h('button', { class: 'btn btn-secondary btn-sm', type: 'button' }, 'Crear otra empresa');
  const slot = h('div', { 'aria-live': 'assertive' });
  const form = h('form', { class: 'stack', novalidate: '' },
    f.empresa_id.el, h('div', null, newEmpresa),
    f.nombre.el, f.ciudad.el, f.direccion.el,
    h('div', { class: 'form-grid' }, f.contacto_nombre.el, f.contacto_telefono.el),
    f.contacto_email.el, slot);

  async function submit({ close, button }) {
    slot.replaceChildren();
    Object.values(f).forEach((x) => x.setError(''));
    let bad = false;
    if (!f.empresa_id.value) { f.empresa_id.setError('Elige la empresa a la que pertenece la sede.'); bad = true; }
    if (f.nombre.value.trim().length < 2) { f.nombre.setError('Escribe un nombre de al menos 2 letras.'); bad = true; }
    if (f.ciudad.value.trim().length < 2) { f.ciudad.setError('Escribe la ciudad.'); bad = true; }
    const mail = f.contacto_email.value.trim();
    if (mail && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(mail)) { f.contacto_email.setError('Revisa el correo: debería verse como nombre@empresa.com.'); bad = true; }
    if (bad) { Object.values(f).find((x) => x.el.classList.contains('has-error')).focus(); return false; }
    const body = { empresa_id: Number(f.empresa_id.value) };
    for (const [k, x] of Object.entries(f)) if (k !== 'empresa_id' && x.value.trim()) body[k] = x.value.trim();
    setBusy(button, true, 'Guardando…');
    try {
      const sede = await api.post('/sedes', body);
      toast(`Sede «${sede.nombre}» creada.`, { type: 'ok' });
      close();
      if (onCreated) onCreated(sede);
    } catch (e) {
      setBusy(button, false);
      if (e instanceof ApiError && e.status === 409) { f.nombre.setError('Ya existe una sede con ese nombre en esa empresa.'); f.nombre.focus(); return false; }
      if (e instanceof ApiError && e.status === 422) {
        const rest = applyFieldErrors(e, f);
        if (rest.length) slot.replaceChildren(notice({ kind: 'bad', title: 'Revisa los datos', text: rest.join(' ') }));
        return false;
      }
      slot.replaceChildren(notice({ kind: 'bad', title: 'No se pudo crear la sede', text: e.message }));
      return false;
    }
    return true;
  }

  const { dialog, close } = openDialog({
    title: 'Nueva sede',
    body: [h('p', { class: 'dialog-text' }, 'Una sede es el lugar donde está instalado el equipo: una tienda, una oficina, una casa.'), form],
    actions: [
      { label: 'Cancelar', variant: 'secondary', value: false },
      { label: 'Crear sede', variant: 'primary', onClick: submit },
    ],
  });
  newEmpresa.addEventListener('click', () => {
    if (close) close();
    openEmpresaDialog((emp) => openSedeDialog(onCreated, { empresaId: emp.id }));
  });
  form.addEventListener('submit', (ev) => { ev.preventDefault(); dialog.querySelector('.btn-primary').click(); });
  (f.empresa_id.value ? f.nombre : f.empresa_id).focus();
}
