import test from 'node:test';
import assert from 'node:assert/strict';
import {gameCastData,keepCachedGameCredits} from '../src/lib/game-cast-data.ts';

const actor={id:1,kind:'actor',name:'Performer',characters:['Role'],roles:['Voice actor'],image:'actor-portrait'};

test('game cast retains the actor portrait and character names in a single actor view',()=>{
  const cast=gameCastData([actor],{complete:true});
  assert.deepEqual(cast,{actors:[actor],availability:'available'});
});

test('negative actor availability requires completed cast evidence',()=>{
  for(const state of [undefined,{complete:false},{complete:true,pending:true}]){
    assert.equal(gameCastData([],state).availability,'unknown');
  }
  assert.equal(gameCastData([],{complete:true}).availability,'empty');
});

test('an interrupted empty refresh preserves usable cached credits but not an identity rejection',()=>{
  const cached=gameCastData([actor]);
  const data={people:[],organizations:[],character_status:{pending:true}};
  assert.equal(keepCachedGameCredits(data,cached),true);
  assert.equal(keepCachedGameCredits({...data,character_status:{pending:false,complete:true}},cached),false);
  assert.equal(keepCachedGameCredits({...data,character_status:{pending:true,error:'identity_conflict'}},cached),false);
  assert.equal(keepCachedGameCredits(data,gameCastData([])),false);
  assert.equal(keepCachedGameCredits(data,gameCastData([]),true),true);
});
