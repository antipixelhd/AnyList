import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import {statisticsSection,statisticsPanel,statisticsSections} from '../src/lib/unified-statistics-data.ts';
const source=readFileSync(new URL('../src/lib/stats-overview.ts',import.meta.url),'utf8');
test('ready section changes commit before the entrance while ordinary overview updates retain chart motion',()=>{
  const start=source.indexOf('  function render(next:');
  const snippet=source.slice(start,source.indexOf('  function renderNow(',start));
  const actions=[];
  const context={matchesSelection:()=>true,paintedView:'screen:overview',domain:'screen',section:'genres',
    panelActive:()=>true,root:{},response:{overview:{media_type:'all'}},
    results:{},events:new AbortController(),
    settleNavigationContent:(_results,kind,_signal,offset)=>actions.push([kind,offset]),renderNow:(data,initial)=>actions.push([data,initial])};
  runInNewContext(stripTypeScriptTypes(snippet)+';this.render=render;',context);
  const ready={status:'ready',overview:{media_type:'all'}};
  context.render(ready);
  assert.deepEqual(actions.splice(0),[[ready,true],['tab',4]]);
  context.paintedView='screen:genres';context.render(ready);
  assert.deepEqual(actions.splice(0),[[ready,false]],'same-section refresh does not replay the tab transition');
  context.section='overview';context.paintedView='screen:overview';
  const movie={status:'ready',overview:{media_type:'movie'}};
  context.render(movie);
  assert.deepEqual(actions.splice(0),[[movie,false]],'overview media updates keep their original chart animation');
  context.matchesSelection=()=>false;context.render(ready);
  assert.deepEqual(actions,[],'obsolete responses cannot schedule a transition');
});

test('optimistic section controls leave the outgoing data panel intact until commit',()=>{
  const start=source.indexOf('  function updateDomainControls(');
  const snippet=source.slice(start,source.indexOf('  function paintGameExtra',start));
  const panels=[{dataset:{statsPanel:'overview'},hidden:false},{dataset:{statsPanel:'studios'},hidden:true}];
  const generic=()=>({hidden:false,textContent:'',setAttribute(){},removeAttribute(){}});
  const controls=new Map();const node=selector=>{if(!controls.has(selector))controls.set(selector,generic());return controls.get(selector);};
  const buttons=[['screen','overview'],['game','overview'],['game','publishers']].map(([domain,section])=>({dataset:{section},selected:false,closest:()=>({dataset:{statisticsDomain:domain}}),classList:{toggle(){}},setAttribute(name,value){if(name==='aria-pressed')this.selected=value==='true';}}));
  const root={dataset:{},querySelectorAll:selector=>selector==='[data-stats-panel]'?panels:selector.includes('.profile-section-selector')?buttons:[]};
  const context={root,node,domain:'game',section:'publishers',page:1,firstPage:1,appendFailed:false,statisticsPanel,statisticsSections,rawGame:()=>({has_more:false}),panelUrl:()=> 'https://example.test/user/alex/stats/games',URL,chartKeys:['scores','episode_counts','release_years','watch_years']};
  runInNewContext(stripTypeScriptTypes(snippet)+';this.update=updateDomainControls;',context);
  context.update();assert.equal(root.dataset.section,'publishers');assert.deepEqual(panels.map(p=>p.hidden),[false,true]);
  context.update(true);assert.deepEqual(panels.map(p=>p.hidden),[true,false]);
  assert.deepEqual(buttons.map(b=>b.selected),[false,false,true]);
  context.section='overview';context.update();assert.deepEqual(buttons.map(b=>b.selected),[false,true,false]);
});
test('a pending snapshot preserves usable results while exposing the existing pending status on cold entry',()=>{
  const start=source.indexOf('  function renderNow('),end=source.indexOf('    if (!ready) {',start);
  const snippet=source.slice(start,end)+'  }';
  for(const initiallyHidden of [false,true]){
    const elements=new Map();const node=selector=>{if(!elements.has(selector))elements.set(selector,{hidden:selector==='.overview-results'?initiallyHidden:true,textContent:''});return elements.get(selector);};
    const root={dataset:{}};const context={matchesSelection:()=>true,colors:{clear(){}},color(){},response:{},root,node,section:'overview',domain:'screen',timer:undefined,clearTimeout,setTimeout:()=>0,overviewPollDelay:()=>5000,skeletonTiming:{show(){},reset(){}}};
    runInNewContext(stripTypeScriptTypes(snippet)+';this.render=renderNow;',context);context.render({status:'pending',overview:null});
    assert.equal(node('.overview-results').hidden,initiallyHidden);assert.equal(node('.overview-pending').hidden,!initiallyHidden);
  }
});

test('outgoing controls stay inert across cold reads, failures and delayed commits while navigation remains available',()=>{
  const start=source.indexOf('  function statisticsBusy(');
  const snippet=source.slice(start,source.indexOf("  events.signal.addEventListener('abort',()=>statisticsBusy",start));
  let matching=false,cancelled=0;const waiting=[],progress=[];
  const context={cancelPanelReveal(){cancelled++;},appending:false,queueMicrotask:()=>{},checkMore(){},root:{setAttribute(){}},response:{status:'ready'},matchesSelection:()=>matching,setResultsWaiting:value=>waiting.push(value),panelActive:()=>true,progressRunning:false,document:{dispatchEvent:event=>progress.push(event.type)},Event};
  runInNewContext(stripTypeScriptTypes(snippet)+';this.busy=statisticsBusy;',context);
  context.busy(true);context.busy(false);assert.deepEqual(waiting,[true,true]);
  matching=true;context.busy(true);context.busy(false);assert.deepEqual(waiting,[true,true,true,false]);
  assert.deepEqual(progress,['anylist:route-progress-start','anylist:route-progress-end','anylist:route-progress-start','anylist:route-progress-end']);
  assert.equal(cancelled,2,'a new read cancels any outgoing section commit before taking ownership of inert controls');
});

test('domain switches and restored URLs update selection before reading without a loading timer',()=>{
  const selectStart=source.indexOf('  function selectSection(');
  const select=source.slice(selectStart,source.indexOf("  root.addEventListener('anylist:statistics-domain-section'",selectStart));
  const openStart=source.indexOf('  function openUrl(');
  const open=source.slice(openStart,source.indexOf("  root.addEventListener('anylist:statistics-open'",openStart));
  const reads=[],urls=[],controls=[];
  const context={domain:'screen',section:'actors',page:1,media:'series',statisticsSection,URL,
    tooltips:new Map(),pies:new Map(),statisticsPanel,cancelPanelReveal(){},render(){},response:{},root:{dataset:{}},genreSort:value=>value||'count',actorArtwork:value=>value||'media',
    node:()=>({textContent:'Actors'}),syncUrl:push=>urls.push([context.domain,context.section,push]),
    updateDomainControls:()=>controls.push(context.domain),syncLinks(){},scheduleSibling(){},loader:{load:()=>reads.push([context.domain,context.section,context.media])}};
  runInNewContext(stripTypeScriptTypes(select+open)+';this.select=selectSection;this.open=openUrl;',context);
  context.select('actors',true,'game');assert.deepEqual(reads[0],['game','actors','series']);assert.deepEqual(urls[0],['game','actors',true]);
  context.open(new URL('https://example.test/user/alex/stats?section=genres&media=movie'));
  assert.deepEqual(reads[1],['screen','genres','movie']);assert.deepEqual(controls,['game','screen']);
  context.open(new URL('https://example.test/user/alex/stats/games?section=publishers'));
  assert.deepEqual(reads[2],['game','publishers','movie']);
});
