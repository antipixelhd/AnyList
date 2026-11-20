// Keep mounted works opaque while their chronological/grid positions change.
export function createContributorWorksMotion(region: HTMLElement, signal: AbortSignal) {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const running = new Set<Animation>();
  const cancel = () => { for (const animation of running) animation.cancel(); running.clear(); };
  signal.addEventListener('abort', cancel, {once:true});
  reduced.addEventListener('change', cancel, {signal});
  const visible = (box: DOMRect) => box.width > 0 && box.height > 0 && box.bottom > 0 && box.top < innerHeight;
  return {
    cancel,
    capture() {
      const before = new Map<HTMLElement, DOMRect>();
      for (const card of region.querySelectorAll<HTMLElement>('[data-work-key]')) {
        // Reparenting restarts CSS poster/caption entrances in Chromium. Only
        // suppress completed poster entrances: pending artwork still fades in.
        card.querySelectorAll<HTMLElement>('.browse-card-caption, .browse-cover[data-poster-ready]')
          .forEach(element => element.setAttribute('data-work-settled', ''));
        if (!signal.aborted && !reduced.matches) before.set(card, card.getBoundingClientRect());
      }
      // Read the current visual position before cancelling an interrupted move.
      cancel();
      return () => {
        if (signal.aborted || reduced.matches) return;
        const moves = [...before].filter(([card]) => card.isConnected).map(([card, old]) => ({card, old, next:card.getBoundingClientRect()}));
        for (const {card, old, next} of moves) {
          if (!visible(old) || next.width <= 0 || next.height <= 0) continue;
          const x = old.left - next.left, y = old.top - next.top;
          if (Math.abs(x) < .5 && Math.abs(y) < .5) continue;
          const animation = card.animate([
            {transform:`translate(${x}px, ${y}px)`}, {transform:'translate(0, 0)'},
          ], {duration:280, easing:'cubic-bezier(.2,.7,.2,1)'});
          running.add(animation);
          animation.addEventListener('finish', () => running.delete(animation), {once:true});
        }
      };
    },
  };
}

// Leave already ordered nodes attached, preserving focus and CSS animations.
export function orderContributorNodes(parent: HTMLElement, nodes: HTMLElement[]) {
  let cursor = parent.firstElementChild;
  for (const node of nodes) {
    if (node === cursor) cursor = cursor.nextElementSibling;
    else parent.insertBefore(node, cursor);
  }
}
