import assert from 'node:assert/strict';
import test from 'node:test';
import {gameListQuery,gameListEditorTitle} from '../src/lib/game-list-data.ts';

test('game list filters validate URL state and ignore old pagination links',()=>{
  const {query}=gameListQuery(new URLSearchParams('q=Elden&status=completed&genre=RPG&year=2024&page=2&favorites=true'),'score');
  assert.equal(query.get('status'),'completed');assert.equal(query.get('q'),'Elden');
  assert.equal(query.has('page'),false);assert.equal(query.get('sort'),'score');
  assert.equal(query.get('genre'),'RPG');assert.equal(query.get('year'),'2024');assert.equal(query.get('favorites'),'true');
  const invalid=gameListQuery(new URLSearchParams('page=Infinity&status=watching&year=any&sort=sql')).query;
  assert.equal(invalid.has('status'),false);assert.equal(invalid.has('year'),false);assert.equal(invalid.get('sort'),'title');
});
test('game list editor seeds keep typed identity, independent expansion state and unknown progress',()=>{
  const state={subject:{kind:'game',catalogue_id:7},title:'Expansion',game_class:'expansion',base_work_id:6,listed:true,status:'completed',score:8,total_seconds:null};
  const seed=gameListEditorTitle(state);
  assert.equal(seed.id,7);assert.equal(seed.type,'game');assert.equal(seed.subject,state.subject);
  assert.equal(seed.base_work_id,6);assert.equal(seed.entry.total_seconds,null);assert.equal(seed.game_state,state);
});
