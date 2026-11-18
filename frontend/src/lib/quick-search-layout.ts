/** Keep the first row in reading order, then use the earliest free space. */
export function quickSearchPanelPositions(heights: number[], columns: number, gap: number) {
  const ends = Array<number>(columns).fill(0);
  return heights.map((height, index) => {
    const column = index < columns ? index : ends.indexOf(Math.min(...ends));
    const top = ends[column];
    ends[column] = top + height + gap;
    return {column: column + 1, top, height};
  });
}

/** Keep layout measurements separate from result fetching and respond to panel resizes. */
export function mountQuickSearchLayout(shell: HTMLElement, phone: MediaQueryList, signal: AbortSignal) {
  const narrow = matchMedia('(max-width:1400px)');
  const reducedMotion = matchMedia('(prefers-reduced-motion:reduce)');
  const cards = [...shell.querySelectorAll<HTMLElement>(':scope > .quick-search-card')];
  const moves = new Map<HTMLElement, Animation>();
  let frame = 0;
  const clear = () => {
    shell.removeAttribute('data-masonry');
    for (const card of cards) { card.style.removeProperty('grid-column'); card.style.removeProperty('grid-row'); }
  };
  const layout = () => {
    if (signal.aborted || shell.hidden || phone.matches) { clear(); return; }
    const visible = cards.filter(card => !card.hidden);
    const columns = narrow.matches ? 2 : 3;
    if (visible.length <= columns) { clear(); return; }
    const heights = visible.map(card => card.offsetHeight);
    if (heights.some(height => !height)) { clear(); return; }
    const positions = quickSearchPanelPositions(heights, columns, parseFloat(getComputedStyle(shell).columnGap));
    for (let i = 0; i < visible.length; i++) {
      const {column, top, height} = positions[i];
      visible[i].style.gridColumn = String(column);
      visible[i].style.gridRow = `${top + 1} / span ${height}`;
    }
    shell.setAttribute('data-masonry', '');
  };
  const schedule = () => {
    if (!frame) frame = requestAnimationFrame(() => { frame = 0; layout(); });
  };
  const observer = new ResizeObserver(schedule);
  cards.forEach(card => observer.observe(card));
  phone.addEventListener('change', schedule, {signal});
  narrow.addEventListener('change', schedule, {signal});
  signal.addEventListener('abort', () => {
    observer.disconnect();
    cancelAnimationFrame(frame);
    moves.forEach(move => move.cancel());
    clear();
  }, {once:true});

  return (change: () => void) => {
    // Measure the running transition too, so another response can move a panel from where it is now.
    const before = new Map<HTMLElement, DOMRect>();
    if (!phone.matches && !reducedMotion.matches && !shell.hidden) {
      for (const card of cards) if (!card.hidden) before.set(card, card.getBoundingClientRect());
    }
    moves.forEach(move => move.cancel());
    moves.clear();
    change();
    layout();
    for (const [card, previous] of before) {
      if (shell.hidden || card.hidden) continue;
      const next = card.getBoundingClientRect();
      const x = previous.left - next.left, y = previous.top - next.top;
      if (Math.abs(x) < 1 && Math.abs(y) < 1) continue;
      const move = card.animate({translate: [`${x}px ${y}px`, '0px 0px']}, {duration: 230, easing: 'cubic-bezier(.2,.7,.2,1)'});
      moves.set(card, move);
      move.onfinish = () => { if (moves.get(card) === move) moves.delete(card); };
    }
  };
}
