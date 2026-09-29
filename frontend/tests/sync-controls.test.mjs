import assert from 'node:assert/strict';
import test from 'node:test';
import { wireCancelButton } from '../src/lib/sync-controls.ts';

function button() {
  const classes = new Set(['hidden']);
  return { disabled: false, textContent: '', onclick: null,
    classList: { add: name => classes.add(name), remove: name => classes.delete(name) },
    classes };
}

test('cancel submits the job with its credential and waits for cooperative completion', async t => {
  const control = button();
  const failures = [];
  let request;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    request = { url, options };
    return new Response(null, { status: 204 });
  });
  wireCancelButton(control, { id: 42, job_type: 'pull' }, 'local-test-token', error => failures.push(error));
  assert.equal(control.classes.has('hidden'), false);
  await control.onclick();
  assert.equal(request.url, '/api/proxy/sync/42/cancel');
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.headers.Authorization, 'Bearer local-test-token');
  assert.equal(control.disabled, true);
  assert.equal(control.textContent, 'Cancelling…');
  assert.deepEqual(failures, []);
});

for (const failure of ['http', 'network']) {
  test(`${failure} failure restores cancellation for retry`, async t => {
    const control = button();
    const errors = [];
    let attempts = 0;
    t.mock.method(globalThis, 'fetch', async () => {
      attempts++;
      if (attempts > 1) return new Response(null, { status: 204 });
      if (failure === 'network') throw new Error('offline');
      return new Response(null, { status: 503 });
    });
    wireCancelButton(control, { id: 7 }, 'token', message => errors.push(message));
    await control.onclick();
    assert.equal(control.disabled, false);
    assert.equal(control.textContent, 'Cancel');
    assert.deepEqual(errors, [failure === 'network' ? 'offline' : 'Unable to cancel job (HTTP 503)']);
    await control.onclick();
    assert.equal(attempts, 2);
    assert.equal(control.disabled, true);
  });
}

test('clear jobs cannot retain a cancellation action from a previous job', () => {
  const control = button();
  wireCancelButton(control, { id: 1 }, 'token', () => {});
  assert.equal(typeof control.onclick, 'function');
  wireCancelButton(control, { id: 2, job_type: 'clear' }, 'token', () => {});
  assert.equal(control.classes.has('hidden'), true);
  assert.equal(control.onclick, null);
});
