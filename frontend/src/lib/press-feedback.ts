import { animate } from 'motion/mini';

const navigation = '.status-filters button, .profile-section-selector__option, .overview-metrics button, [role="tablist"] > button';
const controls = 'button, input[type="button"], input[type="submit"], input[type="reset"], a.button-primary, a.button-secondary, a.button-quiet, a.button-danger, [data-motion-press]';

export function mountPressFeedback(doc: Document, view: Window) {
  const reduced = view.matchMedia('(prefers-reduced-motion: reduce)');
  const active = new Map<HTMLElement, ReturnType<typeof animate>>();
  let held: {element: HTMLElement; pointer?: number; x?: number; y?: number; key?: string} | undefined;

  const usable = (element: HTMLElement) => element.isConnected && element.getClientRects().length > 0 &&
    !element.matches(':disabled, [aria-disabled="true"]') && !element.closest('[inert]');
  function target(event: Event) {
    const element = event.target instanceof Element ? event.target.closest<HTMLElement>(controls) : null;
    if (!element || !usable(element)) return;
    if (element.matches('.alert-icon-button, .quick-rating-close')) return;
    if (!element.matches(navigation) && (element.closest('[data-motion-static]') || element.hasAttribute('aria-expanded'))) return;
    return element;
  }
  function cancel(element: HTMLElement) {
    active.get(element)?.cancel();
    active.delete(element);
    element.style.removeProperty('scale');
  }
  function play(element: HTMLElement, scale: number) {
    if (reduced.matches || !usable(element)) { cancel(element); return; }
    active.get(element)?.stop();
    const animation = animate(element, {scale}, {
      duration: scale === 1 ? .18 : .07,
      ease: scale === 1 ? [.2,.7,.2,1] : 'easeOut',
    });
    active.set(element, animation);
    if (scale === 1) {
      void animation.finished.then(() => {
        if (active.get(element) === animation) cancel(element);
      }).catch(() => {});
    }
  }
  function release() {
    const previous = held;
    held = undefined;
    if (previous) play(previous.element, 1);
  }
  function clear() {
    held = undefined;
    for (const element of active.keys()) cancel(element);
  }
  doc.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !event.isPrimary) return;
    release();
    const element = target(event);
    if (!element) return;
    held = {element, pointer:event.pointerId, x:event.clientX, y:event.clientY};
    play(element, .985);
  }, {capture:true,passive:true});
  doc.addEventListener('pointermove', event => {
    if (!held || held.pointer !== event.pointerId) return;
    const outside = event.target instanceof Node && !held.element.contains(event.target);
    const dragged = event.pointerType !== 'mouse' && Math.hypot(event.clientX - held.x!, event.clientY - held.y!) > 8;
    if (outside || dragged) release();
  }, {capture:true,passive:true});
  doc.addEventListener('pointerout', event => {
    if (held?.pointer === event.pointerId && !held.element.contains(event.relatedTarget as Node | null)) release();
  }, {capture:true,passive:true});
  for (const name of ['pointerup', 'pointercancel'] as const) {
    view.addEventListener(name, event => {
      if (held?.pointer === event.pointerId) release();
    }, {capture:true,passive:true});
  }
  doc.addEventListener('keydown', event => {
    if (event.repeat || (event.key !== 'Enter' && event.key !== ' ')) return;
    const element = target(event);
    if (!element || (element instanceof HTMLAnchorElement && event.key === ' ')) return;
    release();
    held = {element, key:event.key};
    play(element, .985);
  }, true);
  view.addEventListener('keyup', event => {
    if (held?.key === event.key) release();
  }, true);
  doc.addEventListener('focusout', release, true);
  view.addEventListener('scroll', release, {capture:true,passive:true});
  view.addEventListener('blur', clear);
  doc.addEventListener('astro:before-swap', clear);
  view.addEventListener('pagehide', clear);
  doc.addEventListener('visibilitychange', () => { if (doc.hidden) clear(); });
  reduced.addEventListener('change', () => { if (reduced.matches) clear(); });
  const observer = new MutationObserver(() => {
    for (const element of active.keys()) {
      if (usable(element)) continue;
      cancel(element);
      if (held?.element === element) held = undefined;
    }
  });
  observer.observe(doc, {subtree:true,childList:true,attributes:true,attributeFilter:['class','hidden','open','disabled','aria-disabled','inert']});
}
