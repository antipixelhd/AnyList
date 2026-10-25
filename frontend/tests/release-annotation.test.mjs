import assert from 'node:assert/strict';
import test from 'node:test';
import {releaseAnnotation} from '../src/lib/release-annotation.ts';

test('release labels prefer untouched seasons over episodes and disappear when caught up', () => {
  assert.equal(releaseAnnotation(0, 1), 'new episode');
  assert.equal(releaseAnnotation(0, 3), '3 new episodes');
  assert.equal(releaseAnnotation(1, 3), 'new season');
  assert.equal(releaseAnnotation(2, 1), '2 new seasons');
  assert.equal(releaseAnnotation(0, 0), null);
  assert.equal(releaseAnnotation(), null);
});
