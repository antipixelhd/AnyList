import assert from 'node:assert/strict';
import test from 'node:test';
import { safeReturnPath, requiresSameOrigin, isSameOrigin } from '../src/lib/request-security.ts';

test('return paths reject browser URL-normalization tricks', () => {
  for (const path of ['https://evil.test', '//evil.test', '/\\evil.test', '/\n/evil.test', '/\t/evil.test', null, 42]) {
    assert.equal(safeReturnPath(path), '/', String(path));
  }
  assert.equal(safeReturnPath('/user/alice/list?sort=score#movies'), '/user/alice/list?sort=score#movies');
  assert.equal(safeReturnPath('', '/home'), '/home');
});

test('origin checks preserve incoming hosts and use the configured public proxy origin', () => {
  const url = new URL('http://localhost:7330/settings');
  function request(host, origin, forwardedHost) {
    const headers = { Host: host };
    if (origin) headers.Origin = origin;
    if (forwardedHost) headers['X-Forwarded-Host'] = forwardedHost;
    return new Request(url, { method: 'POST', headers });
  }
  assert.equal(isSameOrigin(request('127.0.0.1:7330', 'http://127.0.0.1:7330'), url), true);
  assert.equal(isSameOrigin(request('127.0.0.1:7330', 'https://other.test'), url), false);
  assert.equal(isSameOrigin(request('127.0.0.1:7330', null), url), false);
  assert.equal(isSameOrigin(request('internal:7330', 'https://app.test', 'app.test'), url, 'https://app.test'), true);
  assert.equal(isSameOrigin(request('internal:7330', 'http://app.test', 'app.test'), url, 'https://app.test'), false);
  assert.equal(isSameOrigin(request('internal:7330', 'https://other.test', 'other.test'), url, 'https://app.test'), false);
});

test('browser writes require an origin check and independently authenticated API clients remain usable', () => {
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    assert.equal(requiresSameOrigin(method, '/login', false, false), true);
    assert.equal(requiresSameOrigin(method, '/api/proxy/tracking/entry/1', true, false), true);
    assert.equal(requiresSameOrigin(method, '/api/proxy/tracking/entry/1?api_key=invalid', true, false), true);
    assert.equal(requiresSameOrigin(method, '/api/proxy/tracking/entry/1', false, false), false);
    assert.equal(requiresSameOrigin(method, '/api/proxy/tracking/entry/1', true, true), false);
    assert.equal(requiresSameOrigin(method, '/api/proxy/webhooks/plex', false, false), false);
  }
  for (const method of ['GET', 'HEAD', 'OPTIONS']) {
    assert.equal(requiresSameOrigin(method, '/settings', true, false), false);
  }
});
