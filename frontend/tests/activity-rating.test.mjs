import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

const trackerSource = readFileSync(new URL('../src/lib/tracker.ts', import.meta.url), 'utf8')
  .replace('"./responsive-artwork"', JSON.stringify(new URL('../src/lib/responsive-artwork.ts', import.meta.url).href));
const {activityRating, activityAction} = await import(
  `data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(trackerSource)).toString('base64')}`
);

test('initial rating corrections show only the final score and Rated headline', () => {
  const activity = {score: 8, payload: {rating_changed: true, rating_first: true}, media: {type: 'movie'}};
  assert.equal(activityAction(activity), 'Rated');
  assert.equal(activityRating(activity).current, 8);
  assert.equal(activityRating(activity).previous, null);
});

test('post-cooldown changes show the previous score while absent ratings stay hidden', () => {
  const activity = {score: 8, payload: {rating_changed: true, rating_first: false, previous_score: 7}};
  assert.equal(activityAction(activity), 'Changed Rating for');
  assert.equal(activityRating(activity).previous, 7);
  assert.equal(activityRating({...activity, score: null}), null);
  assert.equal(activityRating({...activity, score: 0}), null);
  assert.equal(activityRating({...activity, payload: {}}), null);
});

test('feed refresh updates scores without changing timestamps or order and removes deleted cards', () => {
  const source = readFileSync(new URL('../src/pages/home.astro', import.meta.url), 'utf8');
  const reconcileSource = source.slice(source.indexOf('  const reconcile='), source.indexOf('  const refresh=async'));
  const elements = [];
  function card(activity) {
    const element = {dataset: {activityKey: activity.key, createdAt: activity.created_at}, score: activity.score};
    element.remove = () => { const index = elements.indexOf(element); if (index >= 0) elements.splice(index, 1); };
    return element;
  }
  elements.push(card({key: 'newer', created_at: '2026-10-03', score: 7}),
                card({key: 'original', created_at: '2026-10-01', score: 6}),
                card({key: 'removed', created_at: '2026-09-30', score: 5}));
  const context = {
    feed: {querySelector: () => null, querySelectorAll: () => [...elements], append: element => elements.push(element)},
    render: card, emptyTemplate: null,
  };
  runInNewContext(stripTypeScriptTypes(reconcileSource) + '\nglobalThis.reconcile = reconcile;', context);
  context.reconcile([{key: 'newer', created_at: '2026-10-03', score: 7},
                     {key: 'original', created_at: '2026-10-01', score: 8}]);
  assert.deepEqual(elements.map(element => element.dataset.activityKey), ['newer', 'original']);
  assert.equal(elements[1].score, 8);
  assert.equal(elements[1].dataset.createdAt, '2026-10-01');
});
