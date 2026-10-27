import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import {artworkIdentity} from '../src/lib/responsive-artwork.ts';
const source=readFileSync(new URL('../src/lib/media-detail-images.ts',import.meta.url),'utf8');
const context={imageKey:artworkIdentity};
runInNewContext(stripTypeScriptTypes(source.slice(source.indexOf('export function uniqueDetailImages'),source.indexOf('export function mountDetailImages')).replace('export ',''))+';globalThis.unique=uniqueDetailImages;',context);
test('Games retain complete canonical galleries while other consumers retain their display budget',()=>{
  const rows=Array.from({length:23},(_,i)=>`https://images.igdb.com/igdb/image/upload/t_thumb/sc${i}.jpg`);
  assert.equal(context.unique(rows).length,10);
  assert.deepEqual(Array.from(context.unique([...rows,rows[0].replace('t_thumb','t_1080p'),'javascript:alert(1)'],Infinity)),rows);
});
