type Anchor = {left: number; top: number; bottom: number; width: number};
type Viewport = {left: number; top: number; width: number; height: number};

export function dropdownPlacement(anchor: Anchor, viewport: Viewport, contentHeight: number) {
  const margin = 8, gap = 5;
  const bottom = viewport.top + viewport.height - margin;
  const above = Math.max(0, anchor.top - gap - viewport.top - margin);
  const below = Math.max(0, bottom - anchor.bottom - gap);
  const desired = Math.min(290, contentHeight);
  const upward = below < desired && above > below;
  const height = Math.min(desired, upward ? above : below);
  const width = Math.min(anchor.width, Math.max(0, viewport.width - 2 * margin));
  const left = Math.max(viewport.left + margin, Math.min(anchor.left, viewport.left + viewport.width - margin - width));
  const top = Math.max(viewport.top + margin, Math.min(upward ? anchor.top - gap - height : anchor.bottom + gap, bottom - height));
  return {left, top, width, maxHeight: height, upward};
}

export function placeDropdown(trigger: HTMLElement, menu: HTMLElement) {
  const viewport = window.visualViewport;
  const bounds = {left: viewport?.offsetLeft ?? 0, top: viewport?.offsetTop ?? 0,
    width: viewport?.width ?? innerWidth, height: viewport?.height ?? innerHeight};
  const anchor = trigger.getBoundingClientRect();
  const hidden = menu.hidden;
  menu.classList.add('tracker-floating-dropdown');
  menu.style.width = `${Math.min(anchor.width, Math.max(0, bounds.width - 16))}px`;
  menu.hidden = false;
  const style = getComputedStyle(menu);
  const contentHeight = menu.scrollHeight + parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
  const placement = dropdownPlacement(anchor, bounds, contentHeight);
  Object.assign(menu.style, {left: `${placement.left}px`, top: `${placement.top}px`,
    width: `${placement.width}px`, maxHeight: `${placement.maxHeight}px`, bottom: 'auto', right: 'auto'});
  menu.dataset.placement = placement.upward ? 'top' : 'bottom';
  menu.hidden = hidden;
}
