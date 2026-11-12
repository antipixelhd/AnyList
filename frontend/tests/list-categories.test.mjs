import assert from 'node:assert/strict';
import test from 'node:test';
import {categoryChoices,defaultGameCategories} from '../src/lib/list-categories.ts';
import {gameDatePatch} from '../src/lib/game-editor-data.ts';
import {ProfileListIndex} from '../src/lib/profile-list-index.ts';

test('game categories have stable identities, protected completion and distinct defaults',()=>{
  const categories=defaultGameCategories();
  assert.deepEqual(categoryChoices(categories).map(row=>row[1]),['In Progress','Completed','Played','Paused','Dropped','Planning']);
  const completed=categories.categories.find(row=>row.id==='completed');
  assert.equal(completed.can_rename,false);assert.equal(completed.can_hide,false);
  categories.categories.find(row=>row.id==='played').hidden=true;
  assert.equal(categoryChoices(categories).some(([id])=>id==='played'),false);
  assert.equal(categoryChoices(categories,'played').some(([id])=>id==='played'),true);
});

test('Played uses local today but preserves historical dates and explicit unknown',()=>{
  const today='2026-11-12',empty={start_date:'',finish_date:''};
  assert.deepEqual(gameDatePatch({status:'planning'},'played',empty,new Set(),today),{start_date:today});
  assert.deepEqual(gameDatePatch({status:'planning'},'played',empty,new Set(['start_date']),today),{start_date:null});
  assert.deepEqual(gameDatePatch({status:'playing',start_date:'2020-01-01'},'played',{...empty,start_date:'2020-01-01'},new Set(),today),{});
});

test('complete local membership reorders without replacing data or changing filtering',()=>{
  const choices=categoryChoices(defaultGameCategories());
  const index=new ProfileListIndex(choices);
  index.publish({complete:true,results:[
    [1,'campaign','playing',8,10,null,'2020',[],false,null,1],
    [2,'arena','played',9,100,null,'2021',[],false,null,0],
  ]});
  assert.deepEqual(index.select(new URLSearchParams())?.ids,[1,2]);
  index.setOrder([...choices].reverse());
  assert.deepEqual(index.select(new URLSearchParams())?.ids,[2,1]);
  assert.deepEqual(index.select(new URLSearchParams('status=playing'))?.ids,[1]);
  assert.deepEqual(index.select(new URLSearchParams())?.status_counts,{playing:1,played:1});
});

test('category presentation changes only the status projection of game statistics',async()=>{
  const {gameStatisticsView}=await import('../src/lib/unified-statistics-data.ts');
  const data={totals:{listed_games:2,completed_games:1,recorded_seconds:500,mean_score:8,standard_deviation:0,rated_games:2},scores:[],hours:[],release_years:[],completion_years:[],statuses:[{key:'played',label:'Played',games:1},{key:'completed',label:'Completed',games:1}],coverage:{},comparisons:[{games:1}]};
  const raw={status:'ready',domain:'game',section:'overview',data};
  const before=gameStatisticsView(raw);
  const presentation=defaultGameCategories();
  presentation.categories.find(row=>row.id==='played').label='Tried';
  presentation.categories.find(row=>row.id==='paused').hidden=true;
  presentation.categories.reverse();
  const after=gameStatisticsView({...raw,category_presentation:presentation});
  assert.deepEqual(after.overview.totals,before.overview.totals);
  assert.equal(after.overview.statuses.find(row=>row.key==='played').label,'Tried');
  assert.equal(after.overview.statuses.some(row=>row.key==='paused'),false);
  assert.equal(after.game.data,data);
});
