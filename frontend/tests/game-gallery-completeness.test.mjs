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

test('a refreshed canonical game gallery removes stale logos instead of merging them back',()=>{
  let publish;
  const info={type:'game',id:194},galleries={posters:[],artwork:['scene','logo']};
  const scope={info,galleries,signal:{aborted:false},artworkLimit:Infinity,previewInfo:{image:'logo'},
    uniqueDetailImages:rows=>rows,applyImages:data=>{galleries.artwork=data.artwork;},
    root:{addEventListener:(_name,callback)=>{publish=callback;},querySelector:()=>null}};
  const start=source.indexOf("root.addEventListener('anylist:game-primary'");
  const end=source.indexOf('  const resource=',start);
  runInNewContext(stripTypeScriptTypes(source.slice(start,end)),scope);
  publish({detail:{id:194,type:'game',artwork:['scene'],artwork_preview:{image:'scene'}}});
  assert.deepEqual(galleries.artwork,['scene']);
  publish({detail:{id:194,type:'game',artwork:[]}});
  assert.deepEqual(galleries.artwork,[]);
  assert.equal(scope.previewInfo,undefined);
});
