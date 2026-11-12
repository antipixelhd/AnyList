import assert from 'node:assert/strict';
import test from 'node:test';
import {gameHeroChoice,rememberGameHero} from '../src/lib/game-hero-choice.ts';

test('only detail commits change cycling history, scoped to viewer and viewport, with pins taking precedence',()=>{
  const state={subject:{catalogue_id:7},backdrop:'desktop',backdrop_mobile:'mobile'};
  rememberGameHero('viewer',7,false,'cycle-two',null);
  assert.equal(gameHeroChoice(state,'viewer',false),'cycle-two');
  assert.equal(gameHeroChoice(state,'viewer',false),'cycle-two');
  assert.equal(gameHeroChoice(state,'viewer',true),'mobile');
  assert.equal(gameHeroChoice(state,'other',false),'desktop');
  assert.equal(gameHeroChoice({...state,pinned_banner:'new-pin'},'viewer',false),'new-pin');
  rememberGameHero('viewer',7,false,'old-pin','old-pin');
  assert.equal(gameHeroChoice(state,'viewer',false),'desktop');
  rememberGameHero('viewer',7,false,'displayed-fallback','old-pin');
  assert.equal(gameHeroChoice({...state,pinned_banner:'old-pin'},'viewer',false),'displayed-fallback');
});
