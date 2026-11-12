/** Cached images paint immediately; slow images keep their reserved slot. */
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
  let finished = false;
  const cleanup = () => {
    clearTimeout(maxTimer);
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
    cleanup();
  };
  const maxTimer = setTimeout(() => {
    // Release a stalled placeholder while the browser can still finish the image.
    cover.removeAttribute("data-poster-loading");
    cover.removeAttribute("data-poster-pending");
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
      region.dataset.browseLoading = append ? "append" : "empty";
      skeleton.hidden = false;
    },
    finish() {
      delete region.dataset.browseLoading;
      skeleton.hidden = true;
    },
  };
}
