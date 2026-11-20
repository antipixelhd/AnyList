/** Seed the replacement bar from the currently painted state before CSS settles it. */
export function carryAppbarAppearance(previous: HTMLElement, incoming: HTMLElement, carryPosition = false) {
  const view = previous.ownerDocument.defaultView;
  if (!view || view.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const carryBackground = !view.matchMedia('(max-width:1040px)').matches;
  if (!carryBackground && !carryPosition) return;
  const appearance = view.getComputedStyle(previous);
  if (carryBackground) incoming.style.backgroundColor = appearance.backgroundColor;
  if (carryPosition) incoming.style.transform = appearance.transform;
  return () => {
    // Establish the carried value on the connected replacement before releasing it.
    void incoming.offsetWidth;
    if (carryBackground) incoming.style.removeProperty('background-color');
    if (carryPosition) incoming.style.removeProperty('transform');
  };
}
