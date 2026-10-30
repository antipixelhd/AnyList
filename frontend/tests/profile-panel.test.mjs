import assert from 'node:assert/strict';
import test from 'node:test';
import {profilePanelKey,panelActive,panelUrl,setPanelUrl,setPanelWaiting} from '../src/lib/profile-panel.ts';

test('profile panels are limited to lists and statistics of an exact profile',()=>{
  for(const key of ['movies','series','list','games','stats','stats/games'])assert.deepEqual(profilePanelKey(new URL(`https://example.test/user/alex%20one/${key}/?section=genres`)),{username:'alex one',key});
  for(const path of ['/user/alex','/user/alex/favorites','/user/alex/social','/settings','/games/1','/user/alex/stats/games/other'])assert.equal(profilePanelKey(new URL(path,'https://example.test')),undefined);
});
test('inactive completions update their own URL without changing the active page or history',t=>{
  const before=Object.getOwnPropertyDescriptor(globalThis,'history'),calls=[];
  Object.defineProperty(globalThis,'history',{configurable:true,value:{state:{index:9},replaceState:(...args)=>calls.push(args),pushState:(...args)=>calls.push(args)}});
  t.after(()=>{if(before)Object.defineProperty(globalThis,'history',before);else delete globalThis.history;});
  const panel={dataset:{profileUrl:'https://example.test/user/alex/games'}};
  const root={isConnected:false,closest:selector=>selector==='[data-profile-url]'?panel:null};
  setPanelUrl(root,'https://example.test/user/alex/games?page=2');
  assert.equal(panelUrl(root),'https://example.test/user/alex/games?page=2');assert.equal(panelActive(root),false);assert.equal(calls.length,0);
  root.isConnected=true;setPanelUrl(root,'https://example.test/user/alex/games?page=3',true);
  assert.equal(calls.length,1);assert.deepEqual(calls[0][0],{index:9});
});


test('waiting panels disable data controls without disabling domain navigation and restore prior inert states',()=>{
  const controls=[{inert:false},{inert:true}],attributes=new Set();
  const root={toggleAttribute:(name,on)=>on?attributes.add(name):attributes.delete(name),querySelectorAll:selector=>{
    assert.equal(selector.includes('.stats-domain-navigation'),false);return controls;
  }};
  setPanelWaiting(root,true);setPanelWaiting(root,true);
  assert.equal(attributes.has('data-profile-waiting'),true);assert.deepEqual(controls.map(node=>node.inert),[true,true]);
  setPanelWaiting(root,false);assert.equal(attributes.has('data-profile-waiting'),false);assert.deepEqual(controls.map(node=>node.inert),[false,true]);
});
