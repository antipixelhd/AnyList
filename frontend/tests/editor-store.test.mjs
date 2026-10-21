import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import test from 'node:test';
import {commitDetailHero} from '../src/lib/detail-hero.ts';

const source = readFileSync(new URL('../src/lib/editor-store.ts', import.meta.url), 'utf8')
  .replace("'./decoded-artwork.ts'", JSON.stringify(new URL('../src/lib/decoded-artwork.ts', import.meta.url).href))
  .replace("'./responsive-artwork'", JSON.stringify(new URL('../src/lib/responsive-artwork.ts', import.meta.url).href))
  .replace("'./date-time'", JSON.stringify(new URL('../src/lib/date-time.ts', import.meta.url).href));
const {EditorStore} = await import(`data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(source)).toString('base64')}`);
const turn = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes,no) => {resolve=yes;reject=no;}); return {promise,resolve,reject}; };
const draft = () => ({id:null,tmdb_id:123,type:'movie',title:'Film',poster:'/poster.jpg',backdrop:'/backdrop.jpg',entry:null,editor_library:{available:true,desired:false}});

const gameState = (id=7, extra={}) => ({subject:{kind:'game',catalogue_id:id},title:'Game',image_url:'/game.jpg',
  game_class:'game',listed:false,status:null,revision:null,favorite:false,...extra});
const gameTitle = state => ({id:state.subject.catalogue_id,subject:state.subject,type:'game',title:state.title,
  poster:state.image_url,game_class:state.game_class,base_work_id:state.base_work_id,game_state:state,
  entry:state.listed?state:null,favorite:state.favorite});

test('game catalogue ids cannot collide with legacy Media ids across reads writes or artwork',async()=>{
  const calls=[];
  const store=new EditorStore(async(path,options)=>{
    calls.push([path,options]);
    if(path==='games/works/7')return gameState();
    if(path==='games/entries/7')return gameState(7,{listed:true,status:'playing',revision:1});
    throw new Error('Unexpected request '+path);
  });
  const movie=store.seed({...draft(),id:7,entry:{status:'watching',progress:4}});
  const game=await store.loadSubject({kind:'game',catalogue_id:7});
  assert.equal(store.find(7),movie);
  assert.equal(await store.load(7),movie);
  assert.equal(store.findSubject({kind:'game',catalogue_id:7}),game);
  store.updateArtwork(7,{poster:'/movie-choice.jpg'});
  assert.equal(game.poster,'/game.jpg');
  store.updateSubjectArtwork({kind:'game',catalogue_id:7},{poster:'/game-choice.jpg'});
  assert.equal(movie.poster,'/movie-choice.jpg');
  await store.write(game,{status:'playing'});
  assert.equal(game.entry.status,'playing');
  assert.equal(movie.entry.status,'watching');
  assert.equal(movie.entry.progress,4);
  store.acceptSaved({id:7,status:'completed'});
  assert.equal(game.entry.status,'playing');
  assert.equal(movie.entry.status,'completed');
  assert.deepEqual(calls.map(([path])=>path),['games/works/7','games/entries/7']);
});

test('queued game writes use committed revisions and preserve completed status during hours updates',async()=>{
  const pending=deferred();const calls=[];
  const store=new EditorStore(async(path,options)=>{
    const body=JSON.parse(options.body);calls.push({path,body});
    if(calls.length===1)return pending.promise;
    return gameState(7,{listed:true,status:'completed',revision:3,total_seconds:7200,completion_seconds:3600});
  });
  const title=store.seed(gameTitle(gameState(7,{listed:true,status:'completed',revision:1,total_seconds:3600,completion_seconds:3600})));
  const score=store.write(title,{score:9});
  const hours=store.write(title,{total_seconds:7200});
  assert.equal(title.entry.status,'completed');
  assert.equal(title.entry.completion_seconds,3600);
  await turn();pending.resolve(gameState(7,{listed:true,status:'completed',revision:2,total_seconds:3600,completion_seconds:3600,score:9}));
  await Promise.all([score,hours]);
  assert.deepEqual(calls.map(c=>c.body.revision),[1,2]);
  assert.ok(calls.every(c=>c.path==='games/entries/7'));
  assert.equal(title.entry.total_seconds,7200);
});

test('game achievement summaries update optimistically, preserve zero and roll back failed writes',async()=>{
  const pending=deferred();const store=new EditorStore(()=>pending.promise);
  const original={mode:'percentage',percentage:40,count:null,total:null,exact_count:7};
  const title=store.seed(gameTitle(gameState(7,{listed:true,status:'completed',revision:1,achievements:original})));
  const write=store.write(title,{achievement_mode:'percentage',achievement_percentage:0});
  assert.equal(title.entry.achievements.percentage,0);
  assert.equal(title.entry.achievements.exact_count,7);
  assert.equal(original.percentage,40);
  await turn();pending.reject(new Error('Retry save'));
  await assert.rejects(write,/Retry save/);
  assert.equal(title.entry.achievements.percentage,40);
  assert.equal(title.entry.achievements.mode,'percentage');
});

test('unlisted expansion editor uses retained revision without creating an independent entry',async()=>{
  const calls=[];const store=new EditorStore(async(path,options)=>{
    calls.push({path,body:JSON.parse(options.body)});
    return gameState(8,{game_class:'expansion',base_work_id:7,listed:true,status:'completed',score:8,revision:5});
  });
  const title=store.seed(gameTitle(gameState(8,{game_class:'expansion',base_work_id:7,status:'completed',score:8,revision:4})));
  assert.equal(title.entry,null);
  await store.write(title,{score:8});
  assert.equal(calls[0].path,'games/children/8');
  assert.equal(calls[0].body.revision,4);
  assert.equal(title.entry.listed,true);
});

test('targeted achievement optimism uses exact evidence instead of the previous simplified count',async()=>{
  const pending=deferred();const store=new EditorStore(()=>pending.promise);
  const original={mode:'count',count:8,percentage:80,total:10,exact_count:2};
  const title=store.seed(gameTitle(gameState(7,{listed:true,status:'playing',revision:1,achievements:original})));
  const write=store.write(title,{achievement_mode:'targeted',unlock_updates:{13:true},expected_achievement_total:10});
  assert.equal(title.entry.achievements.count,3);
  assert.equal(title.entry.achievements.percentage,30);
  assert.equal(original.count,8);
  await turn();pending.resolve(gameState(7,{listed:true,status:'playing',revision:2,achievements:{...original,mode:'targeted',count:3,exact_count:3,percentage:30}}));
  await write;
  assert.equal(title.entry.achievements.count,3);
});

test('game favorite actions do not create list membership and never use the streaming library',async()=>{
  const calls=[];const store=new EditorStore(async(path,options)=>{
    calls.push({path,method:options.method});return gameState(7,{favorite:true});
  });
  const title=store.seed(gameTitle(gameState()));
  await store.write(title,{favorite:true});
  assert.equal(title.entry,null);assert.equal(title.favorite,true);
  assert.deepEqual(calls,[{path:'games/favorites/7',method:'PUT'}]);
  await assert.rejects(store.writeLibrary(title,true));
  assert.equal(calls.length,1);
});

test('predicted metadata preserves pending list edits and existing artwork',async()=>{
  const pending=deferred();const store=new EditorStore(()=>pending.promise);
  const title=store.seed({...draft(),id:1,entry:{status:'planning',progress:0},favorite:true});
  const write=store.write(title,{status:'watching'});
  store.warmMetadata({...draft(),overview:'Preview',runtime:120,poster:'/different.jpg',entry:null,favorite:false});
  assert.equal(title.entry.status,'watching');assert.equal(title.favorite,true);
  assert.equal(title.poster,'/poster.jpg');assert.equal(title.overview,'Preview');
  await turn();pending.resolve({status:'watching',progress:1});await write;
  assert.equal(title.entry.progress,1);assert.equal(title.runtime,120);
});

test('favorites preserve absent list membership and roll back independently on errors',async()=>{
  let pending=deferred();
  const store=new EditorStore(()=>pending.promise);
  const title=store.seed({...draft(),id:1});
  const first=store.write(title,{favorite:true});
  assert.equal(title.entry,null);assert.equal(title.favorite,true);
  await turn();pending.resolve({id:1,status:null,favorite:true});await first;
  assert.equal(title.entry,null);assert.equal(title.favorite,true);
  pending=deferred();const failed=store.write(title,{favorite:false});
  assert.equal(title.entry,null);assert.equal(title.favorite,false);
  await turn();pending.reject(new Error('Unavailable'));await assert.rejects(failed);
  assert.equal(title.entry,null);assert.equal(title.favorite,true);
});

test('favoriting tracked titles preserves status, progress and dates',async()=>{
  const entry={status:'paused',progress:7,start_date:'2025-01-01',finish_date:null,favorite:false};
  const store=new EditorStore(async()=>({...entry,favorite:true}));
  const title=store.seed({...draft(),id:1,entry});
  const saved=store.write(title,{favorite:true});
  assert.equal(title.entry.status,'paused');assert.equal(title.entry.progress,7);
  await saved;assert.equal(title.entry.start_date,'2025-01-01');assert.equal(title.entry.status,'paused');
});

test('commit notifications follow successful writes and removals, never optimistic or failed writes',async()=>{
  let pending=deferred();const commits=[];
  const store=new EditorStore(()=>pending.promise,()=>{},(title,patch)=>commits.push({entry:title.entry&&{...title.entry},patch}));
  const title=store.seed({...draft(),id:1,entry:{status:'planning',progress:0}});
  const first=store.write(title,{status:'completed'});
  assert.equal(commits.length,0);
  await turn();pending.resolve({status:'completed',progress:5});await first;
  assert.equal(commits[0].entry.progress,5);
  assert.deepEqual(commits[0].patch,{status:'completed'});
  pending=deferred();const failed=store.write(title,{status:'watching'});
  await turn();pending.reject(new Error('Unavailable'));await assert.rejects(failed);
  assert.equal(commits.length,1);
  assert.equal(title.entry.status,'completed');
  pending=deferred();const removed=store.remove(title);
  await turn();pending.resolve({});await removed;
  assert.deepEqual(commits[1],{entry:null,patch:null});
});

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

test('deletion waits for draft import and queued saves and clears the shared entry', async () => {
  const calls=[], pending=[];
  const store=new EditorStore((path,options)=>{calls.push([path,options]);const job=deferred();pending.push(job);return job.promise;});
  const title=store.seed(draft());
  const first=store.write(title,{status:'planning'});
  const second=store.write(title,{status:'watching'});
  await turn();
  const removed=store.remove(title);
  assert.equal(title.entry,null);
  assert.equal(calls.length,1);
  pending[0].resolve({id:7});await turn();
  pending[1].resolve({id:7,status:'planning'});await first;await turn();
  assert.equal(title.entry,null);
  pending[2].resolve({id:7,status:'watching'});await second;await turn();
  assert.deepEqual(calls.map(([path])=>path),['catalog/movie/123','entry/7','entry/7','entry/7?confirmed=true']);
  assert.equal(calls[3][1].method,'DELETE');
  pending[3].resolve({});assert.equal(await removed,7);
  assert.equal(store.find(7).entry,null);
});

test('failed deletion restores the saved entry and can be retried', async () => {
  const pending=[];
  const store=new EditorStore(()=>{const job=deferred();pending.push(job);return job.promise;});
  const title=store.seed({...draft(),id:7,entry:{status:'watching',notes:'Keep me'}});
  const removed=store.remove(title);
  const failure=assert.rejects(removed,/offline/);
  await turn();pending[0].reject(Error('offline'));await failure;
  assert.equal(title.entry.notes,'Keep me');
  const retry=store.remove(title);await turn();pending[1].resolve({});await retry;
  assert.equal(title.entry,null);
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

test('background enrichment does not download editor artwork until intent, and reuses the detail source',async()=>{
  const previous=globalThis.Image;let images=0;
  globalThis.Image=class {constructor(){images++;}decode(){return Promise.resolve();}};
  try{
    const store=new EditorStore(()=>{}),title=store.seed({...draft(),id:7});
    store.updateArtwork(7,{backdrop:'/selected.jpg'});assert.equal(images,0);
    store.warmArtwork(title,undefined,'https://image.tmdb.org/t/p/original/selected.jpg');
    await turn();
    assert.equal(store.artwork(title).backdrop,'https://image.tmdb.org/t/p/original/selected.jpg');
    store.warmArtwork(title);assert.equal(images,1);
  }finally{globalThis.Image=previous;}
});

test('a committed MetaHub fallback is reused when opening the editor without a gallery read',async()=>{
  const previous=globalThis.Image,requests=[];
  globalThis.Image=class {set src(value){this.url=value;requests.push(value);}decode(){return Promise.resolve();}};
  try{
    const original='https://images.metahub.space/background/high/tt0133093/img';
    const fallback='https://artworks.thetvdb.com/banners/fallback.jpg';
    const save=deferred(),store=new EditorStore(()=>save.promise);
    const title=store.seed({...draft(),id:7,backdrop:original});
    const pending=store.write(title,{favorite:true});
    const hero={className:'lab-hero-image',replaceWith(image){this.replacement=image;}};
    const candidate={hidden:true},button={dataset:{},hidden:true};
    commitDetailHero(hero,candidate,fallback,button,backdrop=>store.updateArtwork(7,{backdrop}));
    assert.strictEqual(hero.replacement,candidate);assert.equal(candidate.hidden,false);
    assert.equal(button.dataset.detailImage,fallback);assert.equal(title.backdrop,fallback);
    assert.equal(title.favorite,true);assert.equal(title.entry,null);
    assert.deepEqual(requests,[]);
    store.warmArtwork(title,'cached-poster',fallback);await turn();
    assert.equal(store.artwork(title).backdrop,fallback);assert.deepEqual(requests,[fallback]);
    save.resolve({id:7,status:null,favorite:true});await pending;
    assert.equal(title.favorite,true);
  }finally{globalThis.Image=previous;}
});

test('curated artwork updates preserve optimistic intent and ignore an old decode',async()=>{
  const previous=globalThis.Image,old=deferred(),curated=deferred(),save=deferred();let images=0;
  globalThis.Image=class {decode(){return images++===0?old.promise:curated.promise;}};
  try {
    const store=new EditorStore(()=>save.promise),title=store.seed({...draft(),id:7});
    store.warmArtwork(title,'cached-poster');
    const pending=store.write(title,{favorite:true});
    store.updateArtwork(7,{poster:'/new-poster.jpg',backdrop:'/new-background.jpg'});
    store.warmArtwork(title);
    assert.equal(title.favorite,true);assert.equal(title.entry,null);
    old.resolve();await turn();
    assert.equal(store.artwork(title).backdrop,'https://image.tmdb.org/t/p/w342/new-poster.jpg');
    curated.resolve();await turn();
    assert.equal(store.artwork(title).backdrop,'https://image.tmdb.org/t/p/w1280/new-background.jpg');
    save.resolve({id:7,status:null,favorite:true});await pending;
    assert.equal(title.poster,'/new-poster.jpg');assert.equal(title.favorite,true);
  }finally{globalThis.Image=previous;}
});

test('typed browse invalidation preserves pending game writes and reloads complete state afterward',async()=>{
  const pending=deferred();let reads=0;
  const store=new EditorStore(async(_path,options)=>{
    if(options?.method==='PATCH')return await pending.promise;
    reads++;return gameState(7,{listed:true,status:'playing',revision:3,notes:'Fresh private note'});
  });
  store.scope('viewer');
  const game=store.seed(gameTitle(gameState(7,{listed:true,status:'playing',revision:1,notes:'Old private note'})));
  const movie=store.seed({id:7,type:'movie',title:'Other domain',entry:{status:'watching'}});
  store.invalidateSubject(game.subject);
  const write=store.write(game,{score:9});
  assert.equal(store.hasPendingSubject(game.subject),true);
  store.invalidateSubject(game.subject);
  assert.equal(store.findSubject(game.subject),game);
  await turn();pending.resolve(gameState(7,{listed:true,status:'playing',revision:2,score:9,notes:'Old private note'}));await write;
  assert.equal(store.hasPendingSubject(game.subject),false);
  store.invalidateSubject(game.subject);
  assert.equal(store.findSubject(game.subject),undefined);
  const fresh=await store.loadSubject(game.subject);
  assert.equal(fresh.entry.notes,'Fresh private note');assert.equal(reads,1);
  assert.equal(store.find(7),movie);
});
