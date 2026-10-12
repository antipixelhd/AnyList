import { cancelUiMotion, dismiss, reveal } from './ui-motion.ts';

/** Share browse's short exit/entrance, keeping only the latest requested view. */
export function createContentTransition(element: HTMLElement, signal: AbortSignal, motion = {dismiss, reveal, cancel:cancelUiMotion}) {
  let version = 0;
  let interrupt: (() => void) | undefined;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const cancel = () => { version++; interrupt?.(); motion.cancel(element); element.inert = false; };
  signal.addEventListener('abort', cancel, {once:true});
  reduced.addEventListener('change', () => { motion.cancel(element); if (reduced.matches) interrupt?.(); }, {signal});
  return async (change: () => void) => {
    const current = ++version;
    interrupt?.();
    if (signal.aborted) return;
    if (!reduced.matches && element.getClientRects().length) {
      element.inert = true;
      const interrupted = new Promise<void>(resolve => { interrupt = resolve; });
      await Promise.race([motion.dismiss(element), interrupted]);
    }
    if (signal.aborted || current !== version) return;
    interrupt = undefined;
    try { change(); }
    finally { element.inert = false; }
    motion.reveal(element);
  };
}

/** Animate a native disclosure without losing its keyboard or no-JS behavior. */
export function mountDetailsMotion(details: HTMLDetailsElement, signal: AbortSignal) {
  const summary = details.querySelector<HTMLElement>(':scope > summary');
  if (!summary) return;
  let expanded = details.open, animation: Animation | undefined;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const settle = () => {
    animation?.cancel(); animation = undefined;
    details.open = expanded;
    details.style.removeProperty('overflow');
  };
  summary.addEventListener('click', event => {
    if (event.target instanceof Element && event.target.closest('button,a,input,select,textarea,label')) return;
    event.preventDefault();
    const start = details.getBoundingClientRect().height;
    animation?.cancel();
    expanded = !expanded;
    details.style.overflow = 'hidden';
    details.open = expanded;
    const end = details.getBoundingClientRect().height;
    if (reduced.matches || start === end) { settle(); return; }
    details.open = true;
    const current = details.animate([{height:`${start}px`},{height:`${end}px`}], {
      duration:260, easing:'cubic-bezier(.2,.7,.2,1)', fill:'both',
    });
    animation = current;
    current.finished.then(() => { if (animation === current) settle(); }).catch(() => {});
  }, {signal});
  reduced.addEventListener('change', settle, {signal});
  signal.addEventListener('abort', settle, {once:true});
}
