import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';
import {profilePanelHeader} from '../src/lib/profile-panel-request.ts';

const source=readFileSync(new URL('../src/lib/profile-shell.ts',import.meta.url),'utf8');
const select=source.slice(source.indexOf('  function select('),source.indexOf('  const target='));
const readPage=source.slice(source.indexOf('  async function readPage('),source.indexOf('  async function read('));
const tabs=['overview','movies','series','games','favorites','social','stats','stats/games','library'];
const settle=()=>new Promise(setImmediate);

function fixture(owner=false){
  const changes=[],requests=[],waiting=[],redirects=[],events=[];
  const root=()=>({dataset:{},dispatchEvent(){},querySelector(){return null;},prepend(){}});
  const outgoing={root:root(),ready:true,kind:'overview',summary:'saved'};
  const panels=new Map(tabs.map(key=>[key,{root:root(),ready:true,kind:key,title:key,checkedAt:Date.now()}]));
  panels.set('source',outgoing);
  const frame=root();
  const context={URL,Date,Event,CustomEvent,AbortSignal,AbortController,owner,viewer:'viewer',selection:0,refreshFlight:undefined,
    signal:new AbortController().signal,active:'source',wanted:'source',shown:outgoing.root,panels,progressRunning:false,
    host:{querySelector(){return null;}},nav:{setAttribute(){},removeAttribute(){},getBoundingClientRect(){return {top:0};}},
    isProfileList:key=>['movies','series','list','games'].includes(key),isProfileSummary:key=>['overview','favorites','social','library'].includes(key),
    profileLoadingFrame:()=>frame,setPanelWaiting:(node,on)=>waiting.push([node,on]),stopProgress(){},update(){},pruneLists(){},forget(){},scheduleRefresh(){},queueWarm(){},profileRefreshAge:60000,summaryRevision:0,
    location:{href:'https://example.test/user/other/movies',assign:url=>redirects.push(url)},window:{scrollTo(){}},scrollY:0,history:{state:{},replaceState(){}},visibilityPath:'/api/proxy/tracking/people/other/header',
    document:{body:{dataset:{cacheUser:'viewer'},toggleAttribute(){}},dispatchEvent:event=>events.push(event.type),title:'',
      createElement:()=>({dataset:{},setAttribute(){},addEventListener(){},append(){}})},
    fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve:(status=200,redirected=false)=>resolve({status,ok:status===200,redirected}),reject})),
    show:node=>{context.shown=node;changes.push(node);},ProfileAccessChanged:class extends Error{},
  };
  runInNewContext(stripTypeScriptTypes(select)+';this.select=select;',context);
  return {context,outgoing,frame,changes,requests,waiting,redirects,events,
    switchTab:key=>context.select(key,`https://example.test/user/other/${key}`,false,true)};
}

for(const tab of tabs)test(`a ready visitor ${tab} tab opens immediately without another access request`,()=>{
  const f=fixture();f.switchTab(tab);
  assert.equal(f.context.shown,f.context.panels.get(tab).root);
  assert.deepEqual(f.changes,[f.context.panels.get(tab).root]);
  assert.equal(f.events.includes('anylist:route-progress-start'),false);
  assert.equal(f.requests.length,0);
  assert.deepEqual(f.waiting,[[f.outgoing.root,true],[f.outgoing.root,false]]);
});

for(const [status,redirected] of [[401,false],[403,false],[404,false],[200,true]])test(`fresh tab reads still reject access response ${status}, redirected=${redirected}`,async()=>{
  const ProfileAccessChanged=class extends Error{};
  const context={AbortSignal,signal:new AbortController().signal,ProfileAccessChanged,profilePanelHeader,
    fetch:async()=>({status,ok:status===200,redirected})};
  runInNewContext(stripTypeScriptTypes(readPage)+';this.readPage=readPage;',context);
  await assert.rejects(context.readPage('/user/other/movies',context.signal),ProfileAccessChanged);
});

test('a tab still being prepared cannot replace a newer ready selection',async()=>{
  const f=fixture();let finish;
  const first=f.context.panels.get('movies');first.preparing=new Promise(resolve=>finish=resolve);
  f.context.prepare=panel=>panel.preparing;
  f.switchTab('movies');assert.equal(f.context.shown,f.frame);
  f.switchTab('series');
  const latest=f.context.panels.get('series').root;
  assert.equal(f.context.shown,latest);
  finish();await settle();
  assert.equal(f.context.shown,latest);assert.deepEqual(f.changes,[f.frame,latest]);
});

test('ready owner tabs still switch immediately without a visibility request',()=>{
  const f=fixture(true);f.switchTab('movies');
  assert.equal(f.context.shown,f.context.panels.get('movies').root);
  assert.equal(f.requests.length,0);assert.equal(f.changes.includes(f.frame),false);
});
