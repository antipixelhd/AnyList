import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import {createMinimumDisplay} from '../src/lib/minimum-display.ts';

test('a scope change cancels a ready response waiting behind the minimum skeleton duration',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const source=readFileSync(new URL('../src/lib/stats-overview.ts',import.meta.url),'utf8');
  const renderStart=source.indexOf('  function render(next:OverviewResponse');
  const render=source.slice(renderStart,source.indexOf('  function renderNow',renderStart));
  const navigateStart=source.indexOf('  function navigate(next: MediaScope');
  const navigate=source.slice(navigateStart,source.indexOf("  root.addEventListener('click'",navigateStart));
  const signal=new AbortController().signal,skeletonTiming=createMinimumDisplay(signal,350,()=>0),painted=[],loads=[];
  const context={matchesSelection:()=>true,skeletonTiming,renderNow:data=>painted.push(data.media),node:()=>({hidden:false}),panelActive:()=>true,root:{querySelectorAll:()=>[]},
    tooltips:new Map(),pies:new Map(),media:'all',timer:undefined,clearTimeout,loader:{load:media=>loads.push(media)}};
  skeletonTiming.show();runInNewContext(stripTypeScriptTypes(render+navigate)+';this.render=render;this.navigate=navigate;',context);
  context.render({status:'ready',media:'all'});context.navigate('movie',false);t.mock.timers.tick(350);
  assert.deepEqual(painted,[]);assert.deepEqual(loads,['movie']);assert.equal(context.media,'movie');
  context.render({status:'ready',media:'movie'});t.mock.timers.tick(350);assert.deepEqual(painted,['movie']);
});
