import test from 'node:test';
import assert from 'node:assert/strict';
import {gameCastRows,gameCastLanguages,gameCastLanguage} from '../src/lib/game-credit-language.ts';
import {gameCastData} from '../src/lib/game-cast-data.ts';

const performance=(characters,languages=[],extra={})=>({source:'imdb',characters,languages,...extra});
const actor=(id,performances)=>({id,kind:'actor',name:`Actor ${id}`,url:`/actors/catalogue:${id}`,roles:['Voice actor'],
  characters:[...new Set(performances.flatMap(row=>row.characters))],credit_performances:performances});

test('English evidence comes first without dropping unlabelled or other-language performers or changing source order',()=>{
  const input=[actor(1,[performance(['Unknown'])]),actor(2,[performance(['Deutsch'],['de'])]),
    actor(3,[performance(['English'],['en'])]),actor(4,[performance(['English UK'],['en-GB'])]),actor(5,[performance(['Other'])])];
  const original=structuredClone(input);
  assert.deepEqual(gameCastRows(input).map(row=>row.id),[3,4,1,2,5]);
  assert.deepEqual(input,original);
});

test('a language filters each performance and character association rather than labelling an actor language union',()=>{
  const source=actor(1,[performance(['English role'],['en'],{credited_as:'English alias',uncredited:null}),
    performance(['German role'],['de'],{uncredited:true}),performance(['Unknown role'],[],{voice:null})]);
  source.languages=['en','de',''];
  const original=structuredClone(source);
  const english=gameCastRows([source],'en')[0];
  assert.deepEqual(english.characters,['English role']);assert.equal(english.credit_performances.length,1);
  assert.equal(english.credit_performances[0].credited_as,'English alias');
  assert.equal(english.credit_performances[0].uncredited,null);
  const unknown=gameCastRows([source],'unknown')[0];
  assert.deepEqual(unknown.characters,['Unknown role']);assert.equal(unknown.credit_performances[0].voice,null);
  assert.deepEqual(source,original);
});

test('no title language or inferred voice fills missing per-performance language',()=>{
  const unknown=actor(1,[performance(['Melina'],[],{voice:true})]);
  unknown.original_language='en';unknown.title_languages=['English'];
  assert.deepEqual(gameCastRows([unknown],'en'),[]);
  assert.deepEqual(gameCastLanguages([unknown]),[['all','All languages'],['unknown','Language unknown']]);
  assert.equal(gameCastLanguage([unknown],'unknown'),'all','a redundant single-bucket control stays hidden');
});

test('all languages retain independent unvoiced characters and source availability regardless of actor view',()=>{
  const actors=[actor(1,[performance(['A'],['en'])]),actor(2,[performance(['B'])])];
  const state=gameCastData(actors,[{id:20,name:'Silent hero',image:'art',performers:[]}],{complete:true},{complete:true});
  const view=gameCastRows(state.actors,'en');
  assert.equal(view.length,1);assert.equal(state.actors.length,2);
  assert.equal(state.characters[0].name,'Silent hero');assert.equal(state.characters[0].image,'art');
  assert.deepEqual(state.availability,{actors:'available',characters:'available'});
});

test('options distinguish explicit language variants and unknown; invalidated selections recover without a false empty cast',()=>{
  const input=[actor(1,[performance(['English'],['en'])]),actor(2,[performance(['French'],['fr'])]),
    actor(3,[performance(['Unknown'])])];
  assert.deepEqual(gameCastLanguages(input),[['all','All languages'],['en','English'],['fr','French'],['unknown','Language unknown']]);
  assert.equal(gameCastLanguage(input,'en'),'en');assert.equal(gameCastLanguage(input,'ja'),'all');
  assert.equal(gameCastLanguage([input[2]],'en'),'all');
  const sameNames=input.map(person=>({...person,name:'Same Name'}));
  assert.equal(gameCastRows(sameNames).length,3,'presentation never merges names');
});

test('older aggregate-only records do not attribute a character to an ambiguous language',()=>{
  const old={id:1,kind:'actor',name:'Legacy',characters:['Unscoped role'],roles:['Actor'],languages:['en','de']};
  assert.deepEqual(gameCastRows([old],'en')[0].characters,[]);
  assert.deepEqual(gameCastRows([old])[0].characters,['Unscoped role']);
});

test('large casts remain complete; filtering is independent of the progressive card budget',()=>{
  const input=Array.from({length:2000},(_,id)=>actor(id,[performance([`Role ${id}`],id%2?['en']:[])]));
  assert.equal(gameCastRows(input).length,2000);assert.equal(gameCastRows(input,'en').length,1000);
});
