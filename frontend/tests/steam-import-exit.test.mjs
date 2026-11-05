import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
const source=readFileSync(new URL('../src/scripts/steam-settings.ts',import.meta.url),'utf8');
function fixture(){
 const nodes=new Map(),buttons=[{disabled:false},{disabled:false}],closed=[],removed=[];
 const get=name=>{if(!nodes.has(name))nodes.set(name,{hidden:false,textContent:'',querySelector:()=>null});return nodes.get(name);};
 const context={active:{id:42,generation:3},importing:false,leaving:false,saveTimer:null,timer:null,reviewEpoch:0,loadVersion:0,paused:false,reviewGames:null,writes:Promise.resolve(),signal:{aborted:false},
  exitDialog:{querySelectorAll:()=>buttons},dialog:{},get,clearTimeout,save:()=>{},fetch:async()=>({ok:true}),
  sessionStorage:{removeItem:key=>removed.push(key)},closeDialog:async value=>{closed.push(value);},list:async()=>{}};
 const code=source.slice(source.indexOf('  async function leave('),source.indexOf("  listen(get('abort')"));
 runInNewContext(stripTypeScriptTypes(code),context);
 return {context,get,buttons,closed,removed};
}
test('failed Save keeps the import open and permits retry',async()=>{
 const {context,get,buttons,closed}=fixture();
 context.save=()=>{context.writes=Promise.reject(new Error('Save failed'));};
 await context.leave(false);
 assert.equal(closed.length,0);assert.equal(context.active.id,42);
 assert.equal(get('exit-error').hidden,false);assert.equal(context.leaving,false);
 assert(buttons.every(b=>!b.disabled));
 context.save=()=>{context.writes=Promise.resolve();};await context.leave(false);
 assert.equal(closed.length,2);assert.equal(context.active.id,42);
});
test('Abort removes only the active connection after pending draft writes settle',async()=>{
 const {context,get,closed,removed}=fixture();let target;
 context.fetch=async url=>{target=url;return {ok:true};};
 await context.leave(true);
 assert.equal(target,'/api/proxy/auth/connections/42');assert.equal(context.active,undefined);
 assert.deepEqual(removed,['steam-import:42:3']);assert.equal(closed.length,2);
 assert.equal(get('resume').hidden,true);
});

for(const abort of [false,true])test(`${abort?'Abort':'Save'} ignores an already pending library response`,async()=>{
 const {context,closed}=fixture();let finish;
 context.request=()=>new Promise(resolve=>{finish=resolve;});
 const code=source.slice(source.indexOf('  async function load(id:'),source.indexOf('  async function list()'));
 runInNewContext(stripTypeScriptTypes(code),context);
 const pending=context.load(42);
 await context.leave(abort);
 finish({id:42,generation:3,imported:false,library:[],job:{status:'running'}});
 await pending;
 assert.equal(context.paused,true);assert.equal(closed.length,2);
 assert.equal(context.active?.id,abort?undefined:42);
});
test('Save rejects an invalid displayed date without closing the prompt',async()=>{
 const {context,get,closed}=fixture();context.reviewGames=[{app_id:'20'}];get('details').querySelector=()=>({});
 await context.leave(false);
 assert.equal(closed.length,0);assert.equal(get('exit-error').hidden,false);
 assert.equal(context.paused,false);
});
