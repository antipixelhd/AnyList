import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';

function fixture(ready=false){
  const nodes=Object.fromEntries(['message','retry','refresh'].map(key=>[key,{hidden:true,textContent:'',events:{},addEventListener(name,callback){this.events[name]=callback;}}]));
  const section={dataset:{gameMetadataReady:String(ready)},querySelector(selector){return nodes[selector.match(/metadata-(message|retry|refresh)/)[1]];}};
  const result={starts:0,retries:0,reloads:0,apply:null,path:null};
  const context={location:{reload(){result.reloads++;}},detailBackground(path,_signal,apply){result.path=path;result.apply=apply;return {start(){result.starts++;},retry(){result.retries++;}};}};
  const source=readFileSync(new URL('../src/lib/game-metadata.ts',import.meta.url),'utf8');
  runInNewContext(stripTypeScriptTypes(source.replace(/^import .*$/gm,'').replace(/export /g,''))+';globalThis.mount=mountGameMetadata;',context);
  context.mount({querySelector(){return section;}},42,new AbortController().signal);
  return {nodes,result};
}
test('warm primary metadata adds no status polling',()=>{assert.equal(fixture(true).result.starts,0);});
test('late metadata never reloads an active editor automatically; refresh requires explicit action',()=>{
  const {nodes,result}=fixture();assert.equal(result.starts,1);
  assert.equal(result.path,'/api/proxy/tracking/games/works/42/metadata');
  const state=result.apply({primary_ready:true,pending:true,state:'loading'});
  assert.equal(state.pending,false);assert.equal(state.state,'ready');assert.equal(result.reloads,0);
  assert.equal(nodes.refresh.hidden,false);nodes.refresh.events.click();assert.equal(result.reloads,1);
});
test('failed and pending metadata retain the page and permit bounded status retries',()=>{
  const {nodes,result}=fixture();result.apply({primary_ready:false,pending:true,error:'unavailable'});
  assert.equal(nodes.retry.hidden,false);assert.equal(nodes.refresh.hidden,true);assert.equal(result.reloads,0);
  nodes.retry.events.click();assert.equal(result.retries,1);assert.equal(result.reloads,0);
});
