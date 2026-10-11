import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';
import {countryLabel} from '../src/lib/stats-overview-data.ts';
const source=readFileSync(new URL('../src/lib/media-detail-data.ts',import.meta.url),'utf8');
const context={Intl,Date,countryLabel};
runInNewContext(stripTypeScriptTypes(source.replace(/^import .*$/gm,'').replace(/export /g,''))+';globalThis.api={localMediaId,prepareDetail,durationLabel,detailDate,detailSynopsis,progressTotal,detailFacts,relationLabel,moneyLabel};',context);
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
  assert.deepEqual(Array.from(facts,fact=>fact.label),['First published','Pages']);
});

test('synopsis previews include multiple paragraphs and retain the full expanded text',()=>{
  const short='First paragraph.\n\nA more detailed second paragraph.';
  assert.equal(api.detailSynopsis(short).lead,short);
  assert.equal(api.detailSynopsis(short).expandable,false);
  const full=Array.from({length:160},(_,index)=>`word${index}${index===59?'\n\n':''}`).join(' ');
  const result=api.detailSynopsis(full);
  assert.equal(result.lead.split(/\s+/).length,120);
  assert.equal(result.lead+result.rest,full);
  assert.equal(result.expandable,true);
  assert.equal(api.detailSynopsis(null).expandable,false);
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
