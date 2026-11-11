export const LOADING_REVEAL_WAIT_MS=100;
export const LOADING_REVEAL_DURATION_MS=120;
const active = new Map<HTMLElement, () => void>();

/** Visual polish only: content is already usable and its normal styles stay intact. */
function reveal(element: HTMLElement, signal?: AbortSignal) {
  const view = element.ownerDocument?.defaultView;
  if (signal?.aborted || !element.isConnected || !view || typeof element.animate !== 'function' ||
    view.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  // A module entering the viewport already has its own motion.
  if (element.closest('.media-scroll-reveal, .browse-scroll-reveal')) return;
  const bounds = element.getBoundingClientRect();
  if (!bounds.width || !bounds.height || bounds.bottom <= 0 || bounds.top >= view.innerHeight) return;
  active.get(element)?.();
  const animation = element.animate({ opacity: [.6, 1] }, {
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
      for (const element of Array.isArray(target) ? target : [target]) reveal(element, signal);
    },
    cancel() {
      started = undefined;
    },
  };
}

export function cancelLoadingReveals(root?: HTMLElement) {
  for (const [element, cancel] of active) {
    if (!root || element === root || root.contains(element)) cancel();
  }
}
