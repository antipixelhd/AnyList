import test from 'node:test';
import assert from 'node:assert/strict';
import {nextStepperValue, setupScoreSteppers} from '../src/lib/scoreStepper.ts';
import {editorProgressMaximum} from '../src/lib/editor-progress.ts';

test('editor progress uses known list and detail counts and preserves unknown maxima', () => {
  assert.equal(editorProgressMaximum({type:'series',entry:{released_episodes:24}}),24);
  assert.equal(editorProgressMaximum({type:'series',released_episodes:0,entry:null}),0);
  const seasons=[{season_number:0,released_count:5},{season_number:1,released_count:12},{season_number:2,released_count:3}];
  assert.equal(editorProgressMaximum({type:'series',seasons}),15);
  assert.equal(editorProgressMaximum({type:'series',seasons:[...seasons,{season_number:3,released_count:null}]}),null);
  assert.equal(editorProgressMaximum({type:'series',entry:null}),null);
  assert.equal(editorProgressMaximum({type:'movie',released_episodes:1}),null);
});

test('typed and pasted values respect the current maximum without changing empty or valid values', () => {
  const input = Object.assign(new EventTarget(), {value:'', max:'10', disabled:false, readOnly:false});
  Object.defineProperty(input, 'valueAsNumber', {get:()=>input.value === '' ? NaN : Number(input.value)});
  const stepper = {querySelector:()=>input, querySelectorAll:()=>[]};
  setupScoreSteppers({querySelectorAll:()=>[stepper]});
  for (const [value, expected] of [['11','10'], ['99','10'], ['8.1','8.1'], ['','']]) {
    input.value=value; input.dispatchEvent(new Event('input')); assert.equal(input.value,expected);
  }
  input.max='24'; input.value='25'; input.dispatchEvent(new Event('input')); assert.equal(input.value,'24');
  input.max='0'; input.value='1'; input.dispatchEvent(new Event('change')); assert.equal(input.value,'0');
  input.max=''; input.value='25'; input.dispatchEvent(new Event('input')); assert.equal(input.value,'25');
});

test('counts use whole steps and preserve their bounds', () => {
  assert.equal(nextStepperValue(5,1,1,0,Infinity),6);
  assert.equal(nextStepperValue(5,-1,1,0,Infinity),4);
  assert.equal(nextStepperValue(0,-1,1,0,10000),0);
  assert.equal(nextStepperValue(10000,1,1,0,10000),10000);
  assert.equal(nextStepperValue(NaN,1,1,0,Infinity),1);
});

test('score controls still choose the next half point from typed tenths', () => {
  assert.equal(nextStepperValue(8.1,1,.5,0,10),8.5);
  assert.equal(nextStepperValue(8.1,-1,.5,0,10),8);
  assert.equal(nextStepperValue(8.5,1,.5,0,10),9);
  assert.equal(nextStepperValue(8.5,-1,.5,0,10),8);
  assert.equal(nextStepperValue(10,1,.5,0,10),10);
});
