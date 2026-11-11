import {createLoadingReveal} from './loading-reveal.ts';

export const MINIMUM_BROWSE_SKELETON_MS = 0;

/** Cached images paint immediately; slow images share the bounded skeleton timing. */
export function prepareBrowsePoster(image: HTMLImageElement, signal: AbortSignal, readyPosters = new Set<string>()) {
  const cover = image.closest<HTMLElement>(".browse-cover");
  if (!cover || image.hidden || signal.aborted) return;
  const artworkKey = image.src ? `${image.src}|${image.srcset || ''}|${image.sizes || ''}` : '';
  if (image.complete || (artworkKey && readyPosters.has(artworkKey))) {
    if (artworkKey && image.naturalWidth > 0) readyPosters.add(artworkKey);
    cover.setAttribute("data-poster-ready", "");
    return;
  }
  cover.setAttribute("data-poster-pending", "");
  cover.setAttribute("data-poster-loading", "");
  const shownAt = performance.now();
  const reveal=createLoadingReveal();reveal.begin(shownAt);
  let finished = false;
  let hideTimer: ReturnType<typeof setTimeout> | undefined;
  const cleanup = () => {
    clearTimeout(maxTimer);
    clearTimeout(hideTimer);
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
    const remaining = Math.max(0, MINIMUM_BROWSE_SKELETON_MS - (performance.now() - shownAt));
    if (remaining) hideTimer = setTimeout(cleanup, remaining);
    else cleanup();
    if(event.type==='load')reveal.finish(image,signal);else reveal.cancel();
  };
  const maxTimer = setTimeout(() => {
    // Stop a stalled shimmer, but keep listening so lazy/late artwork fades
    // when it actually arrives rather than spending its fade offscreen.
    cover.removeAttribute("data-poster-loading");
    cover.removeAttribute("data-poster-pending");
  }, 1500);
  image.addEventListener("load", finish, { once: true });
  image.addEventListener("error", finish, { once: true });
  signal.addEventListener("abort", cleanup, { once: true });
}

/** Existing content stays readable; only missing content needs a skeleton. */
export function createBrowseLoading(region: HTMLElement, skeleton: HTMLElement, now = () => performance.now(), minimumMs = MINIMUM_BROWSE_SKELETON_MS) {
  let shownAt: number | undefined;
  const grid = skeleton.querySelector<HTMLElement>('[data-skeleton-grid]')!;
  const sections = skeleton.querySelector<HTMLElement>('[data-skeleton-sections]')!;
  const timeline = skeleton.querySelector<HTMLElement>('[data-skeleton-timeline]')!;
  let active = grid;
  return {
    show(append = false, categories = false, timelineView = false) {
      const hasCards = [...region.querySelectorAll<HTMLElement>("[data-browse-card]")]
        .some((card) => card.getClientRects().length > 0);
      if (!append && hasCards) return;
      const destination = timelineView ? timeline : categories && !append ? sections : grid;
      if (active !== destination) this.finish();
      active = destination;
      grid.hidden = active !== grid;
      sections.hidden = active !== sections;
      timeline.hidden = active !== timeline;
      shownAt ??= now();
      region.dataset.browseLoading = append ? "append" : "empty";
      skeleton.hidden = false;
    },
    remaining() {
      return shownAt === undefined ? 0 : Math.max(0, minimumMs - (now() - shownAt));
    },
    handoff(covers: HTMLElement[]) {
      if (shownAt === undefined) return;
      const slots = [...active.querySelectorAll<HTMLElement>('.browse-skeleton-body')];
      covers.forEach((cover, index) => {
        const bounds = slots[index]?.getBoundingClientRect();
        if (bounds && bounds.height > 0 && bounds.top < innerHeight && bounds.bottom > 0) {
          cover.setAttribute('data-browse-appeared', '');
        }
      });
    },
    async settle(signal: AbortSignal) {
      const remaining = this.remaining();
      if (!remaining || signal.aborted) return;
      await new Promise<void>(resolve => {
        const finish = () => {
          clearTimeout(timer);
          signal.removeEventListener("abort", finish);
          resolve();
        };
        const timer = setTimeout(finish, remaining);
        signal.addEventListener("abort", finish, { once: true });
      });
    },
    finish() {
      shownAt = undefined;
      delete region.dataset.browseLoading;
      skeleton.hidden = true;
    },
  };
}
