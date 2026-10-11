import assert from 'node:assert/strict';
import test from 'node:test';
import { actorArtwork, actorTitleImage, actorTitleImages, actorTitleLabel, hasCharacterImages, rankedActors } from '../src/lib/stats-actors-data.ts';
const row = (i) => ({ key: String(i), label: `Actor ${i}`, titles: i, mean_score: i === 39 ? null : i / 4, minutes: 40 - i, top_titles: [] });
test('actor cards select the top thirty for each metric without changing snapshot order', () => {
  const rows = Array.from({ length: 40 }, (_, i) => row(i));
  assert.equal(rankedActors(rows, 'count').length, 30);
  assert.equal(rankedActors(rows, 'count')[0].titles, 39);
  assert.equal(rankedActors(rows, 'mean_score')[0].titles, 38);
  assert.equal(rankedActors(rows, 'time')[0].titles, 0);
  assert.equal(rows[0].titles, 0);
});

test('character images try title artwork before shared role images and poster', () => {
  const title = { character_image: 'local.jpg', character_images: ['local.jpg', 'sequel.jpg', 'other.jpg'], poster: 'poster.jpg' };
  assert.deepEqual(actorTitleImages(title, 'characters'), ['local.jpg', 'sequel.jpg', 'other.jpg', 'poster.jpg']);
  assert.deepEqual(actorTitleImages(title, 'media'), ['poster.jpg']);
  assert.equal(hasCharacterImages([{ top_titles: [{ ...title, character_image: null }] }]), true);
  assert.deepEqual(actorTitleImages({ poster: null }, 'characters'), []);
});
test('role artwork and hover labels use character metadata with honest media fallback', () => {
  const title = { title: 'Film', character: 'Alice', poster: 'poster.jpg', character_image: 'role.jpg' };
  assert.equal(actorTitleLabel(title), 'Film – Alice');
  assert.equal(actorTitleLabel({ ...title, character: null }), 'Film');
  assert.equal(actorTitleImage(title, 'characters'), 'role.jpg');
  assert.equal(actorTitleImage(title, 'media'), 'poster.jpg');
  assert.equal(actorTitleImage({ ...title, character_image: null }, 'characters'), 'poster.jpg');
  assert.equal(hasCharacterImages([{ top_titles: [title] }]), true);
  assert.equal(hasCharacterImages([{ top_titles: [{ ...title, character_image: null }] }]), false);
  assert.equal(actorArtwork('invalid'), 'media');
});
