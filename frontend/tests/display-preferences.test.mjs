import assert from 'node:assert/strict';
import test from 'node:test';
import { displayPreferenceCookies } from '../src/lib/display-preferences.ts';

test('fresh browsers get the account display preferences on any page', () => {
  assert.deepEqual(displayPreferenceCookies({blur_explicit: false, time_format_24h: true}), [
    ['blur_explicit', 'false'], ['time_format_24h', 'true'],
  ]);
  assert.deepEqual(displayPreferenceCookies({blur_explicit: true, time_format_24h: false}), [
    ['blur_explicit', 'true'], ['time_format_24h', 'false'],
  ]);
});

test('unspecified preferences keep the established account defaults', () => {
  assert.deepEqual(displayPreferenceCookies({blur_explicit: null, time_format_24h: null}), [
    ['blur_explicit', 'true'], ['time_format_24h', 'true'],
  ]);
});

test('a transient settings outage does not overwrite existing cookies', () => {
  assert.deepEqual(displayPreferenceCookies(undefined), []);
});
