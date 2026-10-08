import assert from 'node:assert/strict';
import { test } from 'node:test';
import { settingsGroups, isSettingsLinkActive } from '../src/lib/settings-navigation.ts';

const links = settingsGroups.flatMap(group => group.links);
const selected = path => links.filter(link => isSettingsLinkActive(link.href, new URL(path, 'https://anylist.test'))).map(link => link.label);

test('shared connection URLs select exactly the requested category', () => {
  assert.deepEqual(selected('/connections'), ['Connections']);
  assert.deepEqual(selected('/connections?section=imports'), ['Import data']);
  assert.deepEqual(selected('/connections?section=apps'), ['Connected apps']);
  assert.deepEqual(selected('/connections?section=developer'), ['Developer']);
});

test('unknown connection categories fall back to Connections', () => {
  assert.deepEqual(selected('/connections?section=unknown'), ['Connections']);
});

test('unrelated query parameters do not clear the current category', () => {
  assert.deepEqual(selected('/user-settings?section=apps'), ['Profile']);
  assert.deepEqual(selected('/settings/media?source=profile'), ['Movies & series']);
  assert.deepEqual(selected('/settings/lists'), ['Lists']);
  assert.deepEqual(selected('/settings/notifications'), ['Notifications']);
  assert.deepEqual(selected('/settings'), ['Account']);
});
