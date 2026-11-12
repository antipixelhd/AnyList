import test from 'node:test';
import assert from 'node:assert/strict';
import {reviewPerson,reviewTitle,reviewKind,reviewHome,reviewMatch} from '../src/lib/game-credit-review-data.ts';

test('review navigation preserves verified IDs and limits return destinations',()=>{
  assert.equal(reviewHome('nm123','staff'),'/admin/game-credits/people/nm123?kind=staff');
  const path=reviewMatch(42,'tt123','nm456','actor');
  const url=new URL(path,'https://example.test');
  assert.equal(url.pathname,'/admin/game-credits/42');
  assert.equal(url.searchParams.get('imdb'),'tt123');
  assert.equal(url.searchParams.get('q'),'tt123');
  assert.equal(url.searchParams.get('return_person'),'nm456');
  assert.equal(reviewKind('https://evil.test'),'actor');
  for(const input of ['nm1/../../','nm1?x=2','https://evil.test','tt12','nm','nm1\n']){
    assert.equal(reviewPerson(input),null);assert.equal(reviewHome(input),null);
  }
  for(const input of ['tt1/../../','tt1?x=2','nm12','tt','tt1\n'])assert.equal(reviewTitle(input),null);
  for(const work of [0,-1,1.5,Number.MAX_SAFE_INTEGER+1])assert.equal(reviewMatch(work,'tt1','nm1'),null);
});
