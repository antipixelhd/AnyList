import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';
import {formatDate,localToday} from '../src/lib/date-time.ts';
import {countryLabel} from '../src/lib/stats-overview-data.ts';
const source=readFileSync(new URL('../src/lib/media-detail-data.ts',import.meta.url),'utf8');
const context={Intl,Date,URLSearchParams,countryLabel,formatDate,localToday};
runInNewContext(stripTypeScriptTypes(source.replace(/^import .*$/gm,'').replace(/export /g,''))+';globalThis.api={localMediaId,prepareDetail,detailCredits,detailCreditEpisodes,detailCreditText,detailCreditDescription,detailOrganizationFacts,detailInformationRows,durationLabel,detailDate,detailSynopsis,progressTotal,detailFacts,relationLabel,moneyLabel,detailWorkCaption,detailCastPresentation,detailBrowseHref,detailCreators};',context);
const {api}=context;

test('Games overview collapses at the first paragraph without truncating or losing stored text',()=>{
  for(const separator of ['\n\n','\r\n\r\n','\n \n']){
    const lead='First paragraph. '+Array(150).fill('word').join(' '),full=lead+separator+'Second paragraph.\n\nThird.';
    const result=api.detailSynopsis(full,true);
    assert.equal(result.lead,lead);assert.equal(result.lead+result.rest,full);
    assert.equal(result.expandable,true);assert.equal(result.shortened,false);
  }
  for(const full of ['','Short synopsis.','One paragraph\nwith a line break.']){
    const result=api.detailSynopsis(full,true);
    assert.equal(result.lead,full);assert.equal(result.rest,'');assert.equal(result.expandable,false);
  }
});

test('full game crew jobs keep expansion qualifiers, while screen and character subtitles retain existing presentation',()=>{
  const person={name:'Composer',characters:[],roles:['Music department'],jobs:['composer: main game','composer: Blood and Wine expansion']};
  assert.equal(api.detailCreditText(person),'composer: main game · composer: Blood and Wine expansion');
  assert.equal(api.detailCreditText({...person,jobs:undefined}),'Music department');
  assert.equal(api.detailCreditText({...person,characters:['Character']}),'Character');
  const text=api.detailCreditDescription({...person,credit_performances:[{characters:['English role'],languages:['en'],attributes:[{text:'English version'},{text:'voice'}],credited_as:'Alias',uncredited:null},
    {characters:['Other role'],languages:[],attributes:[null,{text:null}],uncredited:true}]});
  assert.equal(text,'English role — English version, voice, as Alias\nOther role — uncredited');
});

test('verified composer jobs remain prominent inside broader music departments without changing their qualified text',()=>{
  const person=(id,name,role,position,jobs=[])=>({id,kind:'staff',name,roles:[role],role_codes:[role],position,jobs,characters:[]});
  const media=api.prepareDetail({type:'game',organizations:[],people:[person(1,'Sound','sound_department',0),
    person(2,'Producer','producer',1),person(3,'Composer','music_department',2,['composer: Blood and Wine expansion']),person(4,'Writer','writer',3)]});
  const rows=api.detailCredits(media).principal;
  assert.deepEqual(Array.from(rows,row=>row.name),['Writer','Composer','Producer','Sound']);
  assert.equal(api.detailCreditText(rows[1]),'composer: Blood and Wine expansion');
});

test('game platform facts include known availability without requiring a dated release',()=>{
  const media=api.prepareDetail({type:'game',platforms:['PC','PlayStation'],releases:[{platform:'PC'},{platform:'Xbox'}]});
  assert.equal(api.detailFacts(media).find(row=>row.label==='Platforms').value,'PC, PlayStation, Xbox');
});

test('release labels distinguish future calendar dates without guessing partial dates',()=>{
  for(const [release,label] of [['2026-10-20','Releases'],['2026-10-19','Released'],['2026-10-18','Released'],['2027','Releases'],['2026-11','Releases'],['2026-10','Released']]) {
    assert.equal(api.detailFacts(api.prepareDetail({type:'movie',release_date:release}),'2026-10-19')[0].label,label);
  }
  assert.equal(api.detailFacts(api.prepareDetail({type:'series',year:'2027'}),'2026-10-19')[0].label,'Releases');
});

test('header browse links preserve media type and select a full year in timeline view',()=>{
  assert.equal(api.detailBrowseHref('movie'), '/browse?type=movie');
  const year=new URL(api.detailBrowseHref('series',{year:'2014'}),'https://anylist.test').searchParams;
  assert.equal(year.get('type'),'series');
  assert.equal(year.get('start'),'2014-01-01');
  assert.equal(year.get('end'),'2014-12-31');
  assert.equal(year.get('view'),'timeline');
  const genre=new URL(api.detailBrowseHref('movie',{genre:'Science Fiction'}),'https://anylist.test').searchParams;
  assert.equal(genre.get('genres'),'878');
  assert.equal(new URL(api.detailBrowseHref('series',{genre:'Sci-Fi & Fantasy'}),'https://anylist.test').searchParams.get('genres'),'10765');
  assert.equal(new URL(api.detailBrowseHref('movie',{genre:'Anime'}),'https://anylist.test').searchParams.get('genres'),'name:Anime');
  assert.equal(new URL(api.detailBrowseHref('series',{genre:'Action'}),'https://anylist.test').searchParams.get('genres'),'name:Action');
  assert.equal(api.detailBrowseHref('movie',{year:'invalid'}),'/browse?type=movie');
  assert.equal(api.detailBrowseHref('book'),null);
});

test('header creators preserve person links and exclude cast and unrelated crew',()=>{
  const person=(id,kind,role)=>({id,kind,name:`Person ${id}`,roles:[role],url:`/staff/catalogue:${id}`});
  const people=[person(8,'staff','Director of Photography'),person(9,'staff','Casting Director'),person(10,'staff','Art Director'),person(1,'actor','Actor'),person(2,'staff','Director'),person(3,'staff','Writer'),person(4,'staff','Creator'),person(5,'staff','Director')];
  const creators=api.detailCreators(people);
  assert.deepEqual(Array.from(creators,person=>person.id),[2,4]);
  assert.equal(creators[0].url,'/staff/catalogue:2');
});

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
  assert.deepEqual(Array.from(credits.supporting,p=>p.id),[]);
  assert.deepEqual(Array.from(credits.studios,p=>p.id),[7]);
  media.people=media.people.filter(p=>p.kind!=='actor');
  assert.deepEqual(Array.from(api.detailCredits(media).preview,p=>p.id),[4,5]);
});

test('information groups roles once and prefers primary networks over alternate outlets',()=>{
  const organization=(id,name,roles)=>({id,kind:'studio',name,roles,characters:[],url:null});
  const media=api.prepareDetail({type:'series',networks:[{name:'Apple TV+'}],organizations:[organization(1,'YouTube',['Network']),organization(2,'Apple TV+',['Network']),organization(3,'Apple Studios',['Studio']),organization(4,'Apple Studios',['Production Company']),organization(5,'Rental store',['Distributor'])]});
  const groups=api.detailOrganizationFacts(media);
  assert.deepEqual(Array.from(groups,g=>g.label),['Network','Studios']);
  assert.deepEqual(Array.from(groups.flatMap(g=>g.rows),p=>p.name),['Apple TV+','Apple Studios']);
});

test('limited TV producer contributions show truthful episode counts and hide full-series labels',()=>{
  const person={kind:'staff',roles:['Executive Producer'],producer_episode_count:12,episode_total:60};
  assert.equal(api.detailCreditEpisodes(person),'12 episodes');
  assert.equal(api.detailCreditEpisodes({...person,producer_episode_count:1}),'1 episode');
  assert.equal(api.detailCreditEpisodes({...person,producer_episode_count:60}),'');
  assert.equal(api.detailCreditEpisodes({...person,roles:['Creator','Executive Producer']}),'');
  assert.equal(api.detailCreditEpisodes({...person,producer_episode_count:0}),'');
  assert.equal(api.detailCreditEpisodes({...person,producer_episode_count:1.5}),'');
});

test('principal crew includes original authors and department heads and keeps historical studios separate',()=>{
  const person=(id,roles)=>({id,kind:'staff',name:`Person ${id}`,roles,characters:[]});
  const media=api.prepareDetail({type:'movie',people:[person(1,['Novel']),person(2,['Editor']),
    person(3,['Production Design']),person(4,['Assistant Director']),person(5,['Executive Producer'])],
    organizations:[{id:10,kind:'studio',name:'Marvel Studios',roles:['Producer'],characters:[]},
      {id:11,kind:'studio',name:'Marvel Films',roles:['Producer'],characters:[]}]});
  assert.deepEqual(Array.from(api.detailCredits(media).principal,p=>p.id),[1,2,3]);
  assert.deepEqual(Array.from(api.detailCredits(media).studios,p=>p.name),['Marvel Studios','Marvel Films']);
});

test('server-selected cast order and bounded lists survive background updates',()=>{
  const cast=Array.from({length:90},(_,id)=>({id,kind:'actor',name:String(id),roles:['Actor'],
    characters:[],position:id,credit_order:id===89?0:id+1}));
  const media=api.prepareDetail({type:'series',people:cast});
  const credits=api.detailCredits(media);
  assert.equal(credits.cast.length,24);assert.equal(credits.preview[0].id,89);
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

test('movie budget and box office follow Country with whole-dollar currency formatting',()=>{
  const facts=api.detailFacts(api.prepareDetail({type:'movie',countries:['US'],budget:150000000,revenue:825000001}));
  assert.deepEqual(Array.from(facts,fact=>[fact.label,fact.value]),[
    ['Country',countryLabel('US')],['Budget','$150,000,000'],['Box office','$825,000,001'],
  ]);
});

test('unknown movie financials and non-movie financials stay out of Information',()=>{
  for(const value of [undefined,null,0,-1,'1000',true,Infinity,1.5]){
    assert.equal(api.detailFacts(api.prepareDetail({type:'movie',budget:value,revenue:value})).length,0);
  }
  for(const type of ['series','book','game']){
    assert.equal(api.detailFacts(api.prepareDetail({type,budget:150000000,revenue:825000000})).length,0);
  }
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

test('game crew keep their detailed jobs while canonical writer roles determine prominence',()=>{
  const people=[
    {id:1,kind:'staff',name:'Composer',roles:['Composer'],role_codes:['composer'],position:0},
    {id:2,kind:'staff',name:'Writer',roles:['world created by'],role_codes:['writer'],position:20},
  ];
  const game=api.prepareDetail({type:'game',people,organizations:[]});
  assert.deepEqual(Array.from(api.detailCredits(game).principal,row=>row.name),['Writer','Composer']);
  assert.equal(api.detailCredits(game).principal[0].roles[0],'world created by');
});


test('screen information distinguishes pending fields from confirmed unavailable fields',()=>{
  const media=context.api.prepareDetail({id:1,type:'series',title:'Series',genres:[],countries:[],people:[],organizations:[]});
  const pending=context.api.detailInformationRows(media,true);
  assert.ok(pending.some(row=>row.label==='Network'&&row.pending));
  assert.ok(pending.some(row=>row.label==='Episode runtime'&&row.pending));
  assert.equal(context.api.detailInformationRows(media,false).length,0);
  media.episode_count=12;
  media.organizations=[{id:3,kind:'studio',name:'Studio',url:'/studio/3',roles:['producer'],characters:[]}];
  const ready=context.api.detailInformationRows(media,false);
  assert.equal(ready.find(row=>row.label==='Episodes').value,'12');
  assert.equal(ready.find(row=>row.label==='Studios').items[0].name,'Studio');
  assert.ok(ready.every(row=>!row.pending));
});
