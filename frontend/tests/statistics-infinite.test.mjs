import assert from 'node:assert/strict';
import test from 'node:test';
import {appendStatistics,gameStatisticsView} from '../src/lib/unified-statistics-data.ts';
const row=key=>({key,label:key,games:1,recorded_seconds:3600,known_time_games:1,mean_score:8,rated_games:1,href:'/games/1',image:null,roles:[],top_titles:[]});
const view=(page,keys,generation=1,section='actors',sort='count')=>gameStatisticsView({status:'ready',section,page,generation,data:keys.map(row),has_more:true},sort);
test('infinite statistics append consecutive published pages without duplicates and retain earlier card identities',()=>{
  const first=view(1,['a','b']),second=view(2,['b','c']);
  const joined=appendStatistics(first,second);
  assert.deepEqual(joined.game.data.map(r=>r.key),['a','b','c']);assert.equal(joined.game.data[0],first.game.data[0]);
  assert.equal(joined.game.page,2);assert.equal(joined.overview.actors.length,3);
  const third=appendStatistics(joined,view(3,['d']));assert.deepEqual(third.game.data.map(r=>r.key),['a','b','c','d']);
});
test('infinite statistics never mixes generations, sections, sorts or nonconsecutive pages',()=>{
  const first=view(1,['a']);
  for(const next of [view(2,['b'],2),view(2,['b'],1,'genres'),view(2,['b'],1,'actors','time'),view(3,['b'])])assert.equal(appendStatistics(first,next),next);
});

import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
const source=readFileSync(new URL('../src/lib/stats-overview.ts',import.meta.url),'utf8');
test('near-end admission starts one append, never repeats while pending, and respects inactive views',()=>{
  const start=source.indexOf('  function canLoadMore()'),snippet=source.slice(start,source.indexOf('  const moreObserver=',start));
  const loads=[];let active=true;
  const context={events:{signal:new AbortController().signal},panelActive:()=>active,root:{getAttribute:()=> 'false'},domain:'game',section:'genres',appending:false,appendFailed:false,matchesSelection:()=>true,response:{status:'ready'},rawGame:()=>({has_more:true}),node:()=>({getClientRects:()=>[{}],getBoundingClientRect:()=>({top:1000})}),innerHeight:800,page:1,media:'all',loader:{load:(...args)=>loads.push(args)}};
  runInNewContext(stripTypeScriptTypes(snippet)+';this.check=checkMore;',context);
  context.check();context.check();assert.equal(loads.length,1);assert.equal(context.page,2);
  context.appending=false;active=false;context.check();assert.equal(loads.length,1);
});

test('a pending continuation invalidates settled pages before restarting the first-page poll',()=>{
  const start=source.indexOf('    render:next=>{')+'    render:'.length;
  const callback=source.slice(start,source.indexOf('    changed:',start)).trim().replace(/,$/,'');
  const calls=[];const context={domain:'game',section:'actors',page:2,firstPage:1,appending:true,media:'all',loader:{clear:()=>calls.push('clear'),load:()=>calls.push('load')},syncUrl(){},render:()=>calls.push('render'),scheduleSibling(){}};
  runInNewContext(stripTypeScriptTypes('const handle='+callback+';this.handle=handle;'),context);
  context.handle({status:'pending',game:{status:'pending',page:2}});
  assert.deepEqual(calls,['clear','load']);assert.equal(context.page,1);assert.equal(context.firstPage,1);assert.equal(context.appending,false);
});
