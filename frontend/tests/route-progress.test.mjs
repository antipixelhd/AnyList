import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';

test('a new list load cannot be hidden by the previous page-progress finish timer',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const source=readFileSync(new URL('../src/components/NavigationRuntime.astro',import.meta.url),'utf8');
  const start=source.indexOf('  function startProgress()');
  const snippet=source.slice(start,source.indexOf('  if (!state.initialized)',start));
  const classes=new Set(),context={state:{},window:{setTimeout,clearTimeout},document:{querySelector:()=>({classList:{add:(...names)=>names.forEach(name=>classes.add(name)),remove:(...names)=>names.forEach(name=>classes.delete(name))}})}};
  runInNewContext(stripTypeScriptTypes(snippet)+';this.start=startProgress;this.finish=finishProgress;',context);
  context.start();t.mock.timers.tick(150);assert(classes.has('visible'));context.finish();t.mock.timers.tick(10);context.start();
  t.mock.timers.tick(170);assert(classes.has('visible'));assert(!classes.has('finishing'));
  context.finish();t.mock.timers.tick(170);assert(!classes.has('visible'));
});
