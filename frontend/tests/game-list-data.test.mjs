import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import test from 'node:test';
const source=readFileSync(new URL('../src/lib/game-list-data.ts',import.meta.url),'utf8').replace("'./game-editor-data'",JSON.stringify(new URL('../src/lib/game-editor-data.ts',import.meta.url).href));
const {gameListQuery,gameListHref,gameListRefreshQuery,gameListEditorTitle,compareGameListEntries}=await import(`data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(source)).toString('base64')}`);

test('game list pagination keeps filters and validates incoming URL state',()=>{
  const parsed=gameListQuery(new URLSearchParams('q=Elden&status=completed&genre=RPG&year=2024&page=2&favorites=true'),'score');
  assert.equal(parsed.offset,50);assert.equal(parsed.limit,50);
  const next=new URL(gameListHref('/user/alex/games',parsed.query,{page:3}),'https://example.test');
  assert.equal(next.searchParams.get('status'),'completed');assert.equal(next.searchParams.get('q'),'Elden');
  assert.equal(next.searchParams.get('page'),'3');assert.equal(next.searchParams.get('sort'),'score');
  const invalid=gameListQuery(new URLSearchParams('page=Infinity&status=watching&year=any&sort=sql'));
  assert.equal(invalid.offset,0);assert.equal(invalid.query.has('status'),false);assert.equal(invalid.query.has('year'),false);assert.equal(invalid.query.get('sort'),'title');
});

test('save refresh retains a bounded list page but a changed filter starts from its first page',()=>{
  const current=new URLSearchParams('status=completed&sort=score&page=3');
  assert.equal(gameListRefreshQuery(new URLSearchParams('sort=score&status=completed'),current).get('page'),'3');
  assert.equal(gameListRefreshQuery(new URLSearchParams('sort=score&status=paused'),current).has('page'),false);
  assert.equal(gameListRefreshQuery(new URLSearchParams('sort=score&status=completed&q=Tunic'),current).has('page'),false);
  assert.equal(gameListRefreshQuery(new URLSearchParams('sort=score&status=completed'),new URLSearchParams('status=completed&sort=score&page=Infinity')).has('page'),false);
});
test('game list editor seeds keep typed identity, independent expansion state and unknown progress',()=>{
  const state={subject:{kind:'game',catalogue_id:7},title:'Expansion',game_class:'expansion',base_work_id:6,listed:true,status:'completed',score:8,total_seconds:null};
  const seed=gameListEditorTitle(state);
  assert.equal(seed.id,7);assert.equal(seed.type,'game');assert.equal(seed.subject,state.subject);
  assert.equal(seed.base_work_id,6);assert.equal(seed.entry.total_seconds,null);assert.equal(seed.game_state,state);
});

test('optimistic game ordering preserves deterministic ties and unknown versus zero hours',()=>{
  const rows=[{id:3,title:'C',score:8,total_seconds:null},{id:2,title:'B',score:8,total_seconds:0},{id:1,title:'A',score:9,total_seconds:3600}];
  assert.deepEqual([...rows].sort((a,b)=>compareGameListEntries(a,b,'score')).map(row=>row.id),[1,2,3]);
  assert.deepEqual([...rows].sort((a,b)=>compareGameListEntries(a,b,'progress')).map(row=>row.id),[1,2,3]);
  rows[0].score=10;
  assert.deepEqual([...rows].sort((a,b)=>compareGameListEntries(a,b,'score')).map(row=>row.id),[3,1,2]);
});
