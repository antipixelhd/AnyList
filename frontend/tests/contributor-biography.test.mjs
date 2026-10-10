import assert from 'node:assert/strict';
import test from 'node:test';
import { biographyPreview } from '../src/lib/contributor-page-data.ts';

test('biography preview stops after the first paragraph and retains the complete biography', () => {
  const text = '  First paragraph.\n\nSecond paragraph.  ';
  assert.deepEqual(biographyPreview(text),{preview:'First paragraph.',full:text.trim(),expandable:true});
});

test('long single paragraphs are word-limited without losing the full text', () => {
  const full = Array.from({length:100},(_,i) => `word${i}`).join(' ');
  const result = biographyPreview(full);
  assert.equal(result.preview.split(' ').length,60);
  assert.ok(result.preview.endsWith('word59…'));
  assert.equal(result.full,full);
  assert.equal(result.expandable,true);
});

test('short and empty biographies need no expansion control', () => {
  assert.deepEqual(biographyPreview('Short  biography.'),{preview:'Short biography.',full:'Short  biography.',expandable:false});
  assert.deepEqual(biographyPreview(null),{preview:'',full:'',expandable:false});
});
