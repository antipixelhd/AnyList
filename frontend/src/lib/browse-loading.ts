export const MINIMUM_BROWSE_SKELETON_MS = 350;

/** Cached images paint immediately; slow images share the bounded skeleton timing. */
export function prepareBrowsePoster(image: HTMLImageElement, signal: AbortSignal) {
  const cover = image.closest<HTMLElement>(".browse-cover");
  if (!cover || image.hidden || image.complete || signal.aborted) return;
  let shownAt: number | undefined;
  let finished = false;
  let hideTimer: ReturnType<typeof setTimeout> | undefined;
  const showTimer = setTimeout(() => {
    if (finished || signal.aborted) return;
    shownAt = performance.now();
    cover.setAttribute("data-poster-loading", "");
  }, 120);
  const cleanup = () => {
    clearTimeout(showTimer);
    clearTimeout(maxTimer);
    clearTimeout(hideTimer);
    image.removeEventListener("load", finish);
    image.removeEventListener("error", finish);
    signal.removeEventListener("abort", cleanup);
    cover.removeAttribute("data-poster-loading");
  };
  const finish = () => {
    if (finished) return;
    finished = true;
    clearTimeout(showTimer);
    const remaining = shownAt === undefined ? 0 : Math.max(0, MINIMUM_BROWSE_SKELETON_MS - (performance.now() - shownAt));
    if (remaining) hideTimer = setTimeout(cleanup, remaining);
    else cleanup();
  };
  const maxTimer = setTimeout(finish, 1500);
  image.addEventListener("load", finish, { once: true });
  image.addEventListener("error", finish, { once: true });
  signal.addEventListener("abort", cleanup, { once: true });
}

/** Existing content stays readable; only missing content needs a skeleton. */
export function createBrowseLoading(region: HTMLElement, skeleton: HTMLElement, now = () => performance.now()) {
  let shownAt: number | undefined;
  return {
    show(append = false) {
      const hasCards = [...region.querySelectorAll<HTMLElement>("[data-browse-card]")]
        .some((card) => card.getClientRects().length > 0);
      if (!append && hasCards) return;
      if (shownAt === undefined) shownAt = now();
      region.dataset.browseLoading = append ? "append" : "empty";
      skeleton.hidden = false;
    },
    remaining() {
      return shownAt === undefined ? 0 : Math.max(0, MINIMUM_BROWSE_SKELETON_MS - (now() - shownAt));
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
