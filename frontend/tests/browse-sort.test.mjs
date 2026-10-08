import assert from 'node:assert/strict';
import test from 'node:test';
import {initializeBrowseSort} from '../src/lib/browse-sort.ts';

function sorting(initial = 'all', initialFilters) {
  const primary = new EventTarget(), results = new EventTarget();
  const options = () => ['all', 'score', 'title', 'popular'].map(value => ({value, textContent: value, get selected() {return this.owner.value === value;}}));
  for (const select of [primary, results]) {
    select.value = initial;
    select.options = options();
    select.options.forEach(option => option.owner = select);
  }
  const primaryControl = {hidden: initial !== 'all'}, resultsControl = {hidden: initial === 'all'};
  results.closest = () => resultsControl;
  const attributes = new Set();
  const nodes = {'[data-browse-sort]': primary, '[data-browse-results-sort]': results, '[data-browse-primary-sort]': primaryControl, '[data-browse-form]': initialFilters};
  const root = {querySelector: selector => nodes[selector], toggleAttribute: (name, on) => on ? attributes.add(name) : attributes.delete(name)};
  let changed = 0, entrances = 0;
  const signal = new AbortController();
  const NativeFormData = globalThis.FormData;
  // The fixture supplies form values without requiring a browser DOM.
  globalThis.FormData = class extends NativeFormData {
    constructor(values) { super(); for (const [name, value] of Object.entries(values)) this.set(name, value); }
  };
  let sync;
  try { sync = initializeBrowseSort(root, signal.signal, () => changed++, () => entrances++); }
  finally { globalThis.FormData = NativeFormData; }
  return {primary, results, primaryControl, resultsControl, attributes, signal, sync, changed: () => changed, entrances: () => entrances};
}

test('choosing a sort moves the control to results and keeps one canonical sort value', () => {
  const s = sorting();
  assert.equal(s.primaryControl.hidden, false);
  assert.equal(s.resultsControl.hidden, true);
  s.primary.value = 'score';
  s.sync();
  assert.equal(s.primaryControl.hidden, true);
  assert.equal(s.resultsControl.hidden, false);
  assert.equal(s.results.value, 'score');
  assert(s.attributes.has('data-browse-sorted'));
  assert.equal(s.entrances(), 1);
  s.results.value = 'title';
  s.results.dispatchEvent(new Event('change'));
  assert.equal(s.primary.value, 'title');
  assert.equal(s.changed(), 1);
  assert.equal(s.entrances(), 1);
  s.results.value = 'all';
  s.results.dispatchEvent(new Event('change'));
  assert.equal(s.primaryControl.hidden, false);
  assert.equal(s.resultsControl.hidden, true);
});

test('view-all hydration places sorting at results; relevance and navigation remain synchronized', () => {
  const s = sorting('score');
  assert.equal(s.primaryControl.hidden, true);
  assert.equal(s.resultsControl.hidden, false);
  assert.equal(s.entrances(), 0);
  s.sync(true);
  assert.equal(s.primary.disabled, true);
  assert.equal(s.results.disabled, true);
  assert.equal(s.results.options[1].textContent, 'Relevance');
  s.sync(false);
  assert.equal(s.results.disabled, false);
  assert.equal(s.results.options[1].textContent, 'score');
  s.signal.abort();
  s.results.value = 'title';
  s.results.dispatchEvent(new Event('change'));
  assert.equal(s.primary.value, 'score');
});

test('filters show the effective votes sort without making that fallback sticky', () => {
  const s = sorting();
  s.sync(false, true, true);
  assert.equal(s.primaryControl.hidden, true);
  assert.equal(s.resultsControl.hidden, false);
  assert.equal(s.results.value, 'popular');
  assert.equal(s.primary.value, 'all');
  s.sync(false, true, false);
  assert.equal(s.primaryControl.hidden, false);
  assert.equal(s.resultsControl.hidden, true);
  s.sync(false, true, true);
  s.results.value = 'score';
  s.results.dispatchEvent(new Event('change'));
  s.sync(false, true, false);
  assert.equal(s.results.value, 'score');
  assert.equal(s.resultsControl.hidden, false);
});

test('filtered reload hydrates directly into results without changing layout or replaying entrance', () => {
  for (const values of [{genres: '12'}, {start: '2020-01-01'}, {q: 'Arrival'}]) {
    const s = sorting('all', values);
    assert.equal(s.primaryControl.hidden, true);
    assert.equal(s.resultsControl.hidden, false);
    assert.equal(s.results.value, 'popular');
    assert.equal(s.entrances(), 0);
    assert.equal(s.results.disabled, !!values.q);
  }
});
