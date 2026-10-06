// Introduce offscreen modules once, without making readers wait for content.
export function initializeScrollMotion(root: HTMLElement, signal: AbortSignal) {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  if (reduced.matches || signal.aborted) return;
  const mobile = matchMedia('(max-width: 650px), (pointer: coarse)');
  const pending = new Map<HTMLElement, Animation | null>();
  const entrances: Animation[] = [];
  let frame = 0;
  let lastY = scrollY;
  let lastScrollAt = performance.now();
  let fastUntil = 0;

  const viewport = () => {
    const view = window.visualViewport;
    return {bottom: view ? view.offsetTop + view.height : innerHeight, height: view?.height ?? innerHeight};
  };
  const complete = (element: HTMLElement) => {
    pending.get(element)?.cancel();
    element.classList.remove('lab-scroll-reveal');
    pending.delete(element);
  };
  const start = (element: HTMLElement) => {
    const animation = element.animate([
      {opacity: mobile.matches ? 1 : 0, translate: `0 ${mobile.matches ? 2 : 6}px`},
      {opacity: 1, translate: '0 0'},
    ], {duration: mobile.matches ? 160 : 340, easing: 'cubic-bezier(.2,.65,.3,1)', fill: 'both'});
    pending.set(element, animation);
    animation.addEventListener('finish', () => complete(element), {once: true});
  };

  root.querySelectorAll<HTMLElement>('.lab-module').forEach(element => {
    const bounds = element.getBoundingClientRect();
    if (element.getClientRects().length && bounds.top < viewport().bottom) {
      if (bounds.bottom > 0) entrances.push(element.animate([
        {opacity: 0, transform: 'translateY(7px)'},
        {opacity: 1, transform: 'translateY(0)'},
      ], {duration: 450, easing: 'ease'}));
      return;
    }
    // Set the starting appearance BEFORE entry, rather than resetting an
    // already visible component to transparent in an observer callback.
    element.classList.add('lab-scroll-reveal');
    pending.set(element, null);
  });

  const update = () => {
    frame = 0;
    const {bottom, height} = viewport();
    const lead = mobile.matches ? 0 : 16;
    const readingBoundary = bottom - Math.min(mobile.matches ? 64 : 128, height * .18);
    const fast = performance.now() < fastUntil;
    // Batch position reads before starting/cancelling animations. Use the
    // unshifted box, so the entrance itself cannot move its trigger point.
    const positions = [...pending].map(([element, animation]) => {
      const visible = !!element.getClientRects().length;
      const shift = parseFloat(getComputedStyle(element).translate.split(' ')[1]) || 0;
      return {element, animation, visible, top: element.getBoundingClientRect().top - shift};
    });
    for (const {element, animation, visible, top} of positions) {
      if (!visible) {
        if (animation) complete(element);
        continue;
      }
      if (top > bottom + lead) continue;
      // Jumping to an anchor, flinging, or reaching the reading area should
      // expose content immediately, even if its fade has not finished yet.
      if (fast || top <= readingBoundary) {complete(element);continue;}
      if (!animation && !element.matches('[data-lab-pending], [data-lab-loading]')) start(element);
    }
  };
  const schedule = () => {
    if (!frame && pending.size) frame = requestAnimationFrame(update);
  };
  const onScroll = () => {
    const now = performance.now();
    const elapsed = Math.max(16, Math.min(200, now - lastScrollAt));
    if (Math.abs(scrollY - lastY) / elapsed * 1000 > 2400) fastUntil = now + 160;
    lastY = scrollY;
    lastScrollAt = now;
    schedule();
  };
  const finish = () => {
    cancelAnimationFrame(frame);
    frame = 0;
    for (const element of pending.keys()) complete(element);
    entrances.forEach(animation => animation.cancel());
  };
  update();
  window.addEventListener('scroll', onScroll, {passive: true, signal});
  window.addEventListener('resize', schedule, {signal});
  window.visualViewport?.addEventListener('resize', schedule, {signal});
  window.visualViewport?.addEventListener('scroll', schedule, {passive: true, signal});
  // A viewport/input change must not leave an old desktop fade on mobile.
  mobile.addEventListener('change', finish, {signal});
  reduced.addEventListener('change', () => {if (reduced.matches) finish();}, {signal});
  root.addEventListener('focusin', event => {
    const element = (event.target as Element | null)?.closest<HTMLElement>('.lab-module');
    if (element && pending.has(element)) complete(element);
  }, {signal});
  // Loading completion can start a pending reveal, but cannot replay one.
  const visibility = new MutationObserver(schedule);
  visibility.observe(root, {subtree: true, attributes: true, attributeFilter: ['hidden', 'data-lab-pending', 'data-lab-loading']});
  const layout = new ResizeObserver(schedule);
  layout.observe(root);
  signal.addEventListener('abort', () => {visibility.disconnect();layout.disconnect();finish();}, {once: true});
}
