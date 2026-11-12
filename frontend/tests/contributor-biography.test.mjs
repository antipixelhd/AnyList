import assert from 'node:assert/strict';
import test from 'node:test';
import { biographyPreview } from '../src/lib/contributor-page-data.ts';
import {mountContributorBiography,unmountContributorBiography} from '../src/lib/contributor-biography.ts';

test('biography preview stops after the first paragraph and retains the complete biography', () => {
  const text = '  First paragraph.\n\nSecond paragraph.  ';
  const result = biographyPreview(text);
  assert.equal(result.lead,'First paragraph.');
  assert.equal(result.rest,'\n\nSecond paragraph.');
  assert.equal(result.lead + result.rest,text.trim());
  assert.equal(result.expandable,true);
  assert.equal(result.shortened,false);
});

test('long single paragraphs are word-limited without losing the full text', () => {
  const full = Array.from({length:100},(_,i) => `word${i}`).join(' ');
  const result = biographyPreview(full);
  assert.equal(result.preview.split(' ').length,60);
  assert.ok(result.preview.endsWith('word59…'));
  assert.equal(result.full,full);
  assert.equal(result.lead + result.rest,full);
  assert.equal(result.expandable,true);
});

test('short and empty biographies need no expansion control', () => {
  assert.equal(biographyPreview('Short  biography.').expandable,false);
  assert.equal(biographyPreview(null).expandable,false);
});

test('expansion preserves whitespace and the exact text at the word boundary', () => {
  const full = 'One   two\tthree four.\n\nAnother paragraph.';
  const result = biographyPreview(full,3);
  assert.equal(result.lead,'One   two\tthree');
  assert.equal(result.rest,' four.\n\nAnother paragraph.');
  assert.equal(result.lead + result.rest,full);
});

function fixture(){
  const button=new EventTarget(),attributes=new Map();
  button.setAttribute=(key,value)=>attributes.set(key,value);button.getAttribute=key=>attributes.get(key);
  const text={style:{removeProperty(){}},getBoundingClientRect:()=>({height:100})};
  const rest={hidden:true},ellipsis={hidden:false};let scrolls=0;
  const nodes={'[data-biography-toggle]':button,'[data-biography-text]':text,'[data-biography-rest]':rest,'[data-biography-ellipsis]':ellipsis};
  const root={querySelector:key=>nodes[key],getBoundingClientRect:()=>({top:-1}),scrollIntoView(){scrolls++;}};
  return {root,button,rest,ellipsis,scrolls:()=>scrolls};
}

test('biography remount preserves expanded intent and removes the previous handlers',()=>{
  const previous=globalThis.matchMedia,reduced=new EventTarget();reduced.matches=true;
  globalThis.matchMedia=()=>reduced;
  const owner=new AbortController(),f=fixture();
  try{
    mountContributorBiography(f.root,owner.signal);
    f.button.dispatchEvent(new Event('click'));assert.equal(f.rest.hidden,false);
    mountContributorBiography(f.root,owner.signal,true);
    assert.equal(f.button.getAttribute('aria-expanded'),'true');assert.equal(f.button.textContent,'Read less');
    f.button.dispatchEvent(new Event('click'));
    assert.equal(f.rest.hidden,true);assert.equal(f.scrolls(),1);
    owner.abort();f.button.dispatchEvent(new Event('click'));assert.equal(f.rest.hidden,true);
  }finally{owner.abort();globalThis.matchMedia=previous;}
});

test('replacing non-expandable copy can dispose its previous biography without navigation',()=>{
  const previous=globalThis.matchMedia,reduced=new EventTarget();reduced.matches=true;
  globalThis.matchMedia=()=>reduced;
  const owner=new AbortController(),f=fixture();
  try{
    mountContributorBiography(f.root,owner.signal,true);
    unmountContributorBiography(f.root);f.button.dispatchEvent(new Event('click'));
    assert.equal(f.rest.hidden,false);assert.equal(f.scrolls(),0);
  }finally{owner.abort();globalThis.matchMedia=previous;}
});
