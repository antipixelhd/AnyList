import assert from "node:assert/strict";
import test from "node:test";
import { createProfileStatsLoader } from "../src/lib/profile-stats-request.ts";

function pendingFetch(t) {
  const requests = [];
  t.mock.method(
    globalThis,
    "fetch",
    (url, options) =>
      new Promise((resolve, reject) => {
        requests.push({ url, options, resolve, reject });
      }),
  );
  return requests;
}

function callbacks() {
  const rendered = [],
    errors = [],
    busy = [];
  return {
    rendered,
    errors,
    busy,
    render: (value) => rendered.push(value),
    onError: (message) => errors.push(message),
    onBusy: (value) => busy.push(value),
  };
}

test("latest filter wins when an older response arrives later despite cancellation", async (t) => {
  const requests = pendingFetch(t),
    state = callbacks();
  const loader = createProfileStatsLoader("viewer name", state);
  const first = loader.load("movie", "2025");
  const second = loader.load("series", "");
  assert.equal(requests[0].options.signal.aborted, true);
  assert.equal(requests[1].options.cache, "no-store");
  assert.equal(
    requests[0].url,
    "/api/proxy/tracking/profile/viewer%20name/stats/summary?media_type=movie&year=2025",
  );
  requests[1].resolve(Response.json({ current: { total: 20 } }));
  await second;
  requests[0].resolve(Response.json({ current: { total: 10 } }));
  await first;
  assert.deepEqual(state.rendered, [{ current: { total: 20 } }]);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.busy, [true, true, false]);
});

test("superseded failures cannot clear the new request busy state", async (t) => {
  const requests = pendingFetch(t),
    state = callbacks();
  const loader = createProfileStatsLoader("viewer", state);
  const first = loader.load("movie", ""),
    second = loader.load("all", "");
  requests[0].reject(new Error("Old request failed"));
  await first;
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.busy, [true, true]);
  requests[1].resolve(new Response(null, { status: 503 }));
  await second;
  assert.deepEqual(state.errors, ["Unable to load these statistics."]);
  assert.deepEqual(state.busy, [true, true, false]);
  const retry = loader.load("all", "");
  requests[2].resolve(Response.json({ current: { total: 5 } }));
  await retry;
  assert.deepEqual(state.rendered, [{ current: { total: 5 } }]);
});

test("navigation aborts the request and prevents late DOM callbacks or more requests", async (t) => {
  const requests = pendingFetch(t),
    state = callbacks();
  const loader = createProfileStatsLoader("viewer", state);
  const loading = loader.load("all", "");
  loader.stop();
  assert.equal(requests[0].options.signal.aborted, true);
  requests[0].resolve(Response.json({ current: { total: 6 } }));
  await loading;
  await loader.load("series", "2026");
  assert.equal(requests.length, 1);
  assert.deepEqual(state.rendered, []);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.busy, [true]);
});
