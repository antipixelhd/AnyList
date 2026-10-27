import { ToastStore, type ToastOptions, type ToastKind } from './toast-store.ts';
import { dismiss, reveal, cancelUiMotion } from './ui-motion.ts';

export const toastStore = new ToastStore();
export function showToast(message: string | ToastOptions, kind: ToastKind = 'info') {
  const options = typeof message === 'string' ? {message, kind} : message;
  if (options.kind === 'success') return null;
  return toastStore.show(options);
}
let listeners: AbortController | undefined;

export function mountToasts() {
  listeners?.abort();
  const host = document.querySelector<HTMLElement>('[data-toast-host]');
  const template = document.querySelector<HTMLTemplateElement>('[data-toast-template]');
  if (!host || !template) return;
  host.replaceChildren();
  listeners = new AbortController();
  const {signal} = listeners;
  const home = host.parentElement!;
  const placeHost = () => {
    const dialogs = [...document.querySelectorAll<HTMLDialogElement>('dialog[open]')];
    const modal = dialogs.filter(dialog => dialog.matches(':modal')).at(-1);
    const parent = modal || home;
    if (host.parentElement !== parent) parent.append(host);
  };
  const modalObserver = new MutationObserver(placeHost);
  modalObserver.observe(document.documentElement, {subtree:true, attributes:true, attributeFilter:['open']});
  placeHost();
  const rows = new Map<number, HTMLElement>();
  const messageRenderers = new Map<number, () => void>();
  window.addEventListener('resize', () => messageRenderers.forEach(render => render()), {signal});
  const unsubscribe = toastStore.subscribe(toasts => {
    const ids = new Set(toasts.map(toast => toast.id));
    rows.forEach((row, id) => {
      if (!ids.has(id)) { rows.delete(id); messageRenderers.delete(id); row.inert = true; void dismiss(row).then(() => row.remove()); }
    });
    toasts.forEach(toast => {
      let row = rows.get(toast.id);
      const fresh = !row;
      if (!row) {
        row = template.content.firstElementChild!.cloneNode(true) as HTMLElement;
        rows.set(toast.id, row); host.append(row);
        const id = toast.id;
        row.addEventListener('pointerenter', () => toastStore.pause(id), {signal});
        row.addEventListener('pointerleave', () => toastStore.resume(id), {signal});
        row.addEventListener('focusin', () => toastStore.pause(id, 'focus'), {signal});
        row.addEventListener('focusout', event => { if (!row!.contains(event.relatedTarget as Node)) toastStore.resume(id, 'focus'); }, {signal});
        row.querySelector('[data-toast-close]')!.addEventListener('click', () => toastStore.dismiss(id), {signal});
        row.querySelector('[data-toast-action]')!.addEventListener('click', () => {
          void toastStore.act(id).catch(() => showToast('Unable to complete this action. Try again.', 'error'));
        }, {signal});
      }
      row.dataset.kind = toast.kind;
      row.setAttribute('role', toast.kind === 'error' ? 'alert' : 'status');
      row.setAttribute('aria-live', toast.kind === 'error' ? 'assertive' : 'polite');
      const message = row.querySelector<HTMLElement>('[data-toast-message]')!;
      const renderMessage = () => {
        message.textContent = toast.message;
        message.toggleAttribute('data-single-line', !!toast.shortMessage && matchMedia('(max-width:760px)').matches);
        if (toast.shortMessage && matchMedia('(max-width:760px)').matches && message.scrollWidth > message.clientWidth)
          message.textContent = toast.shortMessage;
        message.setAttribute('aria-label', toast.message);
      };
      messageRenderers.set(toast.id, renderMessage);
      renderMessage();
      requestAnimationFrame(() => { if (messageRenderers.get(toast.id) === renderMessage) renderMessage(); });
      row.querySelectorAll<HTMLElement>('[data-toast-icon]').forEach(icon => icon.hidden = icon.dataset.toastIcon !== toast.kind);
      const action = row.querySelector<HTMLButtonElement>('[data-toast-action]')!;
      action.hidden = !toast.action; action.textContent = toast.action?.label || '';
      if (document.hidden) toastStore.pause(toast.id, 'visibility');
      if (fresh) reveal(row);
    });
  });
  signal.addEventListener('abort', () => { modalObserver.disconnect(); unsubscribe(); rows.forEach(cancelUiMotion); if (host.parentElement !== home) home.append(host); }, {once:true});
  document.addEventListener('visibilitychange', () => toastStore.toasts.forEach(toast => {
    if (document.hidden) toastStore.pause(toast.id, 'visibility');
    else toastStore.resume(toast.id, 'visibility');
  }), {signal});
  document.addEventListener('astro:before-swap', () => { toastStore.clear(); listeners?.abort(); }, {signal, once:true});
}
