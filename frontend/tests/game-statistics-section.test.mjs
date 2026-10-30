import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';

test('an uncached Games domain section stays pending instead of repainting the previous ready section on activation',()=>{
  const source=readFileSync(new URL('../src/lib/game-statistics.ts',import.meta.url),'utf8');
  const start=source.indexOf('  function navigate(next:typeof selection');
  const snippet=source.slice(start,source.indexOf('  function draw(',start));
  const loaded=[],cached={status:'ready',generation:2,data:['cached']};let available;
  const control={style:{},getBoundingClientRect:()=>({height:200}),hidden:false};
  const context={selection:{section:'actors',sort:'count',page:1},response:{status:'ready',generation:1,data:['old actors']},
    showPageStart:false,clearTimeout,timer:undefined,attempts:0,loader:{cancel(){},load:value=>loaded.push(value)},
    skeletonTiming:{reset(){},show(){}},node:()=>control,updateSelection(){},sections:{get:()=>available},sectionKey:()=> 'genres:count:1',render:value=>context.response=value};
  runInNewContext(stripTypeScriptTypes(snippet)+';this.navigate=navigate;',context);
  context.navigate({section:'genres',sort:'count',page:1},false);
  assert.equal(context.response.status,'pending');assert.equal(context.response.data,null);assert.equal(loaded.length,1);
  available=cached;context.navigate({section:'genres',sort:'count',page:1},false);assert.equal(context.response,cached);assert.equal(loaded.length,1);
});
