import test from 'node:test';
import assert from 'node:assert/strict';
import {quickSearchShortcut} from '../src/lib/quick-search-shortcut.ts';

const shortcut = (patch={}) => ({key:'s',ctrlKey:true,metaKey:false,shiftKey:false,altKey:false,repeat:false,isComposing:false,...patch});

test('Ctrl-S and Command-S toggle search while Ctrl-K keeps opening it',()=>{
  for (const modifiers of [{},{ctrlKey:false,metaKey:true}]) {
    assert.equal(quickSearchShortcut(shortcut(modifiers),false),'open');
    assert.equal(quickSearchShortcut(shortcut(modifiers),true),'close');
  }
  assert.equal(quickSearchShortcut(shortcut({key:'k'}),true),'open');
});

test('held keys are consumed without retoggling; other dialogs and browser shortcuts remain untouched',()=>{
  assert.equal(quickSearchShortcut(shortcut(),false,true),undefined);
  assert.equal(quickSearchShortcut(shortcut({repeat:true}),true),'handled');
  for (const patch of [{isComposing:true},{altKey:true},{shiftKey:true},{ctrlKey:false},{key:'f'}]) {
    assert.equal(quickSearchShortcut(shortcut(patch),false),undefined);
  }
});
