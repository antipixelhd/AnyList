import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
const source=readFileSync(new URL('../src/lib/media-detail.ts',import.meta.url),'utf8');
const start=source.indexOf('  const loadSocial=()=>{');
const snippet=source.slice(start,source.indexOf("  document.addEventListener('anylist:editor-committed'",start));
const tick=()=>new Promise(setImmediate);
function fixture(){
  const host={children:[{textContent:'Loading social activity…'}],querySelector:()=>null,replaceChildren(...nodes){this.children=nodes;}};
  const content={kind:'activity'},controller=new AbortController();let fail=true,stylesReady;
  const context={root:{querySelector:()=>host},socialLoaded:false,socialPending:undefined,socialRevision:0,signal:controller.signal,
    info:{id:1,type:'game'},location:{href:'https://example.test/games/1'},Error,
    fetch:async()=>{if(fail)throw new Error('Network failed');return {ok:true,url:'https://example.test/games/1/social',text:async()=>'<activity>'};},
    DOMParser:class{parseFromString(){return {querySelector:()=>content};}},
    document:{createElement:tag=>({tag,addEventListener(type,handler){this[type]=handler;}}),importNode:node=>node},localizeDateTimes(){},
    preparePanelStyles:()=>new Promise(resolve=>stylesReady=resolve),
  };
  runInNewContext(stripTypeScriptTypes(snippet)+';this.load=loadSocial;',context);
  return {context,host,content,controller,fail:()=>fail=true,succeed:()=>fail=false,ready:()=>stylesReady()};
}
test('cold Social failure replaces its existing Loading placeholder with a retry',async()=>{
  const f=fixture();f.context.load();await tick();
  assert.equal(f.host.children[0].textContent,'Network failed');assert.equal(f.host.children[1].textContent,'Try again');
  f.succeed();f.host.children[1].click();await tick();assert.notEqual(f.host.children[0],f.content);
  f.ready();await tick();assert.equal(f.host.children[0],f.content);assert.equal(f.context.socialLoaded,true);
});
test('navigation during activity style preparation cannot insert late content',async()=>{
  const f=fixture();f.succeed();f.context.load();await tick();f.controller.abort();f.ready();await tick();
  assert.equal(f.host.children[0].textContent,'Loading social activity…');assert.equal(f.context.socialLoaded,false);
});


test('failed background activity refresh keeps already usable social content',async()=>{
  const f=fixture();f.succeed();f.context.load();await tick();f.ready();await tick();
  f.context.socialLoaded=false;f.fail();f.host.querySelector=selector=>selector==='.media-social-grid'?f.content:null;
  f.context.load();await tick();assert.equal(f.host.children[0],f.content);
});
