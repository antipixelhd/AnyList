import assert from 'node:assert/strict';
import test from 'node:test';
import {anonymousGameDiscoveryRoute as allowed} from '../src/lib/game-public-routes.ts';

test('anonymous Games discovery exposes metadata through the existing admin gate',()=>{
  for(const path of ['/browse/games','/discover-game','/api/proxy/tracking/games/search','/api/proxy/tracking/games/browse','/api/proxy/tracking/games/browse/facets']){
    assert.equal(allowed('GET',path),true);assert.equal(allowed('HEAD',path+'/'),true);
    assert.equal(allowed('POST',path),false);
  }
  assert.equal(allowed('POST','/api/proxy/tracking/games/resolve/1960'),true);
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
