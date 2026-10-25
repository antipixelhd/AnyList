import {cancelImportNotification, confirmImportCancellation} from './import-cancel';

export type ActiveImport = {import_id: string | null; connection_id: number | null; title: string; resolve_url: string};
let active: ActiveImport | null = null;
let generation = 0;

async function refresh(signal?: AbortSignal) {
  const requestGeneration = ++generation;
  const response = await fetch('/api/proxy/imports/active', {signal: AbortSignal.any([
    AbortSignal.timeout(30000), ...(signal ? [signal] : []),
  ])});
  if (!response.ok) throw new Error('Could not check your current import. Please try again.');
  const updated = (await response.json()).active;
  if (requestGeneration !== generation || signal?.aborted) return active;
  active = updated;
  paint();
  return active;
}

function paint() {
  const params = new URL(location.href).searchParams;
  const resumed = !!active?.import_id && (params.get('import') === active.import_id ||
    Array.from(document.querySelectorAll<HTMLElement>('[data-streaming-import][data-import-session]'))
      .some(app => app.dataset.importSession === active!.import_id && !app.hidden));
  document.querySelectorAll<HTMLElement>('[data-import-admission]').forEach(notice => {
    notice.hidden = !active || resumed;
    const title = notice.querySelector('[data-import-admission-title]');
    const resume = notice.querySelector<HTMLAnchorElement>('[data-import-admission-resume]');
    if (title) title.textContent = active?.title || '';
    if (resume) resume.href = active?.resolve_url || '#';
  });
  document.querySelectorAll<HTMLElement>('[data-import-start-connection]').forEach(control => {
    control.hidden = !!active;
  });
  document.querySelectorAll<HTMLElement>('[data-import-start-service]').forEach(control => {
    const app = control.querySelector<HTMLElement>('[data-streaming-import]');
    const ownsReview = !!active?.import_id && (app?.dataset.importSession === active.import_id ||
      (resumed && params.get('source') === control.dataset.service && !params.has('connection')));
    control.hidden = !!active && !ownsReview;
  });
}

export async function canStartImport() {
  try {
    const current = await refresh();
    if (!current) return true;
    Array.from(document.querySelectorAll<HTMLElement>('[data-import-admission]'))
      .find(notice => !notice.closest('[hidden]'))?.scrollIntoView({behavior: 'smooth', block: 'center'});
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
  document.addEventListener('streaming:session-changed', update, {signal});
  document.addEventListener('streaming:activity-changed', () => { queueMicrotask(() => { if (!signal.aborted) paint(); }); }, {signal});
  document.querySelectorAll<HTMLButtonElement>('[data-import-admission-cancel]').forEach(cancel => cancel.addEventListener('click', async event => {
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
  }, {signal}));
  update();
}
