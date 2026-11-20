import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

const source=name=>stripTypeScriptTypes(readFileSync(new URL(`../src/lib/${name}.ts`,import.meta.url),'utf8')
  .replace(/^import .*\r?\n/gm,'').replaceAll('export ',''));
function fixture(t) {
  const signal=new AbortController();
  const media=Object.assign(new EventTarget(),{matches:false});
  const tweens=[];
  const api=runInNewContext(`${source('series-progress-motion')}\n${source('series-progress-picker')}\n({createProgressMotion,bindProgressRanges,syncProgressRange})`,{
    matchMedia:()=>media,Event,
    animate:(from,to,options)=>{
      const tween={from,to,options,stopped:false,finished:new Promise(()=>{}),stop(){this.stopped=true;}};
      tweens.push(tween);return tween;
    },
  });
  const motion=api.createProgressMotion(signal.signal);
  const values=new Map();
  const target={style:{setProperty:(key,value)=>values.set(key,value)},getClientRects:()=>[{}]};
  t.after(()=>signal.abort());
  return {...api,signal,media,motion,target,tweens,value:()=>parseFloat(values.get('--progress'))};
}
test('increment animation retargets from the displayed fill and resets for a new entry',t=>{
  const f=fixture(t);
  f.motion.fill(f.target,20);
  assert.equal(f.value(),20);
  f.motion.fill(f.target,30);
  f.tweens[0].options.onUpdate(24);
  f.motion.fill(f.target,40);
  assert.equal(f.tweens[0].stopped,true);
  assert.equal(f.tweens[1].from,24);
  f.motion.fill(f.target,40);
  assert.equal(f.tweens.length,2,'unchanged renders preserve the running animation');
  f.motion.reset();
  f.motion.fill(f.target,80);
  assert.equal(f.value(),80,'new entries never animate from the previous entry');
});
test('reduced motion and disposal stop animation while retaining the final progress',t=>{
  const f=fixture(t);
  f.motion.fill(f.target,10);f.motion.fill(f.target,50);
  f.media.matches=true;f.media.dispatchEvent(new Event('change'));
  assert.equal(f.value(),50);assert.equal(f.tweens[0].stopped,true);
  f.motion.fill(f.target,60);assert.equal(f.value(),60);
  f.media.matches=false;f.motion.fill(f.target,70);
  f.signal.abort();assert.equal(f.tweens[1].stopped,true);
  f.motion.fill(f.target,90);assert.equal(f.value(),60);
});
test('scrubbing follows fractional pointer positions with whole episode values and settles on release',t=>{
  const f=fixture(t),panel=new EventTarget();
  const slider=Object.assign(new EventTarget(),{min:'0',max:'10',valueAsNumber:2,disabled:false,parentElement:f.target,
    setAttribute(){},focus(){}});
  Object.assign(f.target,{querySelector:()=>slider,closest:()=>f.target,setPointerCapture(){},getBoundingClientRect:()=>({left:0,width:100})});
  slider.addEventListener('input',()=>f.syncProgressRange(slider,slider.valueAsNumber,10,10,f.motion));
  f.motion.fill(f.target,20);f.bindProgressRanges(panel,f.signal.signal,f.motion);
  const pointer=(type,x,y=0,pointerType='mouse')=>{
    const event=new Event(type);Object.defineProperties(event,Object.fromEntries(Object.entries({target:f.target,
      isPrimary:true,button:0,pointerId:1,clientX:x,clientY:y,pointerType}).map(([key,value])=>[key,{value}])));
    panel.dispatchEvent(event);
  };
  pointer('pointerdown',23);assert.equal(slider.valueAsNumber,2);assert.equal(f.value(),23);
  pointer('pointermove',26);assert.equal(slider.valueAsNumber,3);assert.equal(f.value(),26);
  pointer('pointermove',27);assert.equal(slider.valueAsNumber,3);assert.equal(f.value(),27);
  pointer('pointerup',27);assert.equal(f.tweens.at(-1).to,30);
  pointer('pointerdown',40,0,'touch');pointer('pointermove',41,20,'touch');pointer('pointerup',41,20,'touch');
  assert.equal(slider.valueAsNumber,3,'vertical scrolling never changes the episode count');
});
