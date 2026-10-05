import assert from "node:assert/strict";
import test from "node:test";
import { createSyncJobMonitor } from "../src/lib/sync-job-monitor.ts";

function environment(t) {
  const document = new EventTarget();
  document.hidden = false;
  const timers = new Map();
  let id = 0,
    now = Date.parse("2026-09-30T12:00:00Z");
  const originals = new Map();
  for (const [name, value] of Object.entries({
    document,
    setTimeout: (callback, delay) => {
      timers.set(++id, { callback, delay });
      return id;
    },
    clearTimeout: (timer) => timers.delete(timer),
  })) {
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
    Object.defineProperty(globalThis, name, {
      value,
      configurable: true,
      writable: true,
    });
  }
  t.mock.method(Date, "now", () => now);
  t.after(() => {
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  });
  return {
    document,
    timers,
    advance: (milliseconds) => {
      now += milliseconds;
    },
    async tick() {
      const [key, timer] = timers.entries().next().value;
      timers.delete(key);
      now += timer.delay;
      await timer.callback();
    },
  };
}

const job = (
  id,
  source,
  status = "running",
  updated_at = "2026-09-30T12:00:00",
) => ({
  id,
  source,
  status,
  updated_at,
  job_type: "pull",
  connection_id: null,
  current_step: null,
  total_items: 10,
  processed_items: 5,
  error_message: null,
});

test("widgets share one authenticated request and active jobs take precedence over newer terminal records", async (t) => {
  environment(t);
  const calls = [],
    first = [],
    second = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, options });
    return Response.json([
      job(3, "trakt", "completed"),
      job(1, "trakt"),
      job(2, "simkl"),
    ]);
  });
  const monitor = createSyncJobMonitor("test-token");
  await Promise.all([
    monitor.watch("trakt", {
      matches: (j) => j.source === "trakt",
      render: (j) => first.push(j?.id),
    }),
    monitor.watch("simkl", {
      matches: (j) => j.source === "simkl",
      render: (j) => second.push(j?.id),
    }),
  ]);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/proxy/sync/status");
  assert.equal(calls[0].options.headers.Authorization, "Bearer test-token");
  assert.equal(calls[0].options.cache, "no-store");
  assert.deepEqual(first, [1]);
  assert.deepEqual(second, [2]);
  monitor.stop();
});

test("HTTP and network outages preserve running-job locks and retry until completion", async (t) => {
  const env = environment(t),
    rendered = [],
    active = [],
    errors = [];
  const responses = [
    Response.json([job(1, "trakt")]),
    new Response(null, { status: 503 }),
    new Error("offline"),
    Response.json([job(1, "trakt", "completed")]),
  ];
  t.mock.method(globalThis, "fetch", async () => {
    const response = responses.shift();
    if (response instanceof Error) throw response;
    return response;
  });
  const monitor = createSyncJobMonitor("token");
  await monitor.watch("trakt", {
    matches: (j) => j.source === "trakt",
    render: (j) => rendered.push(j?.status ?? null),
    onUpdate: (value) => active.push(value),
    onError: () => errors.push("retry"),
  });
  await env.tick();
  assert.deepEqual(active, [true]);
  assert.deepEqual(rendered, ["running"]);
  assert.equal([...env.timers.values()][0].delay, 5000);
  await env.tick();
  await env.tick();
  assert.deepEqual(errors, ["retry", "retry"]);
  assert.deepEqual(active, [true, false]);
  assert.deepEqual(rendered, ["running", "completed"]);
  await env.tick();
  assert.deepEqual(rendered, ["running", "completed", null]);
  assert.equal(env.timers.size, 0);
  monitor.stop();
});

test("known completion remains visible after a background delay and other widgets cannot extend it", async (t) => {
  const env = environment(t),
    rendered = [];
  const ids = [];
  let jobs = [job(1, "mdblist"), job(2, "simkl")];
  t.mock.method(globalThis, "fetch", async () => Response.json(jobs));
  const monitor = createSyncJobMonitor("token");
  await monitor.watch("mdblist", {
    matches: (j) => j.source === "mdblist",
    render: (j) => {
      rendered.push(j?.status ?? null);
      ids.push(j?.id);
    },
    hideMs: 8000,
    onDone: () => rendered.push("done"),
  });
  env.advance(60_000);
  jobs = [
    job(3, "mdblist", "completed", "2026-09-29T00:00:00"),
    job(1, "mdblist", "completed"),
    job(2, "simkl"),
  ];
  await env.tick();
  assert.deepEqual(rendered, ["running", "completed", "done"]);
  await monitor.watch("simkl", {
    matches: (j) => j.source === "simkl",
    render() {},
  });
  for (let i = 0; i < 4; i++) await env.tick();
  assert.deepEqual(rendered, ["running", "completed", "done", null]);
  assert.deepEqual(ids, [1, 1, undefined]);
  monitor.stop();
});

test("fresh terminal timestamps accept UTC and explicit offsets while stale unrelated jobs stay hidden", async (t) => {
  const env = environment(t),
    rendered = [];
  t.mock.method(globalThis, "fetch", async () =>
    Response.json([
      job(1, "trakt", "completed"),
      job(2, "simkl", "completed", "2026-09-30T10:00:00-02:00"),
      job(3, "mdblist", "completed", "2026-09-29T00:00:00"),
    ]),
  );
  const monitor = createSyncJobMonitor("token");
  await Promise.all(
    ["trakt", "simkl", "mdblist"].map((source) =>
      monitor.watch(source, {
        matches: (j) => j.source === source,
        render: (j) => rendered.push(j?.id ?? null),
      }),
    ),
  );
  assert.deepEqual(rendered, [1, 2, null]);
  await env.tick();
  assert.deepEqual(rendered, [1, 2, null, null, null]);
  assert.equal(env.timers.size, 0);
  monitor.stop();
});

test("queued and tracked jobs missing from a snapshot retain their watch until they appear", async (t) => {
  const env = environment(t),
    rendered = [];
  let jobs = [];
  t.mock.method(globalThis, "fetch", async () => Response.json(jobs));
  const monitor = createSyncJobMonitor("token");
  await monitor.watch("import", {
    matches: () => true,
    jobId: 4,
    render: (j) => rendered.push(j?.status ?? null),
  });
  assert.deepEqual(rendered, []);
  jobs = [job(4, "manual")];
  await env.tick();
  jobs = [];
  await env.tick();
  assert.deepEqual(rendered, ["running"]);
  assert.equal(env.timers.size, 1);
  monitor.stop();
});

test("navigation aborts in-flight requests, timers and visibility wake listeners", async (t) => {
  const env = environment(t),
    rendered = [];
  let finish,
    signal,
    calls = 0;
  t.mock.method(globalThis, "fetch", async (_, options) => {
    calls++;
    signal = options.signal;
    return new Promise((resolve) => {
      finish = resolve;
    });
  });
  const monitor = createSyncJobMonitor("token");
  const pending = monitor.watch("trakt", {
    matches: () => true,
    render: (j) => rendered.push(j),
  });
  await Promise.resolve();
  env.document.dispatchEvent(new Event("astro:before-swap"));
  assert.equal(signal.aborted, true);
  finish(Response.json([job(1, "trakt")]));
  await pending;
  env.document.dispatchEvent(new Event("visibilitychange"));
  await monitor.watch("new", { matches: () => true, render() {} });
  assert.equal(calls, 1);
  assert.deepEqual(rendered, []);
  assert.equal(env.timers.size, 0);
});


test('persistent account watch notices a later scheduled cycle while the page stays open', async t => {
  const env = environment(t), rendered = [];
  const responses = [[], [job(1, 'manual')], [job(1, 'manual', 'completed')], [], [job(2, 'manual')]];
  t.mock.method(globalThis, 'fetch', async () => Response.json(responses.shift() || []));
  const monitor = createSyncJobMonitor('token');
  await monitor.watch('account', { matches: () => true, persistent: true, render: value => rendered.push(value?.id) });
  await env.tick();
  await env.tick();
  await env.tick();
  await env.tick();
  assert.equal(rendered.at(-1), 2);
  monitor.stop();
});
