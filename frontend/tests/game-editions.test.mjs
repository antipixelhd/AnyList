import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
const source=readFileSync(new URL('../src/lib/game-editions.ts',import.meta.url),'utf8').replace(/^import .*$/gm,'').replace('export function','function');
function fixture(pending=false){
 const state={starts:0,reveals:0},handlers={},controller=new AbortController();
 const links={replaceChildren(...rows){state.rows=rows;}};
 const template={content:{firstElementChild:{cloneNode(){const title={},year={};return {title,year,querySelector:s=>s.includes('title')?title:year};}}}};
 const card={hidden:true,querySelector:s=>s.includes('links')?links:template};
 const root={querySelector:()=>card,addEventListener:(name,fn)=>handlers[name]=fn};
 const context={window:{requestIdleCallback(fn){state.idle=fn;return 1;},cancelIdleCallback(){state.cancelled=true;}},
 detailBackground(_url,_signal,apply){state.apply=apply;return {start(){state.starts++;}};},reveal(){state.reveals++;},setTimeout,clearTimeout};
 runInNewContext(stripTypeScriptTypes(source)+';globalThis.mount=mountGameEditions',context);
 context.mount(root,7,controller.signal,pending);
 return {state,handlers,controller,card};
}
test('edition work waits for primary content and idle time, and navigation cancels admission',()=>{
 const f=fixture(true);assert.equal(f.state.idle,undefined);assert.equal(f.state.starts,0);
 f.handlers['anylist:game-primary']();assert.equal(f.state.starts,0);f.state.idle();assert.equal(f.state.starts,1);
 f.controller.abort();assert.equal(f.state.cancelled,true);
});
test('warm primary schedules optional work without blocking and renders local links safely',()=>{
 const f=fixture();assert.equal(f.state.starts,0);f.state.idle();
 f.state.apply({editions:[{id:9,title:'New edition',year:2022},{id:7,title:'same',year:2020},{id:'bad',title:'bad',year:2022}],status:{state:'ready'}});
 assert.equal(f.card.hidden,false);assert.equal(f.state.rows.length,1);
 assert.equal(f.state.rows[0].href,'/games/9');assert.equal(f.state.rows[0].title.textContent,'New edition');
 assert.equal(f.state.rows[0].year.textContent,'2022');assert.equal(f.state.reveals,1);
 f.state.apply({editions:[],status:{state:'ready'}});assert.equal(f.card.hidden,true);f.controller.abort();
});
