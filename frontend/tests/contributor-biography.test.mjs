import assert from 'node:assert/strict';
import test from 'node:test';
import { biographyPreview } from '../src/lib/contributor-page-data.ts';

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
