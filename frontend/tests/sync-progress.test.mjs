import assert from 'node:assert/strict';
import test from 'node:test';
import { createSyncProgressRenderer } from '../src/lib/sync-progress.ts';

function controls(t, widget) {
  const prefix = widget.endsWith('-import') ? widget : `${widget}-job`;
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) {
      const classes = new Set(['hidden']);
      elements.set(id, {
        style: {}, textContent: '', disabled: false, onclick: null, classes,
        classList: { add: c => classes.add(c), remove: c => classes.delete(c) },
      });
    }
    return elements.get(id);
  };
  globalThis.document = {
    getElementById: get,
    querySelector: selector => get(selector.slice(1)),
  };
  t.after(() => delete globalThis.document);
  return {
    progress: get(`${widget}-progress`), dropzone: get(`${widget}-dropzone`),
    ...Object.fromEntries(['bar', 'pct', 'label', 'items', 'status', 'cancel'].map(key => [key, get(`${prefix}-${key}`)])),
  };
}

const job = (overrides = {}) => ({
  id: 42, job_type: 'pull', status: 'running', processed_items: 4, total_items: 10,
  current_step: null, error_message: null, ...overrides,
});

test('queued and running jobs distinguish unknown progress, real progress, and current steps', t => {
  const ui = controls(t, 'simkl');
  const render = createSyncProgressRenderer('simkl', 'token', () => {});
  render(job({ status: 'pending', job_type: 'push' }));
  assert.equal(ui.label.textContent, 'Push queued…');
  assert.equal(ui.bar.style.width, '100%');
  assert.match(ui.bar.className, /bg-amber-500 animate-pulse$/);
  render(job({ current_step: 'Fetching episodes' }));
  assert.equal(ui.label.textContent, 'Fetching episodes');
  assert.equal(ui.pct.textContent, '40%');
  assert.equal(ui.items.textContent, '4 / 10 items');
  assert.equal(ui.bar.style.width, '40%');
  render(job({ total_items: 0 }));
  assert.equal(ui.pct.textContent, '');
  assert.equal(ui.items.textContent, 'Loading…');
  render(job({ processed_items: 20 }));
  assert.equal(ui.pct.textContent, '100%');
});

test('import errors and completion keep dropzones hidden until the monitor expires', t => {
  const ui = controls(t, 'scrob-import');
  const render = createSyncProgressRenderer('scrob-import', 'token', () => {});
  render(job({ status: 'failed', error_message: 'Invalid export' }));
  assert.equal(ui.items.textContent, '');
  assert.equal(ui.status.textContent, 'Invalid export');
  assert.equal(ui.dropzone.classes.has('hidden'), true);
  render(job({ status: 'completed' }));
  assert.equal(ui.status.textContent, '');
  assert.equal(ui.items.textContent, '4 / 10 items');
  assert.match(ui.bar.className, /bg-blue-500$/);
  render(null);
  assert.equal(ui.progress.classes.has('hidden'), true);
  assert.equal(ui.dropzone.classes.has('hidden'), false);
});

test('provider summaries preserve their distinct counts and labels', t => {
  for (const [widget, summary, label] of [
    ['simkl', '2 movies, 3 episodes, 4 ratings', 'Pull complete'],
    ['trakt', '5 ok, 1 failed', 'Pull complete'],
    ['mdblist', '4 items', 'Pull complete'],
    ['bingebase', '4 items pushed', 'Push complete'],
  ]) {
    const ui = controls(t, widget);
    createSyncProgressRenderer(widget, 'token', () => {})(job({
      status: 'completed', stats: { movies: 2, episodes: 3, ratings: 4, succeeded: 5, failed: 1 },
    }));
    assert.equal(ui.items.textContent, summary);
    assert.equal(ui.label.textContent, label);
    assert.equal(ui.cancel.classes.has('hidden'), true);
  }
});

test('provider cancellation stays requested across progress updates and hides at termination', async t => {
  const ui = controls(t, 'trakt');
  let requests = 0;
  t.mock.method(globalThis, 'fetch', async () => { requests++; return new Response(null, { status: 204 }); });
  const render = createSyncProgressRenderer('trakt', 'token', () => {});
  render(job());
  await ui.cancel.onclick();
  render(job({ processed_items: 6 }));
  assert.equal(ui.cancel.disabled, true);
  assert.equal(ui.cancel.textContent, 'Cancelling…');
  await ui.cancel.onclick();
  assert.equal(requests, 1);
  render(job({ status: 'cancelled' }));
  assert.equal(ui.label.textContent, 'Pull cancelled');
  assert.equal(ui.items.textContent, '');
  assert.equal(ui.cancel.classes.has('hidden'), true);
});
