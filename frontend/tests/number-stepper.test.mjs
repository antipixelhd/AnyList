import test from 'node:test';
import assert from 'node:assert/strict';
import {nextStepperValue} from '../src/lib/scoreStepper.ts';

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
