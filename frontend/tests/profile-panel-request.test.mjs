import assert from 'node:assert/strict';
import test from 'node:test';
import {profilePanelRequest,profilePanelHeader} from '../src/lib/profile-panel-request.ts';

test('compact responses require an explicit GET on a supported profile page',()=>{
  for(const path of ['/user/alex','/user/alex/movies','/user/alex/stats/games']){
    assert.equal(profilePanelRequest(new Request('https://example.test'+path)),false);
    assert.equal(profilePanelRequest(new Request('https://example.test'+path,{headers:{[profilePanelHeader]:'1'}})),true);
  }
  for(const path of ['/user-settings','/login','/user/alex/favorites/delete','/api/proxy/tracking/people/alex/social']){
    assert.equal(profilePanelRequest(new Request('https://example.test'+path,{headers:{[profilePanelHeader]:'1'}})),false);
  }
  assert.equal(profilePanelRequest(new Request('https://example.test/user/alex',{method:'POST',headers:{[profilePanelHeader]:'1'}})),false);
});
