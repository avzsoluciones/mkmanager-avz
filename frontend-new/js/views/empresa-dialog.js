import { h, openDialog, toast } from '../dom.js';
import { api, ApiError } from '../api.js';
import { field, applyFieldErrors, setBusy, notice } from '../components.js';

/**
 * Diálogo para crear (o renombrar) una empresa. Llama onSaved(empresa) al guardar.
 * opts.empresa: si viene, se edita; opts.intro: texto de contexto (p. ej. «Primero crea la empresa»).
 */
export function openEmpresaDialog(onSaved, { empresa = null, intro = null } = {}) {
  const f = {
    nombre: field({ label: 'Nombre de la empresa', required: true, value: empresa ? empresa.nombre : '', placeholder: 'Ej. AFS, Cardioprevent', maxlength: 150, autocomplete: 'off' }),
    contacto_email: field({ label: 'Correo de contacto', type: 'email', value: (empresa && empresa.contacto_email) || '', autocomplete: 'off', spellcheck: false }),
  };
  const slot = h('div', { 'aria-live': 'assertive' });
  const form = h('form', { class: 'stack', novalidate: '' }, f.nombre.el, f.contacto_email.el, slot);

  async function submit({ close, button }) {
    slot.replaceChildren();
    Object.values(f).forEach((x) => x.setError(''));
    let bad = false;
    if (f.nombre.value.trim().length < 2) { f.nombre.setError('Escribe un nombre de al menos 2 letras.'); bad = true; }
    const mail = f.contacto_email.value.trim();
    if (mail && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(mail)) { f.contacto_email.setError('Revisa el correo: debería verse como nombre@empresa.com.'); bad = true; }
    if (bad) { Object.values(f).find((x) => x.el.classList.contains('has-error')).focus(); return false; }
    const body = { nombre: f.nombre.value.trim() };
    if (mail || empresa) body.contacto_email = mail;
    setBusy(button, true, 'Guardando…');
    try {
      const saved = empresa ? await api.patch(`/empresas/${encodeURIComponent(empresa.id)}`, body) : await api.post('/empresas', body);
      toast(empresa ? 'Empresa actualizada.' : `Empresa «${saved.nombre}» creada.`, { type: 'ok' });
      close();
      if (onSaved) onSaved(saved);
    } catch (e) {
      setBusy(button, false);
      if (e instanceof ApiError && e.status === 409) { f.nombre.setError('Ya tienes una empresa con ese nombre.'); f.nombre.focus(); return false; }
      if (e instanceof ApiError && e.status === 422) {
        const rest = applyFieldErrors(e, f);
        if (rest.length) slot.replaceChildren(notice({ kind: 'bad', title: 'Revisa los datos', text: rest.join(' ') }));
        return false;
      }
      slot.replaceChildren(notice({ kind: 'bad', title: 'No se pudo guardar la empresa', text: e.message }));
      return false;
    }
    return true;
  }

  const { dialog } = openDialog({
    title: empresa ? 'Editar empresa' : 'Nueva empresa',
    body: [h('p', { class: 'dialog-text' }, intro || 'La empresa es el cliente al que pertenecen las sedes y los equipos. Va siempre primero: después se crean sus sedes.'), form],
    actions: [
      { label: 'Cancelar', variant: 'secondary', value: false },
      { label: empresa ? 'Guardar' : 'Crear empresa', variant: 'primary', onClick: submit },
    ],
  });
  form.addEventListener('submit', (ev) => { ev.preventDefault(); dialog.querySelector('.btn-primary').click(); });
  f.nombre.focus();
}
