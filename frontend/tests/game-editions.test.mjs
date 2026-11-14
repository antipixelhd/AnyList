import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
const source=readFileSync(new URL('../src/lib/game-editions.ts',import.meta.url),'utf8').replace(/^import .*$/gm,'').replace('export function','function');
function fixture(pending=false){
 const state={starts:0,reveals:0},handlers={},controller=new AbortController();
 const links={replaceChildren(...rows){state.rows=rows;}};
 const template={content:{firstElementChild:{cloneNode(){const title={},year={},poster={hidden:true,addEventListener(){}};return {title,year,poster,querySelector:s=>s.includes('title')?title:s.includes('poster')?poster:year};}}}};
 const heading={};
 const card={hidden:true,heading,querySelector:s=>s.includes('heading')?heading:s.includes('links')?links:template};
 const root={querySelector:()=>card,addEventListener:(name,fn)=>handlers[name]=fn};
 const context={window:{requestIdleCallback(fn){state.idle=fn;return 1;},cancelIdleCallback(){state.cancelled=true;}},
 applyResponsiveArtwork(poster,url,options){poster.src=url;poster.options=options;},
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
 f.state.apply({editions:[{id:9,title:'New edition',year:2022,poster:'https://images.igdb.com/igdb/image/upload/t_thumb/cover.jpg'},{id:7,title:'same',year:2020},{id:'bad',title:'bad',year:2022}],status:{state:'ready'}});
 assert.equal(f.card.hidden,false);assert.equal(f.state.rows.length,1);
 assert.equal(f.card.heading.textContent,'New version available');
 assert.equal(f.state.rows[0].poster.hidden,false);assert.equal(f.state.rows[0].poster.options.sizes,'56px');
 assert.equal(f.state.rows[0].href,'/games/9');assert.equal(f.state.rows[0].title.textContent,'New edition');
 assert.equal(f.state.rows[0].year.textContent,'2022');assert.equal(f.state.reveals,1);
 f.state.apply({editions:[{id:9,title:'New edition',year:2022},{id:10,title:'Another edition',year:2024}],status:{state:'ready'}});
 assert.equal(f.state.rows.length,2);assert.equal(f.card.heading.textContent,'New versions available');
 f.state.apply({editions:[{id:9,title:'New edition',year:2022}],status:{state:'ready'}});
 assert.equal(f.state.rows.length,1);assert.equal(f.card.heading.textContent,'New version available');
 f.state.apply({editions:[],status:{state:'ready'}});assert.equal(f.card.hidden,true);f.controller.abort();
});
