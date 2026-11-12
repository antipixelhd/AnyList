const MINIMUM_SKELETON_MS = 350;
const inheritedSkeletons = new WeakSet<HTMLElement>();
const visibilityCallbacks = new WeakMap<Element, () => void>();
let visibilityObserver: IntersectionObserver | undefined;

export function trackBrowseSkeleton(element: HTMLElement) {
  let paintedAt: number | undefined;
  let stopped = false;
  const mark = () => {
    if (stopped) return;
    paintedAt ??= performance.now();
    if (visibilityCallbacks.get(element) === mark) {
      visibilityObserver?.unobserve(element);
      visibilityCallbacks.delete(element);
    }
  };
  if (typeof IntersectionObserver === 'function') {
    visibilityObserver ??= new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (entry.isIntersecting && entry.intersectionRect.width && entry.intersectionRect.height) {
          visibilityCallbacks.get(entry.target)?.();
        }
      }
    });
    visibilityCallbacks.set(element, mark);
    visibilityObserver.observe(element);
  }
  const frame = typeof requestAnimationFrame === 'function' && requestAnimationFrame(() => {
    if (!element.isConnected) return;
    const bounds = element.getBoundingClientRect();
    if (bounds.width && bounds.height && bounds.top < innerHeight && bounds.bottom > 0) mark();
  });
  const stop = () => {
    stopped = true;
    if (frame) cancelAnimationFrame(frame);
    if (visibilityCallbacks.get(element) === mark) {
      visibilityObserver?.unobserve(element);
      visibilityCallbacks.delete(element);
    }
  };
  return {
    get painted() { return paintedAt !== undefined; },
    stop,
    settle(signal: AbortSignal) {
      stop();
      const remaining = paintedAt === undefined ? 0 : MINIMUM_SKELETON_MS - (performance.now() - paintedAt);
      if (remaining <= 0 || signal.aborted) return;
      return new Promise<void>(resolve => {
        const finish = () => {
          clearTimeout(timer);
          signal.removeEventListener('abort', finish);
          resolve();
        };
        const timer = setTimeout(finish, remaining);
        signal.addEventListener('abort', finish, {once: true});
      });
    },
  };
}

export function handoffBrowseSkeletons(source: HTMLElement, cards: Iterable<HTMLElement>) {
  const slots = [...source.querySelectorAll<HTMLElement>('.browse-skeleton-body')];
  for (const [index, card] of [...cards].entries()) {
    const slot = slots[index];
    if (!slot?.hasAttribute('data-browse-appeared')) continue;
    card.setAttribute('data-browse-appeared', '');
    card.classList.remove('browse-scroll-reveal');
    const cover = card.querySelector<HTMLElement>('.browse-cover');
    if (cover) inheritedSkeletons.add(cover);
    const entrance = slot.getAnimations().find(animation => animation.playState === 'running' &&
      (animation.effect as KeyframeEffect | null)?.getKeyframes().some(frame => frame.scale === '0.92'));
    const effect = entrance?.effect as KeyframeEffect | undefined;
    if (effect && entrance?.startTime != null) {
      const continuation = card.animate(effect.getKeyframes(), {...effect.getTiming(), fill: 'none'});
      continuation.startTime = entrance.startTime;
    }
  }
}

/** Cached images paint immediately; a visible placeholder has time to settle. */
export function prepareBrowsePoster(image: HTMLImageElement, signal: AbortSignal, readyPosters = new Set<string>()) {
  const cover = image.closest<HTMLElement>(".browse-cover");
  if (!cover || image.hidden || signal.aborted) return;
  const artworkKey = image.src ? `${image.src}|${image.srcset || ''}|${image.sizes || ''}` : '';
  if (image.complete || image.naturalWidth > 0 || (artworkKey && readyPosters.has(artworkKey))) {
    if (artworkKey && image.naturalWidth > 0) readyPosters.add(artworkKey);
    cover.setAttribute("data-poster-ready", "");
    return;
  }
  cover.setAttribute("data-poster-pending", "");
  cover.setAttribute("data-poster-loading", "");
  const timing = trackBrowseSkeleton(cover);
  let finished = false;
  const cleanup = () => {
    clearTimeout(maxTimer);
    timing.stop();
    image.removeEventListener("load", finish);
    image.removeEventListener("error", finish);
    signal.removeEventListener("abort", cleanup);
    cover.removeAttribute("data-poster-loading");
    cover.removeAttribute("data-poster-pending");
    if (!signal.aborted) cover.setAttribute("data-poster-ready", "");
  };
  const finish = (event: Event) => {
    if (finished) return;
    if (event.type === 'load' && artworkKey) readyPosters.add(artworkKey);
    finished = true;
    const reveal = () => {
      if (signal.aborted) return;
      if ((timing.painted || inheritedSkeletons.has(cover)) && event.type === 'load') {
        cover.setAttribute('data-poster-revealing', '');
      }
      cleanup();
    };
    const wait = inheritedSkeletons.has(cover) ? undefined : timing.settle(signal);
    if (wait) void wait.then(reveal);
    else reveal();
  };
  const maxTimer = setTimeout(() => {
    // Release a stalled placeholder while the browser can still finish the image.
    cover.removeAttribute("data-poster-loading");
    cover.removeAttribute("data-poster-pending");
    timing.stop();
  }, 1500);
  image.addEventListener("load", finish, { once: true });
  image.addEventListener("error", finish, { once: true });
  signal.addEventListener("abort", cleanup, { once: true });
}

/** Existing content stays readable; only missing content needs a skeleton. */
export function createBrowseLoading(region: HTMLElement, skeleton: HTMLElement) {
  const grid = skeleton.querySelector<HTMLElement>('[data-skeleton-grid]')!;
  const sections = skeleton.querySelector<HTMLElement>('[data-skeleton-sections]')!;
  const timeline = skeleton.querySelector<HTMLElement>('[data-skeleton-timeline]')!;
  let active = [grid, sections, timeline].find(view => !view.hidden) ?? grid;
  let timing = skeleton.hidden ? undefined : trackBrowseSkeleton(active);
  return {
    show(mode: "replace" | "append", categories = false, timelineView = false) {
      const append = mode === "append";
      const destination = timelineView ? timeline : categories && !append ? sections : grid;
      if (active !== destination) this.finish();
      active = destination;
      grid.hidden = active !== grid;
      sections.hidden = active !== sections;
      timeline.hidden = active !== timeline;
      region.dataset.browseLoading = append ? "append" : "empty";
      const fresh = skeleton.hidden || !timing;
      skeleton.hidden = false;
      if (fresh) {
        for (const slot of active.querySelectorAll<HTMLElement>('.browse-skeleton-body[data-browse-appeared], .browse-skeleton-body.browse-scroll-reveal')) {
          const clone = slot.cloneNode(true) as HTMLElement;
          clone.removeAttribute('data-browse-appeared');
          clone.classList.remove('browse-scroll-reveal');
          slot.replaceWith(clone);
        }
        timing = trackBrowseSkeleton(active);
      }
    },
    settle(signal: AbortSignal) {
      return timing?.settle(signal);
    },
    handoff(cards: Iterable<HTMLElement>) {
      if (!skeleton.hidden) handoffBrowseSkeletons(active, cards);
    },
    finish() {
      timing?.stop();
      timing = undefined;
      delete region.dataset.browseLoading;
      skeleton.hidden = true;
    },
  };
}
