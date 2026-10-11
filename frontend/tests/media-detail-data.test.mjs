import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';
import {biographyPreview} from '../src/lib/contributor-page-data.ts';
const source=readFileSync(new URL('../src/lib/media-detail-data.ts',import.meta.url),'utf8');
const context={biographyPreview,Intl,Date};
runInNewContext(stripTypeScriptTypes(source.replace(/^import .*$/gm,'').replace(/export /g,''))+';globalThis.api={localMediaId,prepareDetail,durationLabel,detailDate,progressTotal,detailFacts,relationLabel,moneyLabel};',context);
const {api}=context;
test('direct catalogue routes validate local IDs and reject ambiguous or oversized values',()=>{
  assert.equal(api.localMediaId('12'),12);
  for(const value of ['0','-1','12x','1e2','2147483648','01',undefined])assert.equal(api.localMediaId(value),null);
});
test('series progress counts released regular episodes, excluding specials and future totals',()=>{
  const media=api.prepareDetail({id:1,type:'series',episode_count:99,seasons:[{season_number:0,released_count:3},{season_number:1,released_count:9},{season_number:2,released_count:2}]});
  assert.equal(api.progressTotal(media),11);
  assert.equal(api.progressTotal(api.prepareDetail({type:'series',episode_count:19})),19);
  assert.equal(api.progressTotal(api.prepareDetail({type:'movie'})),1);
});
test('partial release dates remain stable and absent metadata is omitted',()=>{
  assert.equal(api.detailDate('1965'),'1965');assert.equal(api.detailDate('invalid'),'');
  assert.equal(api.durationLabel(167),'2h 47m');
  const facts=api.detailFacts(api.prepareDetail({type:'book',year:'1965',pages:896}));
  assert.deepEqual(Array.from(facts,fact=>fact.label),['Format','First published','Pages']);
});
test('cached game prices respect currency minor units and missing prices stay absent',()=>{
  assert.equal(api.moneyLabel({amountInt:799,currency:'EUR'}),'€7.99');
  assert.equal(api.moneyLabel({amountInt:799,currency:'JPY'}),'¥799');
  assert.equal(api.moneyLabel({amount:0,currency:'USD'}),'$0.00');
  for(const value of [null,{}, {currency:'EUR'}, {currency:'USD',amount:-1}, {currency:'bad',amount:1}])assert.equal(api.moneyLabel(value),null);
});
test('similar titles stay in the viewed media type while relations preserve direction',()=>{
  const media=api.prepareDetail({type:'movie',similar:[{type:'series',title:'Series'},{type:'movie',title:'Movie'}],relations:[{type:'book',title:'Source'}]});
  assert.deepEqual(Array.from(media.similar,row=>row.title),['Movie']);
  assert.equal(media.relations[0].type,'book');
  assert.equal(api.relationLabel('based_on',false),'Source');
  assert.equal(api.relationLabel('based_on',true),'Adaptation');
});
