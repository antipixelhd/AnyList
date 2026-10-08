export const MINIMUM_BROWSE_SKELETON_MS = 350;

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
export function createBrowseLoading(region: HTMLElement, skeleton: HTMLElement, now = () => performance.now()) {
  let shownAt: number | undefined;
  const grid = skeleton.querySelector<HTMLElement>('[data-skeleton-grid]')!;
  const sections = skeleton.querySelector<HTMLElement>('[data-skeleton-sections]')!;
  let active = grid;
  return {
    show(append = false, categories = false) {
      const hasCards = [...region.querySelectorAll<HTMLElement>("[data-browse-card]")]
        .some((card) => card.getClientRects().length > 0);
      if (!append && hasCards) return;
      const destination = categories && !append ? sections : grid;
      if (active !== destination) this.finish();
      active = destination;
      grid.hidden = active !== grid;
      sections.hidden = active !== sections;
      if (shownAt === undefined) {
        // Fresh slots receive their own entrance each time the loading view opens.
        active.querySelectorAll<HTMLElement>('.browse-skeleton').forEach(slot => {
          const fresh = slot.cloneNode(true) as HTMLElement;
          fresh.querySelectorAll('[data-browse-appeared]').forEach(element => element.removeAttribute('data-browse-appeared'));
          fresh.querySelectorAll('.browse-scroll-reveal').forEach(element => element.classList.remove('browse-scroll-reveal'));
          slot.replaceWith(fresh);
        });
        shownAt = now();
      }
      region.dataset.browseLoading = append ? "append" : "empty";
      skeleton.hidden = false;
    },
    remaining() {
      return shownAt === undefined ? 0 : Math.max(0, MINIMUM_BROWSE_SKELETON_MS - (now() - shownAt));
    },
    handoff(covers: HTMLElement[]) {
      if (shownAt === undefined) return;
      const slots = [...active.querySelectorAll<HTMLElement>('.browse-skeleton-body')];
      covers.forEach((cover, index) => {
        if (slots[index]?.hasAttribute('data-browse-appeared')) {
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
