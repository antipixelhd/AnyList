import {cancelImportNotification, confirmImportCancellation} from './import-cancel';

type ActiveImport = {import_id: string | null; connection_id: number | null; title: string; resolve_url: string};
let active: ActiveImport | null = null;

async function refresh(signal?: AbortSignal) {
  const response = await fetch('/api/proxy/imports/active', {signal: AbortSignal.any([
    AbortSignal.timeout(30000), ...(signal ? [signal] : []),
  ])});
  if (!response.ok) throw new Error('Could not check your current import. Please try again.');
  active = (await response.json()).active;
  const banner = document.querySelector<HTMLElement>('[data-import-admission]');
  if (banner) {
    banner.hidden = !active;
    const title = banner.querySelector('[data-import-admission-title]');
    const resume = banner.querySelector<HTMLAnchorElement>('[data-import-admission-resume]');
    if (title) title.textContent = active?.title || '';
    if (resume) resume.href = active?.resolve_url || '#';
  }
  const add = document.querySelector<HTMLButtonElement>('#add-connection-btn');
  if (add) { add.disabled = !!active; add.classList.toggle('opacity-50', !!active); }
  document.querySelectorAll<HTMLElement>('[data-role="streaming-import-upload-controls"]').forEach(control => {
    control.inert = !!active;
    control.classList.toggle('opacity-50', !!active);
  });
  return active;
}

export async function canStartImport() {
  try {
    const current = await refresh();
    if (!current) return true;
    document.querySelector('[data-import-admission]')?.scrollIntoView({behavior: 'smooth', block: 'center'});
  } catch (error) {
    const {showMessage} = await import('./settings-feedback');
    showMessage(error instanceof Error ? error.message : 'Could not check your import.', 'error');
  }
  return false;
}

export function mountImportAdmission() {
  const banner = document.querySelector<HTMLElement>('[data-import-admission]');
  if (!banner || banner.dataset.mounted) return;
  banner.dataset.mounted = 'true';
  const controller = new AbortController();
  const {signal} = controller;
  const update = () => { void refresh(signal).catch(() => {}); };
  document.addEventListener('astro:before-swap', () => controller.abort(), {once: true, signal});
  window.addEventListener('focus', update, {signal});
  document.addEventListener('media-tracker:notifications-changed', update, {signal});
  banner.querySelector<HTMLButtonElement>('[data-import-admission-cancel]')?.addEventListener('click', async event => {
    const button = event.currentTarget as HTMLButtonElement;
    button.disabled = true;
    try {
      const current = await refresh(signal);
      if (!current) { location.reload(); return; }
      if (current.import_id) {
        if (!await cancelImportNotification(current.import_id, current.connection_id != null, signal)) return;
      } else {
        if (!await confirmImportCancellation(true, signal)) return;
        const response = await fetch(`/api/proxy/auth/connections/${current.connection_id}`, {
          method: 'DELETE', signal: AbortSignal.any([signal, AbortSignal.timeout(30000)]),
        });
        if (!response.ok) throw new Error('Could not cancel the connection import.');
      }
      location.reload();
    } catch (error) {
      const {showMessage} = await import('./settings-feedback');
      showMessage(error instanceof Error ? error.message : 'Could not cancel the import.', 'error');
    } finally { button.disabled = false; }
  }, {signal});
  update();
}
