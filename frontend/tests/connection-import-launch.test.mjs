import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';
const source=readFileSync(new URL('../src/scripts/streaming-import.ts',import.meta.url),'utf8');
const launch=source.slice(source.indexOf('export function launchConnectionImport'),source.indexOf('function mountStreamingImports()')).replace('export ','');
function fixture(){
 const input={attrs:{id:'connection--1-finish-date'},getAttribute(name){return this.attrs[name]||null;},setAttribute(name,value){this.attrs[name]=value;}};
 const label={attrs:{for:'connection--1-finish-date','aria-controls':'connection--1-finish-date'},getAttribute:input.getAttribute,setAttribute:input.setAttribute};
 const app={dataset:{},querySelectorAll:()=>[input,label],getAttribute:()=>null};
 const mounted=[],added=[],form={hidden:false};let href='https://example.test/connections?import=cancelled-draft&source=stremio&connection=2&section=imports';
 const context={URL,Number,document:{querySelector:selector=>selector==='[data-connection-imports]'?{append:node=>added.push(node)}:selector==='[data-import-start-connection]'?form:{content:{firstElementChild:{cloneNode:()=>app}}}},
 location:{get href(){return href;}},history:{state:null,replaceState:(_state,_title,url)=>{href=String(url);}},mountStreamingImport:node=>mounted.push(node)};
 runInNewContext(stripTypeScriptTypes(launch)+';globalThis.launch=launchConnectionImport;',context);
 return {launch:context.launch,app,input,label,mounted,added,form,url:()=>new URL(href)};
}
test('a new connection launches a fresh importer and replaces cancelled URL identity',()=>{
 const f=fixture();assert.equal(f.launch({id:6,type:'stremio',name:'Living room',sync_watched:true}),true);
 assert.equal(f.url().searchParams.get('import'),null);
 assert.equal(f.url().searchParams.get('connection'),'6');
 assert.equal(f.url().searchParams.get('section'),null);
 assert.equal(f.app.dataset.connectionImport,'6');assert.equal(f.app.dataset.serviceName,'Living room');
 assert.equal(f.app.dataset.connectionCreated,'true');assert.equal(f.form.hidden,true);
 assert.equal(f.input.attrs.id,'connection-6-finish-date');assert.equal(f.label.attrs.for,f.input.attrs.id);
 assert.equal(f.mounted[0],f.app);assert.equal(f.added[0],f.app);
});
test('playback/library-only connections do not launch watch review',()=>{
 const f=fixture();assert.equal(f.launch({id:7,type:'nuvio',name:'TV',sync_watched:false}),false);
 assert.equal(f.mounted.length,0);assert.equal(f.form.hidden,false);
 assert.equal(f.url().searchParams.get('connection'),'2');
});
