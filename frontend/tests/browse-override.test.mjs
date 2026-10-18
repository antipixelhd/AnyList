import assert from 'node:assert/strict';
import test from 'node:test';
import {createBrowseOverride} from '../src/lib/browse-override.ts';

test('Show all persists for the query, including empty discovery, without becoming a saved preference', () => {
  const override = createBrowseOverride('');
  assert.equal(override.forQuery(''), false);
  override.set(true);
  // Filter, type, pagination and layout changes do not redefine the query.
  assert.equal(override.forQuery(''), true);
  assert.equal(override.forQuery('   '), true);
  assert.equal(createBrowseOverride('').forQuery(''), false);
  assert.equal(override.forQuery('Avengers'), false);
  override.set(true);
  assert.equal(override.forQuery(' Avengers '), true);
  override.set(false);
  assert.equal(override.forQuery('Avengers'), false);
});

test('changing the query discards the override even if the old query returns later', () => {
  const override = createBrowseOverride('Below');
  override.set(true);
  assert.equal(override.forQuery('below'), false);
  assert.equal(override.forQuery('Below'), false);
});
