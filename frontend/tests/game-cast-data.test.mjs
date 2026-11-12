import test from 'node:test';
import assert from 'node:assert/strict';
import {gameCharacterCards,gameCastData,keepCachedGameCredits} from '../src/lib/game-cast-data.ts';
const actor=(id,name,characters,performances=[])=>({id,kind:'actor',name,characters,performances,roles:['Voice actor'],image:'actor-portrait',url:`/actors/catalogue:${id}?media_type=game`});

test('independent characters remain visible without actors and use only their own image',()=>{
  const rows=gameCharacterCards([{id:7,name:'The Knight',image:'character-art',performers:[]}],[]);
  assert.equal(rows.length,1);assert.equal(rows[0].name,'The Knight');assert.equal(rows[0].image,'character-art');
  assert.equal(rows[0].url,null);assert.deepEqual(rows[0].roles,[]);
});

test('graph-backed associations are displayed once and text-only performances never acquire guessed character identity/art',()=>{
  const person=actor(10,'Performer',['Knight','Other'],[{id:7,name:'Knight'}]);
  const rows=gameCharacterCards([{id:7,name:'Knight',image:'verified-art',performers:[{id:10,name:'Performer',url:person.url}]}],[person]);
  assert.deepEqual(rows.map(row=>row.name),['Knight','Other']);assert.equal(rows[0].image,'verified-art');
  assert.equal(rows[1].image,null);assert.equal(rows[1].url,person.url);
  const ambiguous=gameCharacterCards([],[actor(10,'Same Name',['Someone']),actor(20,'Same Name',['Someone'])]);
  assert.equal(ambiguous.length,2);assert.notEqual(ambiguous[0].card_key,ambiguous[1].card_key);
});

test('negative availability requires closed cast and character sources; unknown and partial are not zero',()=>{
  for(const state of [undefined,{complete:false},{complete:true,pending:true}]){
    assert.deepEqual(gameCastData([],[],state,{complete:true}).availability,{actors:'unknown',characters:'unknown'});
  }
  assert.deepEqual(gameCastData([],[],{complete:true},{complete:false}).availability,{actors:'empty',characters:'unknown'});
  assert.deepEqual(gameCastData([],[],{complete:true},{complete:true}).availability,{actors:'empty',characters:'empty'});
  const data=gameCastData([],[{id:7,name:'The Knight',image:null,performers:[]}],{complete:true},{complete:true});
  assert.equal(data.mode,'characters');assert.deepEqual(data.availability,{actors:'empty',characters:'available'});
});

test('large source collections stay complete independently of progressive UI page size',()=>{
  const data=gameCastData([],Array.from({length:1000},(_,id)=>({id,name:`Character ${id}`,image:null,performers:[]})));
  assert.equal(data.characters.length,1000);assert.equal(data.mode,'characters');
});

test('an interrupted empty refresh preserves usable cached credits but not an explicit identity rejection',()=>{
  const cached=gameCastData([actor(1,'Performer',['Role'])],[]);
  const data={people:[],organizations:[],game_characters:[],character_status:{pending:true}};
  assert.equal(keepCachedGameCredits(data,cached),true);
  assert.equal(keepCachedGameCredits({...data,character_status:{pending:false,complete:true}},cached),false);
  assert.equal(keepCachedGameCredits({...data,character_status:{pending:true,error:'identity_conflict'}},cached),false);
  assert.equal(keepCachedGameCredits(data,gameCastData([],[])),false);
  assert.equal(keepCachedGameCredits(data,gameCastData([],[]),true),true);
});
