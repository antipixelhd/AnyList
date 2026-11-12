/** Seed the replacement bar from the currently painted state before CSS settles it. */
export function carryAppbarAppearance(previous: HTMLElement, incoming: HTMLElement) {
  const view = previous.ownerDocument.defaultView;
  if (!view || view.matchMedia('(max-width:1040px)').matches ||
      view.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const appearance = view.getComputedStyle(previous);
  incoming.style.backgroundColor = appearance.backgroundColor;
  return () => {
    // Establish the carried value on the connected replacement before releasing it.
    void incoming.offsetWidth;
    incoming.style.removeProperty('background-color');
  };
}
