import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import test from 'node:test';

const source = readFileSync(new URL('../src/lib/editor-store.ts', import.meta.url), 'utf8')
  .replace("'./responsive-artwork'", JSON.stringify(new URL('../src/lib/responsive-artwork.ts', import.meta.url).href));
const {EditorStore} = await import(`data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(source)).toString('base64')}`);
const turn = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes,no) => {resolve=yes;reject=no;}); return {promise,resolve,reject}; };
const draft = () => ({id:null,tmdb_id:123,type:'movie',title:'Film',poster:'/poster.jpg',backdrop:'/backdrop.jpg',entry:null,editor_library:{available:true,desired:false}});

test('seeded editor snapshots open without reads, preserve all fields and share the displayed poster', async () => {
  const calls=[];
  const store=new EditorStore(path => {calls.push(path);throw Error('unexpected read');});
  const title=store.seed({...draft(),id:1,entry:{status:'paused',manual_score:8,notes:'Keep me',season_scores:{1:8},progress:3,favorite:true}});
  store.warmArtwork(title,'https://image.tmdb.org/t/p/w185/poster.jpg');
  assert.equal((await store.load(1)).entry.notes,'Keep me');
  assert.equal(store.artwork(title).poster,'https://image.tmdb.org/t/p/w185/poster.jpg');
  assert.equal(store.artwork(title).backdrop,store.artwork(title).poster);
  assert.deepEqual(calls,[]);
});

test('drafts import only on write; rapid changes paint immediately and save in order', async () => {
  const calls=[], pending=[];
  const store=new EditorStore((path,options)=>{calls.push([path,options]);const job=deferred();pending.push(job);return job.promise;});
  const title=store.seed(draft());
  assert.equal(calls.length,0);
  const first=store.write(title,{status:'planning'});
  const second=store.write(title,{status:'watching'});
  assert.equal(title.entry.status,'watching');
  assert.ok(title.entry.start_date);
  await turn();
  assert.equal(calls[0][0],'catalog/movie/123');
  pending[0].resolve({id:7});await turn();
  pending[1].resolve({id:7,status:'planning',notes:'server note'});await first;await turn();
  assert.equal(title.entry.status,'watching');
  store.acceptSaved({id:7,status:'planning'});
  assert.equal(title.entry.status,'watching');
  pending[2].resolve({id:7,status:'watching',notes:'server note'});await second;
  assert.equal(store.find(7).entry.status,'watching');
  assert.equal(calls.filter(([path])=>path.startsWith('catalog/')).length,1);
  assert.deepEqual(calls.slice(1).map(([,options])=>JSON.parse(options.body).status),['planning','watching']);
});

test('failed writes roll back without erasing a newer intent and allow a retry', async () => {
  const pending=[];
  const store=new EditorStore(()=>{const job=deferred();pending.push(job);return job.promise;});
  const title=store.seed({...draft(),id:7,entry:{status:'paused',notes:'Original',score:8}});
  const first=store.write(title,{status:'planning'});
  const failure=assert.rejects(first,/offline/);
  const second=store.write(title,{status:'watching'});
  await turn();pending[0].reject(Error('offline'));await failure;await turn();
  assert.equal(title.entry.status,'watching');
  assert.equal(title.entry.notes,'Original');
  pending[1].reject(Error('offline'));await assert.rejects(second,/offline/);
  assert.equal(title.entry.status,'paused');
  const retry=store.write(title,{status:'watching'});await turn();pending[2].resolve({id:7,status:'watching'});await retry;
  assert.equal(title.entry.status,'watching');
});

test('library clicks remain optimistic and ordered when the editor closes or another write runs', async () => {
  const calls=[],pending=[];
  const store=new EditorStore((path,options)=>{calls.push([path,options]);const job=deferred();pending.push(job);return job.promise;});
  const title=store.seed({...draft(),id:7});
  const first=store.writeLibrary(title,true);
  const second=store.writeLibrary(title,false);
  const entry=store.write(title,{status:'watching'});
  assert.equal(title.editor_library.desired,false);
  assert.equal(title.entry.status,'watching');
  await turn();pending[0].resolve({desired:true});await first;await turn();
  assert.equal(title.editor_library.desired,false);
  pending[1].resolve({desired:false});await second;await turn();
  pending[2].resolve({id:7,status:'watching'});await entry;
  assert.deepEqual(calls.map(([path])=>path),['library/7','library/7','entry/7']);
});

test('cold reads are deduplicated; changing viewers discards data and prevents queued writes', async () => {
  const read=deferred();let requests=0;
  const store=new EditorStore(()=>{requests++;return read.promise;});
  store.scope('one');
  const first=store.load(7), second=store.load(7);
  assert.equal(requests,1);
  read.resolve({...draft(),id:7});await Promise.all([first,second]);
  const save=store.write(store.find(7),{status:'watching'});
  store.scope('two');
  await assert.rejects(save,/account changed/);
  assert.equal(store.find(7),undefined);
  assert.equal(requests,1);
});

test('saved ratings and deletions update every retained reference without losing notes', () => {
  const store = new EditorStore(()=>{});
  const title = store.seed({...draft(),id:7,entry:{status:'planning',notes:'Keep me'}});
  store.acceptSaved({id:7,status:'planning',notes:'Keep me',score:9,manual_score:9});
  assert.equal(store.find(7),title);
  assert.equal(title.entry.score,9);
  assert.equal(title.entry.notes,'Keep me');
  store.acceptSaved({id:7,status:null});
  assert.equal(title.entry,null);
});

test('cold backdrop decoding never changes the artwork chosen for an open editor', async () => {
  const ready=deferred();const previous=globalThis.Image;
  globalThis.Image=class {decode(){return ready.promise;}};
  try {
    const store=new EditorStore(()=>{}), title=store.seed(draft());
    store.warmArtwork(title,'cached-poster');
    const opened=store.artwork(title);
    assert.equal(opened.backdrop,'cached-poster');
    ready.resolve();await turn();
    assert.equal(opened.backdrop,'cached-poster');
    assert.equal(store.artwork(title).backdrop,'https://image.tmdb.org/t/p/w1280/backdrop.jpg');
  } finally {globalThis.Image=previous;}
});
