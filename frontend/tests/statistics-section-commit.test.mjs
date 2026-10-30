import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import {statisticsPanel,statisticsSections} from '../src/lib/unified-statistics-data.ts';
const source=readFileSync(new URL('../src/lib/stats-overview.ts',import.meta.url),'utf8');
test('optimistic section controls leave the outgoing data panel intact until commit',()=>{
  const start=source.indexOf('  function updateDomainControls(');
  const snippet=source.slice(start,source.indexOf('  function paintGameExtra',start));
  const panels=[{dataset:{statsPanel:'overview'},hidden:false},{dataset:{statsPanel:'studios'},hidden:true}];
  const generic=()=>({hidden:false,textContent:'',setAttribute(){},removeAttribute(){}});
  const controls=new Map();const node=selector=>{if(!controls.has(selector))controls.set(selector,generic());return controls.get(selector);};
  const root={dataset:{},querySelectorAll:selector=>selector==='[data-stats-panel]'?panels:[]};
  const context={root,node,domain:'game',section:'publishers',page:1,firstPage:1,appendFailed:false,statisticsPanel,statisticsSections,rawGame:()=>({has_more:false}),panelUrl:()=> 'https://example.test/user/alex/stats/games',URL,chartKeys:['scores','episode_counts','release_years','watch_years']};
  runInNewContext(stripTypeScriptTypes(snippet)+';this.update=updateDomainControls;',context);
  context.update();assert.equal(root.dataset.section,'publishers');assert.deepEqual(panels.map(p=>p.hidden),[false,true]);
  context.update(true);assert.deepEqual(panels.map(p=>p.hidden),[true,false]);
});
test('a pending snapshot preserves usable results while keeping the initial headline skeleton for cold entry',()=>{
  const start=source.indexOf('  function renderNow('),end=source.indexOf('    if (!ready) {',start);
  const snippet=source.slice(start,end)+'  }';
  for(const initiallyHidden of [false,true]){
    const elements=new Map();const node=selector=>{if(!elements.has(selector))elements.set(selector,{hidden:selector==='.overview-results'?initiallyHidden:true,textContent:''});return elements.get(selector);};
    const root={dataset:{}};const context={matchesSelection:()=>true,colors:{clear(){}},color(){},response:{},root,node,section:'overview',domain:'screen',timer:undefined,clearTimeout,setTimeout:()=>0,overviewPollDelay:()=>5000,skeletonTiming:{show(){},reset(){}}};
    runInNewContext(stripTypeScriptTypes(snippet)+';this.render=renderNow;',context);context.render({status:'pending',overview:null});
    assert.equal(node('.overview-results').hidden,initiallyHidden);assert.equal(node('.overview-pending').hidden,!initiallyHidden);assert.equal(node('[data-stats-headline-loading]').hidden,!initiallyHidden);
  }
});

test('outgoing controls stay inert across cold reads, failures and delayed commits while navigation remains available',()=>{
  const start=source.indexOf('  function statisticsBusy(');
  const snippet=source.slice(start,source.indexOf("  events.signal.addEventListener('abort',()=>statisticsBusy",start));
  let matching=false;const waiting=[],progress=[];
  const context={appending:false,queueMicrotask:()=>{},checkMore(){},root:{setAttribute(){}},response:{status:'ready'},matchesSelection:()=>matching,setResultsWaiting:value=>waiting.push(value),panelActive:()=>true,progressRunning:false,document:{dispatchEvent:event=>progress.push(event.type)},Event};
  runInNewContext(stripTypeScriptTypes(snippet)+';this.busy=statisticsBusy;',context);
  context.busy(true);context.busy(false);assert.deepEqual(waiting,[true,true]);
  matching=true;context.busy(true);context.busy(false);assert.deepEqual(waiting,[true,true,true,false]);
  assert.deepEqual(progress,['anylist:route-progress-start','anylist:route-progress-end','anylist:route-progress-start','anylist:route-progress-end']);
});
