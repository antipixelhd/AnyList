import assert from 'node:assert/strict';
import test from 'node:test';
import { sessionCookieName, secureSessionCookie } from '../src/lib/session-cookies.ts';

test('preview sessions and OIDC state do not collide on one host with different ports', () => {
  for (const name of ['token', 'oidc_state', 'oidc_next']) {
    assert.equal(sessionCookieName(name, ''), name);
    const names = ['beta', 'development-1', 'development-2'].map(slot => sessionCookieName(name, slot));
    assert.equal(new Set(names).size, 3);
    assert.throws(() => sessionCookieName(name, '../../main'));
  }
});

test('HTTPS source previews set Secure without changing ordinary HTTP development', () => {
  assert.equal(secureSessionCookie(false, 'https://preview.test:8001'), true);
  assert.equal(secureSessionCookie(false, 'http://localhost:7340'), false);
  assert.equal(secureSessionCookie(true, ''), true);
});
