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
  wireCancelButton(control, { id: 42, job_type: 'pull_cycle' }, 'local-test-token', error => failures.push(error));
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
    wireCancelButton(control, { id: 7, job_type: 'pull_cycle' }, 'token', message => errors.push(message));
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
  wireCancelButton(control, { id: 1, job_type: 'pull_cycle' }, 'token', () => {});
  assert.equal(typeof control.onclick, 'function');
  wireCancelButton(control, { id: 2, job_type: 'clear' }, 'token', () => {});
  assert.equal(control.classes.has('hidden'), true);
  assert.equal(control.onclick, null);
});

test('progress updates preserve cancellation and cannot submit it twice', async t => {
  const control = button();
  let finish, calls = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    calls++;
    return new Promise(resolve => { finish = resolve; });
  });
  wireCancelButton(control, { id: 42, job_type: 'pull_cycle' }, 'token', () => {});
  const pending = control.onclick();
  wireCancelButton(control, { id: 42, job_type: 'pull_cycle' }, 'token', () => {});
  assert.equal(control.disabled, true);
  assert.equal(control.textContent, 'Cancelling…');
  await control.onclick();
  assert.equal(calls, 1);
  finish(new Response(null, { status: 204 }));
  await pending;
  wireCancelButton(control, { id: 42, job_type: 'pull_cycle' }, 'token', () => {});
  assert.equal(control.disabled, true);
  wireCancelButton(control, { id: 43, job_type: 'pull_cycle' }, 'token', () => {});
  assert.equal(control.disabled, false);
  assert.equal(control.textContent, 'Cancel');
});

test('a failed cancellation for an old job cannot change the new job controls', async t => {
  const control = button(), errors = [];
  let fail;
  t.mock.method(globalThis, 'fetch', () => new Promise((_, reject) => { fail = reject; }));
  wireCancelButton(control, { id: 1, job_type: 'pull_cycle' }, 'token', message => errors.push(message));
  const pending = control.onclick();
  wireCancelButton(control, { id: 2, job_type: 'pull_cycle' }, 'token', message => errors.push(message));
  fail(new Error('Old request failed'));
  await pending;
  assert.equal(control.disabled, false);
  assert.equal(control.textContent, 'Cancel');
  assert.deepEqual(errors, []);
});


for (const kind of ['pull', 'push']) {
  test(`${kind} jobs cannot be cancelled independently`, () => {
    const control = button();
    wireCancelButton(control, { id: 1, job_type: 'pull_cycle' }, 'token', () => {});
    wireCancelButton(control, { id: 2, job_type: kind }, 'token', () => {});
    assert.equal(control.classes.has('hidden'), true);
    assert.equal(control.onclick, null);
  });
}

test('an existing cancellation request survives a page reload', () => {
  const control = button();
  wireCancelButton(control, { id: 1, job_type: 'pull_cycle', stats: { cancel_requested: true } }, 'token', () => {});
  assert.equal(control.disabled, true);
  assert.equal(control.textContent, 'Cancelling…');
});
