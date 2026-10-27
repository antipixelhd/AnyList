import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

const source=readFileSync(new URL('../src/pages/user/[username]/[section].astro',import.meta.url),'utf8');
const start=source.lastIndexOf("      document.addEventListener('anylist:editor-committed'");
const binding=source.slice(start,source.indexOf("      }, {signal:pageListeners.signal});",start)+"      }, {signal:pageListeners.signal});".length);
function fixture(existing=true) {
  let listener,refreshes=0;
  runInNewContext(stripTypeScriptTypes(binding),{
    document:{addEventListener:(_,callback)=>{listener=callback;}},
    groups:{querySelector:()=>existing?{}:null},
    pageListeners:new AbortController(),dirty:false,
    scheduleRefresh:()=>{refreshes++;},
  });
  return {commit:patch=>listener({detail:{title:{id:1},patch}}),refreshes:()=>refreshes};
}
test('progress confirmation keeps the existing row instead of refreshing its shell',()=>{
  const f=fixture(); f.commit({progress:9}); f.commit({progress:10});
  assert.equal(f.refreshes(),0);
});
test('a missing row still reconciles additions and a removed entry still refreshes',()=>{
  const f=fixture(false); f.commit({progress:1}); f.commit(null);
  assert.equal(f.refreshes(),2);
});
test('other editor changes keep the existing reconciliation path',()=>{
  const f=fixture(); f.commit({notes:'new note'}); f.commit({status:'completed',progress:9});
  assert.equal(f.refreshes(),2);
});
