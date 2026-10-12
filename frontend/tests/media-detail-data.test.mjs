import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';
import {formatDate} from '../src/lib/date-time.ts';
import {countryLabel} from '../src/lib/stats-overview-data.ts';
const source=readFileSync(new URL('../src/lib/media-detail-data.ts',import.meta.url),'utf8');
const context={Intl,Date,countryLabel,formatDate};
runInNewContext(stripTypeScriptTypes(source.replace(/^import .*$/gm,'').replace(/export /g,''))+';globalThis.api={localMediaId,prepareDetail,detailCredits,detailOrganizationFacts,durationLabel,detailDate,detailSynopsis,progressTotal,detailFacts,relationLabel,moneyLabel,detailWorkCaption,detailCastPresentation};',context);
const {api}=context;

test('cast mode reverses name hierarchy and preserves actor portrait as the final fallback',()=>{
  const actor={name:'Actor',kind:'actor',characters:['Character'],roles:['Actor'],image:'portrait',character_image:'local',character_images:['local','other']};
  const character=api.detailCastPresentation(actor,'characters');
  assert.equal(character.primary,'Character');assert.equal(character.secondary,'Actor');
  assert.deepEqual(Array.from(character.images),['local','other','portrait']);
  const person=api.detailCastPresentation(actor,'actors');
  assert.equal(person.primary,'Actor');assert.equal(person.secondary,'Character');assert.deepEqual(Array.from(person.images),['portrait']);
  const missing=api.detailCastPresentation({...actor,characters:[],character_image:null,character_images:[]},'characters');
  assert.equal(missing.primary,'Actor');assert.equal(missing.secondary,'');assert.deepEqual(Array.from(missing.images),['portrait']);
});

test('similar captions use the first available genre and year with clean missing-data fallbacks',()=>{
  assert.equal(api.detailWorkCaption({type:'movie',genres:['Science Fiction','Action'],year:'2020'},true),'Science Fiction · 2020');
  assert.equal(api.detailWorkCaption({type:'game',genres:['',' Adventure '],year:'2019'},true),'Adventure · 2019');
  assert.equal(api.detailWorkCaption({type:'book',year:'1965'},true),'1965');
  assert.equal(api.detailWorkCaption({type:'series',genres:['Drama']},true),'Drama');
  assert.equal(api.detailWorkCaption({type:'movie',genres:['Drama'],year:'2020'}),'Movie · 2020');
});

test('credits prioritize cast billing, responsible crew and separate production companies',()=>{
  const person=(id,kind,roles,position)=>({id,kind,name:`Person ${id}`,roles,position,characters:[]});
  const media=api.prepareDetail({type:'series',people:[person(1,'actor',['Actor'],5),person(2,'actor',['Actor'],0),person(3,'staff',['Costume Assistant'],1),person(4,'staff',['Director'],9),person(5,'staff',['Writer'],4)],organizations:[person(6,'studio',['Network'],0),person(7,'studio',['Production Company'],0)]});
  const credits=api.detailCredits(media);
  assert.deepEqual(Array.from(credits.preview,p=>p.id),[2,1]);
  assert.deepEqual(Array.from(credits.principal,p=>p.id),[4,5]);
  assert.deepEqual(Array.from(credits.supporting,p=>p.id),[3]);
  assert.deepEqual(Array.from(credits.studios,p=>p.id),[7]);
  media.people=media.people.filter(p=>p.kind!=='actor');
  assert.deepEqual(Array.from(api.detailCredits(media).preview,p=>p.id),[4,5,3]);
});

test('information groups roles once and prefers primary networks over alternate outlets',()=>{
  const organization=(id,name,roles)=>({id,kind:'studio',name,roles,characters:[],url:null});
  const media=api.prepareDetail({type:'series',networks:[{name:'Apple TV+'}],organizations:[organization(1,'YouTube',['Network']),organization(2,'Apple TV+',['Network']),organization(3,'Apple Studios',['Studio']),organization(4,'Apple Studios',['Production Company']),organization(5,'Rental store',['Distributor'])]});
  const groups=api.detailOrganizationFacts(media);
  assert.deepEqual(Array.from(groups,g=>g.label),['Network','Studios']);
  assert.deepEqual(Array.from(groups.flatMap(g=>g.rows),p=>p.name),['Apple TV+','Apple Studios']);
});
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
