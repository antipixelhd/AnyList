import assert from "node:assert/strict";
import test from "node:test";
import { createDeviceAuthorization } from "../src/lib/device-authorization.ts";

const drain = () => new Promise(resolve => setImmediate(resolve));
const code = { user_code: "1234", interval: 1, expires_in: 60, verification_url: "https://provider.test" };

function environment(t, fetch) {
  let now = 0, id = 0;
  const timers = new Map();
  t.mock.method(Date, "now", () => now);
  t.mock.method(globalThis, "setTimeout", (callback, delay) => {
    timers.set(++id, { callback, delay });
    return id;
  });
  t.mock.method(globalThis, "clearTimeout", timer => timers.delete(timer));
  t.mock.method(globalThis, "fetch", fetch);
  return {
    timers,
    async tick() {
      const [id, timer] = timers.entries().next().value;
      timers.delete(id);
      now += timer.delay;
      timer.callback();
      await drain();
    },
  };
}

function session() {
  const codes = [], errors = [];
  let connected = 0;
  const auth = createDeviceAuthorization({
    provider: "Trakt", token: "fixture-token", startUrl: "/start", pollUrl: "/poll",
    onCode: value => codes.push(value),
    onConnected: () => connected++,
    onError: message => errors.push(message),
  });
  return { auth, codes, errors, connected: () => connected };
}

test("credentials precede authorization and pending responses poll until connected", async t => {
  const requests = [];
  const responses = [Response.json({}), Response.json(code), Response.json({ status: "pending" }), Response.json({ status: "connected" })];
  const env = environment(t, async (url, options) => {
    requests.push({ url, options });
    return responses.shift();
  });
  const state = session();
  const attempt = state.auth.start({ trakt_client_id: "id", trakt_client_secret: "secret" });
  await drain();
  assert.deepEqual(requests.map(request => request.url), ["/api/proxy/auth/settings", "/start"]);
  assert.equal(requests[0].options.method, "PATCH");
  assert.deepEqual(JSON.parse(requests[0].options.body), { trakt_client_id: "id", trakt_client_secret: "secret" });
  assert.equal(requests[0].options.headers.Authorization, "Bearer fixture-token");
  assert.deepEqual(state.codes, [code]);
  await env.tick();
  assert.equal(state.connected(), 0);
  await env.tick();
  await attempt;
  assert.equal(state.connected(), 1);
  assert.deepEqual(requests.map(request => request.url), ["/api/proxy/auth/settings", "/start", "/poll", "/poll"]);
  assert.equal(env.timers.size, 0);
  assert.deepEqual(state.errors, []);
});

test("navigation aborts an in-flight poll and suppresses a late success", async t => {
  let resolvePoll, pollSignal;
  const env = environment(t, (url, options) => url === "/start"
    ? Promise.resolve(Response.json(code))
    : new Promise(resolve => { resolvePoll = resolve; pollSignal = options.signal; }));
  const state = session();
  const attempt = state.auth.start();
  await drain();
  await env.tick();
  state.auth.stop();
  assert.equal(pollSignal.aborted, true);
  resolvePoll(Response.json({ status: "connected" }));
  await attempt;
  await state.auth.start();
  assert.equal(state.connected(), 0);
  assert.deepEqual(state.errors, []);
  assert.equal(env.timers.size, 0);
});

test("navigation during credential saving cannot start a provider authorization", async t => {
  let complete, signal;
  const requests = [];
  const env = environment(t, (url, options) => new Promise(resolve => {
    requests.push(url); complete = resolve; signal = options.signal;
  }));
  const state = session();
  const attempt = state.auth.start({ client: "fixture" });
  state.auth.stop();
  assert.equal(signal.aborted, true);
  complete(Response.json({}));
  await attempt;
  assert.deepEqual(requests, ["/api/proxy/auth/settings"]);
  assert.deepEqual(state.codes, []);
  assert.deepEqual(state.errors, []);
  assert.equal(env.timers.size, 0);
});

test("replacement attempts ignore the older start response and stop their timer", async t => {
  const starts = [];
  const env = environment(t, (_, options) => new Promise(resolve => starts.push({ resolve, signal: options.signal })));
  const state = session();
  const first = state.auth.start(), second = state.auth.start();
  assert.equal(starts[0].signal.aborted, true);
  starts[1].resolve(Response.json({ ...code, user_code: "new" }));
  await drain();
  starts[0].resolve(Response.json({ ...code, user_code: "old" }));
  await first;
  assert.deepEqual(state.codes.map(value => value.user_code), ["new"]);
  state.auth.stop();
  await second;
  assert.equal(env.timers.size, 0);
  assert.deepEqual(state.errors, []);
});

test("transient network failure retries but a rejected HTTP response ends polling", async t => {
  let polls = 0;
  const env = environment(t, async url => {
    if (url === "/start") return Response.json(code);
    if (++polls === 1) throw new TypeError("Network unavailable");
    return Response.json({ detail: "Authorization denied" }, { status: 403 });
  });
  const state = session();
  const attempt = state.auth.start();
  await drain();
  await env.tick();
  assert.deepEqual(state.errors, []);
  await env.tick();
  await attempt;
  assert.deepEqual(state.errors, ["Authorization denied"]);
  assert.equal(polls, 2);
  assert.equal(env.timers.size, 0);
});

test("expiry ends authorization before another provider request", async t => {
  let requests = 0;
  const env = environment(t, async () => { requests++; return Response.json({ ...code, interval: 5, expires_in: 2 }); });
  const state = session();
  const attempt = state.auth.start();
  await drain();
  await env.tick();
  await attempt;
  assert.equal(requests, 1);
  assert.deepEqual(state.errors, ["Authorization timed out. Please try again."]);
  assert.equal(env.timers.size, 0);
});

test("a non-JSON provider failure reports the HTTP error and never displays a PIN", async t => {
  environment(t, async () => new Response("upstream unavailable", { status: 502 }));
  const state = session();
  await state.auth.start();
  assert.deepEqual(state.codes, []);
  assert.equal(state.errors.length, 1);
  assert.match(state.errors[0], /authorization failed.*HTTP 502/);
});
