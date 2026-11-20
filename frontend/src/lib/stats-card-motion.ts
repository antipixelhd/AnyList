export function createStatsCardMotion(grid: HTMLElement, reduced: MediaQueryList, signal: AbortSignal, settleScroll: (card: HTMLElement) => void) {
  const running = new Map<HTMLElement, Animation>();
  const stop = () => {
    running.forEach(animation => animation.cancel());
    running.clear();
  };
  reduced.addEventListener('change', stop, { signal });
  signal.addEventListener('abort', stop, { once: true });

  return (cards: HTMLElement[], animate: boolean) => {
    const outgoing = Array.from(grid.children) as HTMLElement[];
    if (outgoing.length === cards.length && outgoing.every((card, index) => card === cards[index])) return;
    const shouldAnimate = animate && !reduced.matches && grid.isConnected;
    const inView = (box: DOMRect) => box.width > 0 && box.height > 0 && box.bottom > 0 && box.top < innerHeight;
    const previous = new Map<HTMLElement, { box: DOMRect; opacity: number }>();
    if (shouldAnimate) for (const card of outgoing) {
      const box = card.getBoundingClientRect();
      if (inView(box)) previous.set(card, { box, opacity: Number(getComputedStyle(card).opacity) });
    }
    stop();
    grid.replaceChildren(...cards);
    if (!shouldAnimate) return;

    const visible = cards.filter(card => inView(card.getBoundingClientRect()));
    visible.forEach(settleScroll);
    const positions = visible.map(card => ({ card, box: card.getBoundingClientRect() }));
    const startTime = grid.ownerDocument.timeline.currentTime;
    for (const { card, box } of positions) {
      const old = previous.get(card);
      const x = old ? old.box.left - box.left : 0;
      const y = old ? old.box.top - box.top : 4;
      const opacity = old?.opacity ?? .78;
      if (Math.abs(x) < .5 && Math.abs(y) < .5 && opacity === 1) continue;
      const animation = card.animate([
        { transform: `translate(${x}px, ${y}px)`, opacity },
        { transform: 'none', opacity: 1 },
      ], { duration: old ? 360 : 300, easing: 'cubic-bezier(.3,.12,.24,1)' });
      if (startTime !== null) animation.startTime = startTime;
      running.set(card, animation);
      const clear = () => { if (running.get(card) === animation) running.delete(card); };
      void animation.finished.then(clear, clear);
    }
  };
}
