import assert from 'node:assert/strict';
import test from 'node:test';
import { GET, HEAD, POST } from '../src/pages/api/proxy/[...path].ts';

function context(method = 'GET', headers = {}, query = '') {
  return { params: { path: 'media/example' }, request: new Request(`http://localhost/api/proxy/media/example${query}`, { method, headers }) };
}

test('malformed credentials return 400 before contacting the backend', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', () => { throw new Error('unexpected fetch'); });
  const response = await GET(context('GET', { Cookie: 'token=%ZZ' }));
  assert.equal(response.status, 400);
  assert.equal((await GET(context('GET', {}, '?token=invalid%0Aheader'))).status, 400);
  assert.equal(fetch.mock.callCount(), 0);
});

test('proxy forwards explicit credentials and download headers', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(new URL(url).pathname, '/media/example');
    assert.equal(options.headers.get('Authorization'), 'Bearer explicit');
    assert.equal(options.headers.get('Range'), 'bytes=0-2');
    assert.equal(options.redirect, 'manual');
    return new Response('abc', { status: 206, headers: { 'Content-Range': 'bytes 0-2/3', 'Content-Type': 'video/x-matroska' } });
  });
  const response = await GET(context('GET', { Authorization: 'Bearer explicit', Cookie: 'token=cookie', Range: 'bytes=0-2' }, '?token=query'));
  assert.equal(response.status, 206);
  assert.equal(response.headers.get('Content-Range'), 'bytes 0-2/3');
  assert.equal(response.headers.get('Content-Type'), 'video/webm');
  assert.equal(await response.text(), 'abc');
});

test('redirects must use HTTPS artwork hosts without embedded credentials', async (t) => {
  t.mock.method(console, 'error', () => {});
  for (const [location, status] of [
    ['https://image.tmdb.org/t/p/w500/a.jpg', 302],
    ['https://artworks.thetvdb.com/banners/a.jpg', 302],
    ['http://image.tmdb.org/a.jpg', 502],
    ['https://user:secret@image.tmdb.org/a.jpg', 502],
    ['https://evil.test/a.jpg', 502],
    ['/relative', 502],
  ]) {
    t.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 302, headers: { Location: location } }));
    const response = await GET(context());
    assert.equal(response.status, status, location);
    assert.equal(response.headers.get('Location'), status === 302 ? location : null);
  }
});

test('network failures return 502 without logging query credentials or error messages', async (t) => {
  const logs = [];
  t.mock.method(console, 'error', (...args) => logs.push(args));
  t.mock.method(globalThis, 'fetch', async () => { throw new Error('failed URL includes secret'); });
  const response = await GET(context('GET', {}, '?token=secret&api_key=secret'));
  assert.equal(response.status, 502);
  assert.equal(logs.length, 1);
  assert.equal(JSON.stringify(logs).includes('secret'), false);
});

test('HEAD forwards the method and headers with no response body', async (t) => {
  t.mock.method(globalThis, 'fetch', async (_, options) => {
    assert.equal(options.method, 'HEAD');
    return new Response('ignored', { headers: { 'Content-Length': '7' } });
  });
  const response = await HEAD(context('HEAD'));
  assert.equal(response.body, null);
  assert.equal(response.headers.get('Content-Length'), '7');
});

test('multipart bodies and their boundary reach the backend intact', async (t) => {
  const body = '--boundary\r\nexample\r\n--boundary--';
  t.mock.method(globalThis, 'fetch', async (_, options) => {
    assert.equal(options.headers.get('Content-Type'), 'multipart/form-data; boundary=boundary');
    assert.equal(new TextDecoder().decode(options.body), body);
    return new Response(null, { status: 204 });
  });
  const ctx = context('POST');
  ctx.request = new Request(ctx.request.url, { method: 'POST', body, headers: { 'Content-Type': 'multipart/form-data; boundary=boundary' } });
  assert.equal((await POST(ctx)).status, 204);
});
