// Reveal each module once, over a short distance at the bottom of the viewport.
// Progress follows scrolling rather than a timer, so a swipe cannot outrun it.
export function initializeScrollMotion(root: HTMLElement, signal: AbortSignal) {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  if (reduced.matches || signal.aborted) return;
  const mobile = matchMedia('(max-width: 650px), (pointer: coarse)');
  const pending = new Map<HTMLElement, {progress: number; shift: number}>();
  const entrances: Animation[] = [];
  let frame = 0;

  const clear = (element: HTMLElement) => {
    element.classList.remove('lab-scroll-reveal');
    element.style.removeProperty('--lab-reveal-y');
    element.style.removeProperty('--lab-reveal-opacity');
  };
  const viewportBottom = () => window.visualViewport
    ? visualViewport!.offsetTop + visualViewport!.height : innerHeight;

  // Already visible content keeps its initial entrance. Hidden modules wait
  // until their tab/state opens before their position is evaluated.
  root.querySelectorAll<HTMLElement>('.lab-module').forEach(element => {
    const bounds = element.getBoundingClientRect();
    if (element.getClientRects().length && bounds.top < viewportBottom()) {
      if (bounds.bottom > 0) entrances.push(element.animate([
        {opacity: 0, transform: 'translateY(7px)'},
        {opacity: 1, transform: 'translateY(0)'},
      ], {duration: 450, easing: 'ease'}));
      return;
    }
    pending.set(element, {progress: 0, shift: 0});
  });

  const update = () => {
    frame = 0;
    const bottom = viewportBottom();
    const distance = mobile.matches ? 2 : 4;
    const band = mobile.matches ? 64 : 96;
    // Read positions before writing styles. Subtract our previous translation
    // so the reveal's own movement cannot affect its scroll progress.
    const updates = [...pending].flatMap(([element, state]) => {
      if (!element.getClientRects().length) return [];
      const top = element.getBoundingClientRect().top - state.shift;
      const progress = Math.max(state.progress, Math.min(1, Math.max(0, (bottom - top) / band)));
      return [{element, state, progress}];
    });
    for (const {element, state, progress} of updates) {
      if (progress === 1) {
        clear(element);
        pending.delete(element);
        continue;
      }
      state.progress = progress;
      state.shift = distance * (1 - progress);
      element.style.setProperty('--lab-reveal-y', `${state.shift}px`);
      element.style.setProperty('--lab-reveal-opacity', String(mobile.matches ? 1 : .9 + .1 * progress));
      element.classList.add('lab-scroll-reveal');
    }
  };
  const schedule = () => {
    if (!frame && pending.size) frame = requestAnimationFrame(update);
  };
  const finish = () => {
    cancelAnimationFrame(frame);
    frame = 0;
    for (const element of pending.keys()) clear(element);
    pending.clear();
    // Unlike a CSS animation class, these entrances cannot restart when a
    // loading attribute is removed. Cancel them on navigation/reduced motion.
    entrances.forEach(animation => animation.cancel());
  };
  update();
  window.addEventListener('scroll', schedule, {passive: true, signal});
  window.addEventListener('resize', schedule, {signal});
  window.visualViewport?.addEventListener('resize', schedule, {signal});
  window.visualViewport?.addEventListener('scroll', schedule, {passive: true, signal});
  mobile.addEventListener('change', schedule, {signal});
  reduced.addEventListener('change', () => {if (reduced.matches) finish();}, {signal});
  // Tab switches, font/skeleton completion, and expanding content can move a
  // module without a scroll event. Never restart modules that have finished.
  const visibility = new MutationObserver(schedule);
  visibility.observe(root, {subtree: true, attributes: true, attributeFilter: ['hidden', 'data-lab-pending', 'data-lab-loading']});
  const layout = new ResizeObserver(schedule);
  layout.observe(root);
  signal.addEventListener('abort', () => {visibility.disconnect();layout.disconnect();finish();}, {once: true});
}
