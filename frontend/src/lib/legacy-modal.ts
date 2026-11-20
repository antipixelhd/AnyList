import {showOverlay, hideOverlay} from './ui-motion';
const generations=new WeakMap<HTMLElement,number>();
/** Preserve the legacy full-screen panel geometry while using native modality. */
export function openLegacyDialog(element: HTMLElement, animated = false) {
  const dialog = element as HTMLDialogElement;
  generations.set(dialog,(generations.get(dialog)||0)+1);
  dialog.classList.remove('hidden');
  if (!dialog.open) dialog.showModal();
  if (animated) showOverlay(dialog);
}

export async function closeLegacyDialog(element: HTMLElement, animated = false) {
  const dialog = element as HTMLDialogElement;
  const generation=(generations.get(dialog)||0)+1;generations.set(dialog,generation);
  if (animated) await hideOverlay(dialog);
  if (generations.get(dialog)!==generation) return;
  if (dialog.open) dialog.close();
  dialog.classList.add('hidden');
}
