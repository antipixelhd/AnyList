import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';

function fixture(ready=false,pending=false,revision=null){
  const nodes=Object.fromEntries(['message','retry','refresh'].map(key=>[key,{hidden:true,textContent:'',events:{},addEventListener(name,callback){this.events[name]=callback;}}]));
  const section={dataset:{gameMetadataReady:String(ready),gameMetadataPending:String(pending),gameMetadataRevision:revision||''},querySelector(selector){return nodes[selector.match(/metadata-(message|retry|refresh)/)[1]];}};
  const result={starts:0,retries:0,reloads:0,publications:0,apply:null,path:null,requests:[],valid:true};
  const context={fetch:async path=>{result.requests.push(path);return Response.json({id:42,type:'game'});},
    publishGamePrimary(){result.publications++;return result.valid;},
    location:{reload(){result.reloads++;}},detailBackground(path,_signal,apply){result.path=path;result.apply=apply;return {start(){result.starts++;},retry(){result.retries++;}};}};
  const source=readFileSync(new URL('../src/lib/game-metadata.ts',import.meta.url),'utf8');
  runInNewContext(stripTypeScriptTypes(source.replace(/^import .*$/gm,'').replace(/export /g,''))+';globalThis.mount=mountGameMetadata;',context);
  context.mount({querySelector(){return section;}},42,new AbortController().signal);
  return {nodes,result};
}
test('warm primary metadata adds no status polling',()=>{assert.equal(fixture(true).result.starts,0);});
test('late metadata publishes public primary data in place without a route reload',async()=>{
  const {nodes,result}=fixture();assert.equal(result.starts,1);
  assert.equal(result.path,'/api/proxy/tracking/games/works/42/metadata');
  const state=await result.apply({primary_ready:true,pending:true,state:'loading'});
  assert.equal(state.pending,false);assert.equal(state.state,'ready');assert.equal(result.reloads,0);
  assert.equal(result.publications,1);assert.deepEqual(result.requests,['/api/proxy/tracking/games/works/42/primary']);
});
test('failed and pending metadata retain the page and permit bounded status retries',async()=>{
  const {nodes,result}=fixture();await result.apply({primary_ready:false,pending:true,error:'unavailable'});
  assert.equal(nodes.retry.hidden,false);assert.equal(nodes.refresh.hidden,true);assert.equal(result.reloads,0);
  nodes.retry.events.click();assert.equal(result.retries,1);assert.equal(result.reloads,0);
});

test('a mismatched primary publication fails for retry without a navigation',async()=>{
  const {result}=fixture();result.valid=false;
  await assert.rejects(result.apply({primary_ready:true}),/identity changed/);
  assert.equal(result.reloads,0);
});

test('a warm refresh waits for changed public evidence while retaining its usable content',async()=>{
  const {result}=fixture(true,true,'old');assert.equal(result.starts,1);
  const pending=await result.apply({primary_ready:true,primary_revision:'old',pending:true,state:'loading'});
  assert.equal(pending.pending,true);assert.equal(result.requests.length,0);
  const ready=await result.apply({primary_ready:true,primary_revision:'new',pending:true,state:'loading'});
  assert.equal(ready.pending,false);assert.equal(result.publications,1);assert.equal(result.reloads,0);
});
