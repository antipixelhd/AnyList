import test from 'node:test';
import assert from 'node:assert/strict';
import {createGameIntent,gameIntentTarget} from '../src/lib/game-intent.ts';

test('intent accepts only canonical game and native discovery links',()=>{
  const origin='https://anylist.test';
  assert.equal(gameIntentTarget(new URL('/games/42',origin),origin),'work/42');
  assert.equal(gameIntentTarget(new URL('/discover-game?id=123',origin),origin),'igdb/123');
  for(const href of ['/games/0','/games/42/editor','/games/42#cast','/discover-game?id=-2','/discover-game?id=99999999999999999','https://other.test/games/42'])
    assert.equal(gameIntentTarget(new URL(href,origin),origin),undefined);
});

test('intent bounds starts and outstanding admission without storing viewer data',async()=>{
  const calls=[];let finish;
  const intent=createGameIntent((path,options)=>{calls.push({path,options});return new Promise(resolve=>finish=resolve);},()=>true);
  intent.warm('work/1');intent.warm('work/2');assert.equal(calls.length,1);
  const complete=async()=>{finish(new Response(null,{status:204}));await new Promise(resolve=>setImmediate(resolve));};
  await complete();intent.warm('work/1');assert.equal(calls.length,1);
  intent.warm('work/2');await complete();intent.warm('igdb/3');await complete();intent.warm('work/4');
  assert.equal(calls.length,3);assert.equal(calls[0].options.cache,'no-store');
  intent.reset();intent.warm('work/4');assert.equal(calls.length,4);await complete();
  createGameIntent(()=>{throw Error('must skip');},()=>false).warm('work/1');
});
