import assert from 'node:assert/strict';
import test from 'node:test';
import {anonymousGameDiscoveryRoute as allowed,approvedGamesPage,reusableGamesPage} from '../src/lib/game-public-routes.ts';

test('Games routing reachability is independent of backend approval',()=>{
  for(const path of ['/browse/games','/discover-game','/api/proxy/tracking/games/search','/api/proxy/tracking/games/browse','/api/proxy/tracking/games/browse/facets']){
    assert.equal(allowed('GET',path),true);assert.equal(allowed('HEAD',path+'/'),true);
    assert.equal(allowed('POST',path),false);
  }
  assert.equal(allowed('POST','/api/proxy/tracking/games/resolve/1960'),true);
});

test('only Browse and profile Games tabs permit snapshots behind fresh authorization',()=>{
  for(const path of ['/browse/games','/user/alex/games','/user/alex/stats/games'])assert(reusableGamesPage(new URL(path,'https://example.test')));
  for(const path of ['/games/1','/discover-game','/actors/catalogue:1?media_type=game','/user/alex/games/private'])assert.equal(reusableGamesPage(new URL(path,'https://example.test')),false);
});

test('Games documents always require a fresh authorized navigation',()=>{
  for(const path of ['/games/1','/games/1/social','/browse/games','/discover-game?id=1',
    '/user/alex/games','/actors/catalogue:1?media_type=game','/person/tmdb:2?media_type=game','/studio/catalogue:3?media_type=game',
    '/actors/catalogue:1?domain=game','/person/tmdb:2?domain=game']){
    assert.equal(approvedGamesPage(new URL(path,'https://anylist.example')),true,path);
  }
  for(const path of ['/browse','/user/alex/movies','/user/alex/series','/actors/tmdb:1?media_type=movie']){
    assert.equal(approvedGamesPage(new URL(path,'https://anylist.example')),false,path);
  }
});

test('metadata permission never opens private tracking or malformed resolver routes',()=>{
  for(const method of ['GET','HEAD','POST','PATCH','PUT','DELETE'])for(const path of [
    '/api/proxy/tracking/games/state','/api/proxy/tracking/games/entries/1',
    '/api/proxy/tracking/games/children/1','/api/proxy/tracking/games/favorites',
    '/api/proxy/tracking/games/resolve/0','/api/proxy/tracking/games/resolve/-1',
    '/api/proxy/tracking/games/resolve/1/extra','/browse/games/private'])assert.equal(allowed(method,path),false);
  assert.equal(allowed('GET','/api/proxy/tracking/games/resolve/1960'),false);
  assert.equal(allowed('DELETE','/api/proxy/tracking/games/resolve/1960'),false);
});
