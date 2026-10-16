export type DropdownTiming = {
  duration: number;
  ease: 'easeIn' | [number, number, number, number];
};

export function dropdownTiming(open: boolean, fading = false): DropdownTiming {
  return {
    duration: fading ? (open ? .24 : .16) : (open ? .26 : .18),
    ease: open ? [0.2, 0.7, 0.2, 1] : 'easeIn',
  };
}

type Arrow = HTMLElement | SVGElement;
type Control = { stop(): void; cancel(): void; finished: Promise<unknown> };
type AnimateArrow = (arrow: Arrow, values: { transform: [string, string] }, timing: DropdownTiming) => Control;

function rotationFrame(transform: string, open: boolean) {
  // Computed matrices lose the sign of a half turn. Keep the angle negative so
  // closing and interrupted animations return through the right side as well.
  const matrix = /^matrix\(([^)]+)\)$/.exec(transform);
  if (matrix) {
    const [a, b] = matrix[1].split(',').map(Number);
    return `rotate(${-Math.abs(Math.atan2(b, a) * 180 / Math.PI)}deg)`;
  }
  return transform || `rotate(${open ? 0 : -180}deg)`;
}

function findArrow(panel: HTMLElement): Arrow | null {
  const trigger = panel.id ? [...panel.ownerDocument.querySelectorAll<HTMLElement>('[aria-controls], [data-target], [data-warning-toggle]')]
    .find(element => element.getAttribute('aria-controls')?.split(/\s+/).includes(panel.id) || element.dataset.target === panel.id || element.dataset.warningToggle === panel.id) : null;
  const summary = panel.parentElement?.matches('details')
    ? panel.parentElement.querySelector<HTMLElement>(':scope > summary') : null;
  return (trigger || summary)?.querySelector<Arrow>(
    '.tracker-select-chevron > svg, .ui-icon > svg[data-icon="chevron-down"], [data-dropdown-arrow], .collapsible-chevron, .warn-chevron, summary > svg, #user-chevron',
  ) ?? null;
}

/** Use the panel's own Motion timing; reverse from the current angle on interruption. */
export function createDropdownArrowMotion(animate: AnimateArrow, reduced: () => boolean, resolve = findArrow) {
  const running = new Map<HTMLElement, { arrow: Arrow; open: boolean; control: Control }>();
  function settle(panel: HTMLElement) {
    const current = running.get(panel);
    if (!current) return;
    current.control.cancel();
    current.arrow.style.transform = `rotate(${current.open ? -180 : 0}deg)`;
    running.delete(panel);
  }
  return {
    play(panel: HTMLElement, open: boolean, timing: DropdownTiming) {
      const previous = running.get(panel);
      previous?.control.stop();
      running.delete(panel);
      const arrow = resolve(panel);
      if (!arrow) return;
      // Legacy CSS transitions must not add a second, independent rotation clock.
      arrow.style.transition = 'none';
      const transform = `rotate(${open ? -180 : 0}deg)`;
      if (reduced() || timing.duration === 0) { arrow.style.transform = transform; return; }
      const from = rotationFrame(arrow.style.transform, open);
      const control = animate(arrow, { transform: [from, transform] }, timing);
      running.set(panel, { arrow, open, control });
      void control.finished.then(() => {
        if (running.get(panel)?.control === control) settle(panel);
      }).catch(() => {});
    },
    cancel(root?: HTMLElement) {
      for (const [panel, { arrow }] of running) {
        if (!root || root.contains(panel) || root.contains(arrow)) settle(panel);
      }
    },
  };
}
