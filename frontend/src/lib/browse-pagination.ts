/** Pagination follows new scroll intent, never layout changes from fetched results. */
export function createBrowsePaginationDemand(initialScrollY: number) {
  let previousScrollY = initialScrollY;
  let requested = false;
  return {
    advance(scrollY: number, enabled: boolean) {
      if (enabled && scrollY > previousScrollY) requested = true;
      previousScrollY = scrollY;
    },
    consume() {
      const demand = requested;
      requested = false;
      return demand;
    },
    reset(scrollY: number) {
      previousScrollY = scrollY;
      requested = false;
    },
  };
}
