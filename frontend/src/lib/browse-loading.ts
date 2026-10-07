/** Mask existing cards in place so category headings, rows and scroll stay put. */
export function createBrowseLoading(region: HTMLElement, skeleton: HTMLElement) {
  return {
    show(append = false) {
      const hasCards = [...region.querySelectorAll<HTMLElement>("[data-browse-card]")]
        .some((card) => card.getClientRects().length > 0);
      region.dataset.browseLoading = append ? "append" : hasCards ? "replace" : "empty";
      skeleton.hidden = !append && hasCards;
    },
    finish() {
      delete region.dataset.browseLoading;
      skeleton.hidden = true;
    },
  };
}
