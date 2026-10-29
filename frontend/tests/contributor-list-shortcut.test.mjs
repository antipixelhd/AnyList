import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';

const source=readFileSync(new URL('../src/lib/contributor-page.ts',import.meta.url),'utf8');
test('list shortcut submits with the works fragment, restores the form and preserves modified navigation',()=>{
 const start=source.indexOf("  root.querySelector<HTMLAnchorElement>('.contributor-list-link')");
 const snippet=source.slice(start,source.indexOf("  root.querySelectorAll<HTMLButtonElement>('[data-contributor-view]')",start));
 let click;const submitted=[],scrolled=[],scope={checked:false},works={};
 const form={action:'https://example.test/person/1?media_type=movie',elements:{namedItem:()=>scope},requestSubmit:()=>submitted.push(form.action)};
 const context={signal:new AbortController().signal,root:{querySelector:selector=>selector==='.contributor-list-link'?{addEventListener:(_,handler)=>click=handler}:works},form,scrollToWorks:target=>scrolled.push(target)};
 vm.runInNewContext(stripTypeScriptTypes(snippet),context);
 const event=overrides=>({button:0,preventDefault(){this.prevented=true;},...overrides});
 const first=event();click(first);assert.equal(first.prevented,true);assert.equal(scope.checked,true);
 assert.deepEqual(submitted,['https://example.test/person/1?media_type=movie#contributor-works']);
 assert.equal(form.action,'https://example.test/person/1?media_type=movie');
 assert.equal(scrolled.length,0,'wait for filtered results before scrolling');
 click(event());assert.equal(submitted.length,1);assert.equal(scrolled[0],works);
 for(const modifier of ['ctrlKey','metaKey','altKey','shiftKey']){const modified=event({[modifier]:true});click(modified);assert.equal(modified.prevented,undefined);}
 assert.equal(scrolled.length,1);
});

test('explicit filtered results scroll only after the requested works render',async()=>{
 const start=source.indexOf("  form.addEventListener('submit',event=>{");
 const snippet=source.slice(start,source.indexOf('  let syncing=',start));
 let submit,resolvePage;const order=[],works={},urls=[];
 const form={action:'https://example.test/person/1#contributor-works',querySelectorAll:()=>[],addEventListener:(_,callback)=>submit=callback};
 const context={form,signal:new AbortController().signal,prediction:{cancel(){}},filterRequest:undefined,revision:0,AbortController,URL,URLSearchParams,
  sourceRefresh:{update(){},stop(){}},syncAgain:false,updateEmpty(){},pages:{get:()=>new Promise(resolve=>resolvePage=resolve)},
  error:{hidden:true},region:{setAttribute(){},removeAttribute(){},replaceChildren(){order.push('replace')}},
  FormData:class{forEach(callback){callback('in','list_scope');callback('movie','media_type')}},
  contributorParams:p=>p,requestPage:()=>new Promise(resolve=>resolvePage=resolve),root:{dataset:{endpoint:'people/1'},querySelector:s=>s==='.contributor-works'?works:s==='.contributor-list-link'?null:{}},
  items:new Map(),data:{list_count:2},params:new URLSearchParams(),append:()=>order.push('render'),more:{},
  location:{href:'https://example.test/person/1'},requestedView:'timeline',history:{state:null,replaceState:(_,__,url)=>urls.push(url)},
  scrollToWorks:target=>{assert.equal(target,works);order.push('scroll')},showError:message=>assert.fail(message)};
 vm.runInNewContext(stripTypeScriptTypes(snippet),context);
 submit({preventDefault(){}});assert.deepEqual(order,[]);
 resolvePage({works:[],roles:[],kind:'actor',has_more:false});await new Promise(setImmediate);
 assert.deepEqual(order,['replace','render','scroll']);
 assert.equal(urls[0].hash,'#contributor-works');assert.equal(urls[0].searchParams.get('list_scope'),'in');
 assert.equal(urls[0].searchParams.get('media_type'),'movie');
});
