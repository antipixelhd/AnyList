/** Retain every cached credit, but materialize cards only as the reader reaches
 * them. Expansion must not construct hundreds of offscreen portraits at once. */
export function createProgressiveCredits<T>(container: HTMLElement, signal: AbortSignal,
  render: (rows: T[]) => void, batchSize = 32) {
  let rows: T[] = [], shown = 0, expanded = false;
  const sentinel = document.createElement('div');
  sentinel.dataset.creditSentinel = '';
  sentinel.setAttribute('aria-hidden', 'true');
  sentinel.style.gridColumn = '1 / -1';
  sentinel.style.height = '1px';
  let scheduled: ReturnType<typeof setTimeout> | undefined;
  const observer = new IntersectionObserver(entries => {
    if (!expanded || signal.aborted || !entries.some(entry => entry.isIntersecting) || scheduled) return;
    scheduled = setTimeout(() => {scheduled = undefined;if (expanded && !signal.aborted) append();}, 0);
  }, {rootMargin: '200px'});
  const paint = () => {
    render(rows.slice(0, shown));
    sentinel.hidden = shown >= rows.length;
    container.append(sentinel);
    observer.unobserve(sentinel);
    if (expanded && !sentinel.hidden) observer.observe(sentinel);
  };
  const append = () => {if (shown < rows.length) {shown = Math.min(rows.length, shown + batchSize);paint();}};
  signal.addEventListener('abort', () => {clearTimeout(scheduled);observer.disconnect();}, {once: true});
  return {
    replace(next: T[]) {
      rows = next;
      shown = Math.min(shown, rows.length);
      if (expanded && !shown) shown = Math.min(rows.length, batchSize);
      if (!signal.aborted) paint();
    },
    toggle(open: boolean) {
      expanded = open;
      if (signal.aborted) return;
      if (open && !shown) append();
      else {observer.unobserve(sentinel);if (open && shown < rows.length) observer.observe(sentinel);}
    },
  };
}
