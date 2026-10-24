import assert from 'node:assert/strict';
import test from 'node:test';
import {createImportDraft} from '../src/lib/import-draft.ts';

function fixture() {
  let session = {id: 'amazon-or-netflix', revision: 1, items: {a: {}, b: {}}};
  let server = structuredClone(session);
  const requests = [];
  const draft = createImportDraft({
    getSession: () => session,
    setSession: value => {session = value;},
    apply: (value, patch) => Object.assign(value.items[patch.itemId], patch.changes),
    save: (value, patch) => new Promise((resolve, reject) => {
      const snapshot = structuredClone(server);
      snapshot.revision = value.revision;
      requests.push({revision: value.revision, patch: structuredClone(patch), reject, finish: () => {
        Object.assign(snapshot.items[patch.itemId], patch.changes);
        snapshot.revision++;
        server = structuredClone(snapshot);
        resolve(snapshot);
      }});
    }),
  });
  return {draft, requests, session: () => session, replace: value => {session = value;}};
}

test('choices stay local and repeated edits to one entry save its final choices together', async () => {
  const f = fixture();
  f.draft.stage({itemId: 'a', changes: {status: 'partial', latest_episode: 1}});
  f.draft.stage({itemId: 'a', changes: {latest_episode: 8}});
  f.draft.stage({itemId: 'b', changes: {status: 'skip'}});
  assert.equal(f.requests.length, 0);
  assert.equal(f.session().items.a.latest_episode, 8);
  const saved = f.draft.flush();
  assert.equal(f.requests.length, 1);
  assert.deepEqual(f.requests[0].patch.changes, {status: 'partial', latest_episode: 8});
  f.requests[0].finish();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(f.requests[1].revision, 2);
  assert.equal(f.session().items.b.status, 'skip');
  f.requests[1].finish(); await saved;
  assert.equal(f.session().revision, 3);
});

test('a delayed response cannot undo a newer choice for the same entry', async () => {
  const f = fixture();
  f.draft.stage({itemId: 'a', changes: {status: 'skip'}});
  const saved = f.draft.flush();
  f.draft.stage({itemId: 'a', changes: {status: 'partial', latest_episode: 8}});
  f.requests[0].finish();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(f.session().items.a.status, 'partial');
  assert.equal(f.session().items.a.latest_episode, 8);
  f.requests[1].finish(); await saved;
  assert.equal(f.session().items.a.status, 'partial');
});

test('save failure keeps unsaved choices across server refresh and retry', async () => {
  const f = fixture();
  f.draft.stage({itemId: 'a', changes: {latest_episode: 8}});
  const failed = f.draft.flush();
  f.requests[0].reject(new Error('offline'));
  await assert.rejects(failed, /offline/);
  f.draft.rebase({id: 'amazon-or-netflix', revision: 7, items: {a: {}, b: {}}});
  assert.equal(f.session().items.a.latest_episode, 8);
  const saved = f.draft.flush();
  assert.equal(f.requests[1].revision, 7);
  f.requests[1].finish(); await saved;
  assert.equal(f.session().items.a.latest_episode, 8);
});

test('a cancelled import cannot be restored by an old save response', async () => {
  const f = fixture();
  f.draft.stage({itemId: 'a', changes: {status: 'partial'}});
  const saved = f.draft.flush();
  f.replace(null); f.requests[0].finish(); await saved;
  assert.equal(f.session(), null);
  f.replace({id: 'new-import', revision: 1, items: {a: {}, b: {}}});
  await f.draft.flush();
  assert.equal(f.requests.length, 1);
  assert.deepEqual(f.session().items.a, {});
});
