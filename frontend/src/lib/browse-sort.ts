import { reveal } from './ui-motion.ts';
import { hasBrowseFilters } from './browse-view.ts';

/** One sort value, with a discovery control and a compact results control. */
export function initializeBrowseSort(root: HTMLElement, signal: AbortSignal, changed: () => void, animate = reveal) {
  const primary = root.querySelector<HTMLSelectElement>('[data-browse-sort]')!;
  const results = root.querySelector<HTMLSelectElement>('[data-browse-results-sort]')!;
  const primaryControl = root.querySelector<HTMLElement>('[data-browse-primary-sort]')!;
  const resultsControl = results.closest<HTMLElement>('.browse-results-sort')!;
  const names = new Map([...primary.options].map(option => [option.value, option.textContent]));
  const focusControl = (control: HTMLElement) => queueMicrotask(() => {
    if (signal.aborted || !root.isConnected) return;
    const target = control.querySelector<HTMLElement>('.tracker-select-trigger, select');
    if (target?.getClientRects().length) target.focus();
    else root.querySelector<HTMLElement>('.browse-more > summary')?.focus();
  });
  let currentSearching = false, currentFiltered = false;
  const sync = (searching = currentSearching, motion = true, filtered = currentFiltered) => {
    currentSearching = searching;
    currentFiltered = filtered;
    const sorted = primary.value !== 'all' || filtered || searching;
    const entering = resultsControl.hidden && sorted;
    root.toggleAttribute('data-browse-sorted', sorted);
    primaryControl.hidden = sorted;
    resultsControl.hidden = !sorted;
    results.value = sorted && primary.value === 'all' ? 'popular' : primary.value;
    for (const select of [primary, results]) {
      select.disabled = searching;
      for (const option of select.options) {
        option.textContent = searching && option.selected ? 'Relevance' : names.get(option.value) || option.value;
      }
      select.dispatchEvent(new Event('input', {bubbles: true}));
    }
    if (entering && motion) animate(resultsControl);
  };
  results.addEventListener('change', () => {
    primary.value = results.value;
    sync();
    changed();
    if (primary.value === 'all') focusControl(primaryControl);
  }, {signal});
  primary.addEventListener('change', () => {
    if (primary.value !== 'all') focusControl(resultsControl);
  }, {signal});
  // Hydrate the server-rendered view directly, without briefly showing discovery.
  const form = root.querySelector<HTMLFormElement>('[data-browse-form]');
  const initialValues = new URLSearchParams();
  if (form) for (const [name, value] of new FormData(form)) {
    if (typeof value === 'string') initialValues.set(name, value);
  }
  sync(!!initialValues.get('q')?.trim(), false, hasBrowseFilters(initialValues));
  return sync;
}
