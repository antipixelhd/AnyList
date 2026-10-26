import {confirmEntryAction} from './entry-confirmation';

export const connectionImportCancelWarning = 'Cancel this import? The connection will be removed. You can add it again later.';
export function confirmImportCancellation(connection: boolean, signal: AbortSignal) {
  return connection ? confirmEntryAction(connectionImportCancelWarning, signal) : Promise.resolve(true);
}
export async function cancelImportNotification(id: string, connection: boolean, signal: AbortSignal) {
  if (!await confirmImportCancellation(connection, signal)) return false;
  const response = await fetch(`/api/proxy/imports/${encodeURIComponent(id)}`, {
    method: 'DELETE', signal: AbortSignal.any([signal, AbortSignal.timeout(30000)]),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || 'Could not cancel the import.');
  }
  document.dispatchEvent(new CustomEvent('media-tracker:notifications-changed'));
  return true;
}
