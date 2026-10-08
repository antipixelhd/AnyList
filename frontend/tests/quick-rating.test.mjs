import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../src/components/QuickRating.astro', import.meta.url), 'utf8');
const pickerSource = source.slice(source.indexOf('  async function save('), source.indexOf('  buildStars();'));

function picker(score, onScore) {
  const context = {
    Error,
    active: null,
    savedScore: null,
    pendingScore: null,
    dialog: {},
    title: {},
    poster: {removeAttribute() {}},
    remove: {hidden: true},
    help: {},
    confirmation: {},
    error: {},
    painted: null,
    closing: [],
    opened: 0,
  };
  context.paint = score => { context.painted = score; };
  context.showDialog = () => { context.opened++; };
  context.closeDialog = () => { context.closing.push(context.remove.hidden); };
  runInNewContext(stripTypeScriptTypes(pickerSource) + '\nglobalThis.picker = {save, open};', context);
  const detail = {title: 'A show', score, ratingMode: 'manual', onScore};
  context.picker.open(detail);
  return {context, detail};
}

test('first callback rating keeps the remove action hidden while closing and shows it on reopening', async () => {
  const scores = [];
  const {context, detail} = picker(null, score => scores.push(score));
  await context.picker.save(8);
  assert.deepEqual(scores, [8]);
  assert.deepEqual(context.closing, [true]);
  assert.equal(context.remove.hidden, true);
  assert.equal(context.painted, 8);
  context.picker.open(detail);
  assert.equal(context.remove.hidden, false);
  assert.equal(context.savedScore, 8);
});

test('removing a callback rating keeps the layout stable while closing and hides the action on reopening', async () => {
  const {context, detail} = picker(8, () => {});
  await context.picker.save(null);
  assert.deepEqual(context.closing, [false]);
  assert.equal(context.remove.hidden, false);
  context.picker.open(detail);
  assert.equal(context.remove.hidden, true);
  assert.equal(context.savedScore, null);
});

test('failed asynchronous callback rating restores the previous score and reopens for retry', async () => {
  let reject;
  const pending = new Promise((_, fail) => { reject = fail; });
  const {context, detail} = picker(null, () => pending);
  await context.picker.save(8);
  assert.equal(context.remove.hidden, true);
  reject(new Error('Save failed'));
  await pending.catch(() => {});
  assert.equal(context.opened, 2);
  assert.equal(context.savedScore, null);
  assert.equal(detail.score, null);
  assert.equal(context.painted, null);
  assert.equal(context.remove.hidden, true);
  assert.equal(context.error.hidden, false);
  assert.equal(context.error.textContent, 'Save failed');
});

test('synchronous callback failure leaves the picker open with its previous rating', async () => {
  const {context, detail} = picker(7, () => { throw new Error('Save failed'); });
  await context.picker.save(8);
  assert.deepEqual(context.closing, []);
  assert.equal(context.savedScore, 7);
  assert.equal(detail.score, 7);
  assert.equal(context.remove.hidden, false);
  assert.equal(context.error.hidden, false);
});
