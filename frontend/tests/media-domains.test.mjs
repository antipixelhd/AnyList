import assert from 'node:assert/strict';
import test from 'node:test';
import {mediaDomains,profileMediaDomains,domainListLinks} from '../src/lib/media-domains.ts';

test('viewer domain opt-out hides the whole movie/series domain while preserving its split preference',()=>{
  const hidden=mediaDomains({show_movies_series:false,combine_lists:false});
  assert.deepEqual(domainListLinks('/user/alex',hidden).map(row=>row.href),['/user/alex/games']);
  assert.deepEqual(domainListLinks('/user/alex',{...hidden,show_movies_series:true}).map(row=>row.href),['/user/alex/movies','/user/alex/series','/user/alex/games']);
});
test('viewer visibility never removes another users public profile sections',()=>{
  const viewer=mediaDomains({show_movies_series:false,show_games:false});
  assert.deepEqual(domainListLinks('/user/alex',profileMediaDomains(true,viewer)),[]);
  assert.equal(domainListLinks('/user/sam',profileMediaDomains(false,viewer)).length,2);
  assert.equal(profileMediaDomains(false,viewer).show_game_achievements,true);
});

test('another profile never grants a viewer Games access',()=>{
  const viewer=mediaDomains({show_games:false,can_access_games:false});
  assert.equal(profileMediaDomains(false,viewer).show_games,false);
  assert.deepEqual(domainListLinks('/user/sam',profileMediaDomains(false,viewer)).map(row=>row.href),['/user/sam/list']);
});
