import test from 'node:test';
import assert from 'node:assert/strict';
import {capitalizeSearchWords,wireSearchWordCapitalization} from '../src/lib/search-word-capitalization.ts';

test('word starts capitalize without lowercasing acronyms or touching symbols and digits',()=>{
  assert.equal(capitalizeSearchWords('the lord of the rings'),'The Lord Of The Rings');
  assert.equal(capitalizeSearchWords('NASA meets WALL-E'),'NASA Meets WALL-E');
  assert.equal(capitalizeSearchWords('élan déjà ßeta'),'Élan Déjà SSeta');
  assert.equal(capitalizeSearchWords('123 42nd !hello 😊 word'),'123 42nd !hello 😊 Word');
});

function field() {
  const events=new Map();
  return {events,value:'',selectionStart:0,selectionEnd:0,selectionDirection:'none',
    addEventListener(type,listener,options){events.set(type,{listener,options});},
    setSelectionRange(start,end,direction){this.selectionStart=start;this.selectionEnd=end;this.selectionDirection=direction;},
    dispatchEvent(event){this.dispatched=(this.dispatched||0)+1;events.get(event.type)?.listener(event);},
  };
}

test('mobile normalization preserves caret/selection and emits no duplicate search input',()=>{
  const input=field();
  wireSearchWordCapitalization(input,new AbortController().signal,()=>true);
  input.value='ßeta test';input.selectionStart=5;input.selectionEnd=9;input.selectionDirection='backward';
  input.events.get('input').listener({isComposing:false});
  assert.equal(input.value,'SSeta Test');
  assert.deepEqual([input.selectionStart,input.selectionEnd,input.selectionDirection],[6,10,'backward']);
  assert.equal(input.dispatched,undefined);
  assert.equal(input.events.get('input').options.capture,true);
});

test('desktop input and active IME composition remain untouched',()=>{
  const input=field();let mobile=false;
  wireSearchWordCapitalization(input,new AbortController().signal,()=>mobile);
  input.value='the matrix';input.selectionStart=input.selectionEnd=10;
  input.events.get('input').listener({isComposing:false});assert.equal(input.value,'the matrix');
  mobile=true;
  input.events.get('input').listener({isComposing:true});assert.equal(input.value,'the matrix');
  input.events.get('compositionend').listener();
  assert.equal(input.value,'The Matrix');assert.equal(input.dispatched,1);
});
