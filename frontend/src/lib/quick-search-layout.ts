/** Place short result panels beneath one another when they fit within a full panel's height. */
export function quickSearchPanelPositions(heights: number[], columns: number, gap: number, minimumColumnHeight = 540) {
  const ceiling = Math.max(minimumColumnHeight, ...heights);
  const ends = Array<number>(columns).fill(0);
  return heights.map((height, index) => {
    let column = index < columns ? index : ends.indexOf(Math.min(...ends));
    let top = ends[column];
    if (top && top + height > ceiling) {
      top = Math.max(...ends);
      ends.fill(top);
      column = 0;
    }
    ends[column] = top + height + gap;
    return {column: column + 1, top, height};
  });
}

/** Keep layout measurements separate from result fetching and respond to panel resizes. */
export function mountQuickSearchLayout(shell: HTMLElement, signal: AbortSignal) {
  const desktop = matchMedia('(min-width:651px)');
  const narrow = matchMedia('(max-width:1400px)');
  const cards = [...shell.querySelectorAll<HTMLElement>(':scope > .quick-search-card')];
  let frame = 0;
  const clear = () => {
    shell.removeAttribute('data-masonry');
    for (const card of cards) { card.style.removeProperty('grid-column'); card.style.removeProperty('grid-row'); }
  };
  const layout = () => {
    frame = 0;
    if (signal.aborted || shell.hidden || !desktop.matches) { clear(); return; }
    const visible = cards.filter(card => !card.hidden);
    const columns = narrow.matches ? 2 : 3;
    if (visible.length <= columns) { clear(); return; }
    const heights = visible.map(card => card.offsetHeight);
    if (heights.some(height => !height)) { clear(); return; }
    const positions = quickSearchPanelPositions(heights, columns, 32);
    for (let i = 0; i < visible.length; i++) {
      const {column, top, height} = positions[i];
      visible[i].style.gridColumn = String(column);
      visible[i].style.gridRow = `${top + 1} / span ${height}`;
    }
    shell.setAttribute('data-masonry', '');
  };
  const schedule = () => { if (!frame) frame = requestAnimationFrame(layout); };
  const observer = new ResizeObserver(schedule);
  cards.forEach(card => observer.observe(card));
  desktop.addEventListener('change', schedule, {signal});
  narrow.addEventListener('change', schedule, {signal});
  signal.addEventListener('abort', () => { observer.disconnect(); cancelAnimationFrame(frame); clear(); }, {once:true});
  return schedule;
}
