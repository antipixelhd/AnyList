import assert from "node:assert/strict";
import test from "node:test";
import { createImportUploader, importFormats } from "../src/lib/data-imports.ts";

function session(format = importFormats[0]) {
  const started = [], errors = [];
  const uploader = createImportUploader(format, "fixture-token", {
    onStarted: data => started.push(data), onError: message => errors.push(message),
  });
  return { uploader, started, errors };
}

for (const format of importFormats) {
  test(`${format.title} uploads only supported fields with the correct endpoint and prefix`, async t => {
    const requests = [];
    t.mock.method(globalThis, "fetch", async (url, options) => {
      requests.push({ url, options });
      return Response.json({ job_id: 123, message: "Started" });
    });
    const { uploader, started, errors } = session(format);
    const selection = Object.fromEntries(format.options.map(option => [option.key, !option.secret]));
    selection.unknown = true;
    await uploader.upload(new File(["export"], `export${format.extension.toUpperCase()}`), selection);
    assert.equal(requests.length, 1);
    const { url, options } = requests[0];
    assert.equal(url, format.endpoint);
    assert.equal(options.method, "POST");
    assert.deepEqual(options.headers, { Authorization: "Bearer fixture-token" });
    assert.equal(options.body.get("file").name, `export${format.extension.toUpperCase()}`);
    assert.deepEqual([...options.body.keys()], ["file", ...format.options.map(option => format.fieldPrefix + option.key)]);
    for (const option of format.options) assert.equal(options.body.get(format.fieldPrefix + option.key), String(!option.secret));
    assert.deepEqual(started, [{ job_id: 123, message: "Started" }]);
    assert.deepEqual(errors, []);
  });
}

test("secret restoration requires an explicit selection and omitted options are false", async t => {
  let body;
  t.mock.method(globalThis, "fetch", async (_, options) => {
    body = options.body;
    return Response.json({});
  });
  await session().uploader.upload(new File(["export"], "backup.zip"), { api_keys: true });
  assert.equal(body.get("api_keys"), "true");
  assert.equal(body.get("connections"), "false");
  assert.equal(body.get("watched"), "false");
});

test("invalid files and unsupported or empty selections never send a request", async t => {
  const fetch = t.mock.method(globalThis, "fetch", async () => assert.fail("unexpected request"));
  const { uploader, errors } = session();
  await uploader.upload(new File([], "backup.csv"), { watched: true });
  await uploader.upload(new File([], "backup.zip"), { unknown: true });
  await uploader.upload(new File([], "backup.zip"), {});
  assert.equal(fetch.mock.callCount(), 0);
  assert.deepEqual(errors, ["Please select a .zip export file.", "Select at least one item to import.", "Select at least one item to import."]);
});

test("pending uploads reject duplicate submissions and failed uploads can be retried", async t => {
  let finish;
  const fetch = t.mock.method(globalThis, "fetch", () => new Promise(resolve => { finish = resolve; }));
  const { uploader, started, errors } = session();
  const file = new File([], "backup.zip");
  const first = uploader.upload(file, { watched: true });
  await uploader.upload(file, { watched: true });
  assert.equal(fetch.mock.callCount(), 1);
  finish(Response.json({ detail: "Import unavailable" }, { status: 503 }));
  await first;
  assert.deepEqual(errors, ["Import unavailable"]);
  assert.deepEqual(started, []);
  const retry = uploader.upload(file, { watched: true });
  assert.equal(fetch.mock.callCount(), 2);
  finish(Response.json({ job_id: 456 }));
  await retry;
  assert.deepEqual(started, [{ job_id: 456 }]);
});

test("navigation aborts uploads and suppresses late success, failure, and further submissions", async t => {
  for (const response of [Response.json({ job_id: 123 }), Response.json({ detail: "Rejected" }, { status: 403 })]) {
    let finish, signal;
    const fetch = t.mock.method(globalThis, "fetch", (_, options) => {
      signal = options.signal;
      return new Promise(resolve => { finish = resolve; });
    });
    const { uploader, started, errors } = session();
    const file = new File([], "backup.zip");
    const pending = uploader.upload(file, { watched: true });
    uploader.stop();
    assert.equal(signal.aborted, true);
    finish(response);
    await pending;
    await uploader.upload(file, { watched: true });
    assert.equal(fetch.mock.callCount(), 1);
    assert.deepEqual(started, []);
    assert.deepEqual(errors, []);
    fetch.mock.restore();
  }
});
