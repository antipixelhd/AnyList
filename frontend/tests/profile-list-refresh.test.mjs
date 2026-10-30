import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';

test('failed private-list reconciliation preserves the mounted page and exposes an inline retry',async()=>{
  const source=readFileSync(new URL('../src/lib/profile-list.ts',import.meta.url),'utf8');
  const start=source.indexOf('      const scheduleRefresh = () => {');
  const snippet=source.slice(start,source.indexOf("      panel.addEventListener('anylist:panel-active'",start));
  const alerts=[];let reloads=0;
  const content={prepend:node=>alerts.push(node)};
  const context={dirty:true,refreshScheduled:false,revision:0,panelActive:()=>true,editorStore:{hasPendingWrites:()=>false},pageListeners:new AbortController(),
    panel:{querySelector:selector=>selector==='.list-content'?content:null},panelUrl:()=> 'https://example.test/user/alex/movies',
    document:{querySelector:()=>null,activeElement:null,createElement:()=>({dataset:{},children:[],setAttribute(){},append(...nodes){this.children.push(...nodes);},addEventListener(name,fn){this[name]=fn;}})},
    HTMLInputElement:class{},fetch:async()=>{throw new Error('offline');},location:{reload:()=>reloads++},Error,
  };
  runInNewContext(stripTypeScriptTypes(snippet)+';this.refresh=scheduleRefresh;',context);
  context.refresh();await new Promise(setImmediate);
  assert.equal(reloads,0);assert.equal(alerts.length,1);assert.equal(alerts[0].children[1].textContent,'Retry');assert.equal(context.refreshScheduled,false);
});
