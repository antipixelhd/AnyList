import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

const source=readFileSync(new URL('../src/lib/profile-list.ts',import.meta.url),'utf8');
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

test('Games commits with overlapping IDs never mark a screen row as reconciled or request screen reads',()=>{
  let listener,refreshes=0,lookups=0;
  runInNewContext(stripTypeScriptTypes(binding),{
    document:{addEventListener:(_,callback)=>listener=callback},
    groups:{querySelector:()=>{lookups++;return {};}},pageListeners:new AbortController(),dirty:false,
    scheduleRefresh:()=>refreshes++,
  });
  for(const patch of [{progress:1},{status:'playing'},null])listener({detail:{title:{id:1,type:'game',subject:{kind:'game',catalogue_id:1}},patch}});
  assert.equal(lookups,0);assert.equal(refreshes,0);
});

test('Games state events cannot paint a retained screen row with the same numeric ID',()=>{
  const start=source.indexOf("      document.addEventListener('anylist:editor-state'");
  const end=source.indexOf("      document.addEventListener('anylist:editor-committed'",start);
  let listener;
  const context={document:{addEventListener:(_,callback)=>listener=callback},pageListeners:new AbortController(),revision:0,
    groups:{querySelector:()=>assert.fail('Games state must not look up a screen row')},
  };
  runInNewContext(stripTypeScriptTypes(source.slice(start,end)),context);
  listener({detail:{id:1,type:'game',subject:{kind:'game',catalogue_id:1},entry:{status:'playing',score:3}}});
  assert.equal(context.revision,0);
});
