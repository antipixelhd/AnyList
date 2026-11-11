export const LOADING_REVEAL_WAIT_MS=100;
export const LOADING_REVEAL_DURATION_MS=120;
export const ARTWORK_PULSE_DELAY_MS=120;
export const ARTWORK_PULSE_DURATION_MS=180;
const active = new Map<HTMLElement, () => void>();
const artworkPulses = new WeakMap<HTMLElement, () => void>();

/** Visual polish only: content is already usable and its normal styles stay intact. */
export function revealReadyContent(element: HTMLElement, signal?: AbortSignal) {
  const view = element.ownerDocument?.defaultView;
  if (signal?.aborted || !element.isConnected || !view || typeof element.animate !== 'function' ||
    view.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  // A module entering the viewport already has its own motion.
  if (element.closest('.media-scroll-reveal, .browse-scroll-reveal')) return;
  const bounds = element.getBoundingClientRect();
  if (!bounds.width || !bounds.height || bounds.bottom <= 0 || bounds.top >= view.innerHeight) return;
  active.get(element)?.();
  const animation = element.animate({ opacity: [.92, 1] }, {
    duration: LOADING_REVEAL_DURATION_MS,
    easing: 'cubic-bezier(.16,1,.3,1)',
  });
  const cleanup = () => {
    signal?.removeEventListener('abort', cancel);
    if (active.get(element) === cancel) active.delete(element);
  };
  const cancel = () => {
    animation.cancel();
    cleanup();
  };
  active.set(element, cancel);
  signal?.addEventListener('abort', cancel, { once: true });
  void animation.finished.then(cleanup, cleanup);
}

/** Cached content and waits under 100ms never acquire a loading animation. */
export function createLoadingReveal(now = () => performance.now()) {
  let started: number | undefined;
  return {
    begin(since = now()) {
      started ??= since;
    },
    finish(target: HTMLElement | HTMLElement[], signal?: AbortSignal) {
      const elapsed = started === undefined ? 0 : now() - started;
      started = undefined;
      if (elapsed < LOADING_REVEAL_WAIT_MS) return;
      for (const element of Array.isArray(target) ? target : [target]) revealReadyContent(element, signal);
    },
    cancel() {
      started = undefined;
    },
  };
}

/** A slow artwork slot gets one pulse. Loaded art can paint under its tail. */
export function scheduleArtworkPulse(holder: HTMLElement, signal: AbortSignal) {
  artworkPulses.get(holder)?.();
  const view = holder.ownerDocument?.defaultView;
  const reduced = view?.matchMedia('(prefers-reduced-motion: reduce)');
  if (!view || !reduced || reduced.matches || signal.aborted) return () => {};
  let overlay: HTMLElement | undefined;
  let animation: Animation | undefined;
  let delay: ReturnType<typeof setTimeout> | undefined;
  let observer: IntersectionObserver | undefined;
  const clear = () => {
    if (artworkPulses.get(holder) !== clear) return;
    artworkPulses.delete(holder);
    clearTimeout(delay);
    observer?.disconnect();
    animation?.cancel();
    overlay?.remove();
    signal.removeEventListener('abort', clear);
    reduced.removeEventListener('change', clear);
  };
  const inViewport = () => {
    const bounds = holder.getBoundingClientRect();
    return bounds.width > 0 && bounds.height > 0 && bounds.bottom > 0 && bounds.top < view.innerHeight &&
      bounds.right > 0 && bounds.left < view.innerWidth;
  };
  const play = () => {
    if (artworkPulses.get(holder) !== clear) return;
    if (!holder.isConnected) { clear(); return; }
    if (!inViewport()) { waitForView(); return; }
    overlay = holder.ownerDocument.createElement('span');
    overlay.className = 'artwork-skeleton-pulse';
    overlay.setAttribute('aria-hidden', 'true');
    holder.append(overlay);
    animation = overlay.animate({ opacity: [0, .14, 0] }, {
      duration: ARTWORK_PULSE_DURATION_MS,
      easing: 'ease-in-out',
    });
    void animation.finished.then(clear, clear);
  };
  const waitForView = () => {
    if (inViewport()) {
      delay = setTimeout(play, ARTWORK_PULSE_DELAY_MS);
    } else if (typeof view.IntersectionObserver === 'function') {
      observer = new view.IntersectionObserver(entries => {
        if (artworkPulses.get(holder) !== clear || !entries.some(entry => entry.isIntersecting)) return;
        observer?.disconnect();
        observer = undefined;
        delay = setTimeout(play, ARTWORK_PULSE_DELAY_MS);
      });
      observer.observe(holder);
    }
  };
  artworkPulses.set(holder, clear);
  signal.addEventListener('abort', clear, { once: true });
  reduced.addEventListener('change', clear, { once: true });
  waitForView();
  return (ready = false) => {
    if (!ready || !animation) clear();
  };
}

export function cancelLoadingReveals(root?: HTMLElement) {
  for (const [element, cancel] of active) {
    if (!root || element === root || root.contains(element)) cancel();
  }
}
