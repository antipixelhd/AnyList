import { animate } from 'motion/mini';

type MotionControl = ReturnType<typeof animate>;
const running = new Map<HTMLElement, MotionControl>();
const closing = new WeakMap<HTMLElement, number>();
const boundDialogs = new WeakSet<HTMLDialogElement>();
let sequence = 0;

const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

function stop(element: HTMLElement) {
  running.get(element)?.stop();
  running.delete(element);
  element.style.removeProperty('opacity');
  element.style.removeProperty('transform');
}

function play(element: HTMLElement, entering: boolean, distance: number) {
  stop(element);
  if (reduced()) return null;
  const control = animate(element, {
    opacity: entering ? [0, 1] : [1, 0],
    transform: entering
      ? [`translateY(${distance}px) scale(.99)`, 'translateY(0) scale(1)']
      : ['translateY(0) scale(1)', `translateY(${Math.max(2, distance / 2)}px) scale(.995)`],
  }, { duration: entering ? .18 : .12, ease: entering ? [0.2, 0.7, 0.2, 1] : 'easeIn' });
  running.set(element, control);
  void control.finished.then(() => {
    if (running.get(element) === control) stop(element);
  }).catch(() => {});
  return control;
}

export function showDialog(dialog: HTMLDialogElement) {
  closing.set(dialog, ++sequence);
  dialog.style.removeProperty('pointer-events');
  if (!boundDialogs.has(dialog)) {
    boundDialogs.add(dialog);
    dialog.addEventListener('cancel', event => {
      if (event.defaultPrevented) return;
      event.preventDefault();
      void closeDialog(dialog, 'cancel');
    });
    dialog.addEventListener('submit', event => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement) || form.method !== 'dialog') return;
      event.preventDefault();
      const value = (event as SubmitEvent).submitter instanceof HTMLButtonElement
        ? (event as SubmitEvent).submitter!.getAttribute('value') ?? '' : '';
      void closeDialog(dialog, value);
    });
  }
  if (!dialog.open) dialog.showModal();
  play(dialog, true, 8);
}

export async function closeDialog(dialog: HTMLDialogElement, returnValue?: string) {
  if (!dialog.open) return;
  const token = ++sequence;
  closing.set(dialog, token);
  dialog.style.pointerEvents = 'none';
  const control = play(dialog, false, 8);
  if (control) await control.finished.catch(() => {});
  if (closing.get(dialog) !== token) return;
  stop(dialog);
  dialog.style.removeProperty('pointer-events');
  dialog.close(returnValue);
}

export function showMenu(menu: HTMLElement) {
  closing.set(menu, ++sequence);
  menu.hidden = false;
  menu.inert = false;
  menu.style.removeProperty('pointer-events');
  play(menu, true, 5);
}

export function reveal(element: HTMLElement) {
  closing.set(element, ++sequence);
  play(element, true, 6);
}

export async function dismiss(element: HTMLElement) {
  const token = ++sequence;
  closing.set(element, token);
  const control = play(element, false, 6);
  if (control) await control.finished.catch(() => {});
  return closing.get(element) === token;
}

export async function hideMenu(menu: HTMLElement) {
  if (menu.hidden) return;
  const token = ++sequence;
  closing.set(menu, token);
  menu.inert = true;
  menu.style.pointerEvents = 'none';
  const control = play(menu, false, 5);
  if (control) await control.finished.catch(() => {});
  if (closing.get(menu) !== token) return;
  stop(menu);
  menu.hidden = true;
  menu.style.removeProperty('pointer-events');
}

export function showOverlay(overlay: HTMLElement) {
  const panel = overlay.lastElementChild as HTMLElement | null;
  closing.set(overlay, ++sequence);
  overlay.classList.remove('hidden');
  overlay.classList.add('flex');
  overlay.inert = false;
  if (panel) play(panel, true, 8);
}

export async function hideOverlay(overlay: HTMLElement) {
  if (overlay.classList.contains('hidden')) return;
  const token = ++sequence;
  const panel = overlay.lastElementChild as HTMLElement | null;
  closing.set(overlay, token);
  overlay.inert = true;
  const control = panel ? play(panel, false, 8) : null;
  if (control) await control.finished.catch(() => {});
  if (closing.get(overlay) !== token) return;
  overlay.classList.add('hidden');
  overlay.classList.remove('flex');
  if (panel) stop(panel);
  overlay.inert = false;
}

export function cancelUiMotion() {
  for (const element of running.keys()) stop(element);
}
