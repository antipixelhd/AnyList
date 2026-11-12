import assert from 'node:assert/strict';
import test from 'node:test';
import { contributorParams, contributorYear,contributorCreditScope, workHref } from '../src/lib/contributor-page-data.ts';

test('confirmed child credits label the source and retain the parent game destination',()=>{
  const game={id:12,type:'game',title:'Warframe',credit_sources:[{kind:'update',title:'Warframe: Tau'}]};
  assert.equal(contributorCreditScope(game),'Update: Tau');
  assert.equal(workHref(game),'/games/12');
  assert.equal(contributorCreditScope({title:'Warframe'}),'');
  assert.equal(contributorCreditScope({title:'Example',credit_sources:[{kind:'edition',title:'Example: Complete Edition'}]}),'Edition: Complete Edition');
  assert.equal(contributorCreditScope({title:'Example',credit_sources:[{kind:'port',title:'Example Mobile'}]}),'Port: Example Mobile');
  assert.equal(contributorCreditScope({title:'Example',credit_sources:[{kind:'episode',title:'Example: Episode III'}]}),'Episode III');
  assert.equal(contributorCreditScope({title:'Vader Immortal: A Star Wars VR Series',credit_sources:[
    {kind:'episode',title:'Vader Immortal: Episode I'},{kind:'episode',title:'Vader Immortal: Episode III'}]}),'Episode I · Episode III');
  assert.equal(contributorCreditScope({title:'Example: The Story',credit_sources:[{kind:'episode',title:'Other: Episode III'}]}),'Episode: Other: Episode III');
});

test('contributor filters whitelist scopes and never send viewer IDs', () => {
  const input = new URLSearchParams('media_type=series&list_scope=in&status=completed&page=2&view=grid&user_id=99&cursor=abc&source=provider');
  assert.equal(contributorParams(input,true).toString(),'media_type=series&list_scope=in&page=2&cursor=abc&source=provider');
  assert.equal(contributorParams(input,false).toString(),'media_type=series&page=2&cursor=abc&source=provider');
  assert.equal(contributorParams(new URLSearchParams('media_type=person&list_scope=x&status=x&page=-2&source=x'),true).toString(),'');
  assert.equal(contributorParams(new URLSearchParams('list_scope=out&status=completed'),true).toString(),'');
});
test('timeline groups dates and preserves internal, discover and provider destinations', () => {
  assert.equal(contributorYear({release_date:'2026-06-01',year:'2020'}),'2026');
  assert.equal(contributorYear({release_date:null,year:'2020'}),'2020');
  assert.equal(contributorYear({release_date:null,year:''}),'Undated');
  assert.equal(workHref({id:8,tmdb_id:9,type:'movie'}),'/title/8');
  assert.equal(workHref({id:null,tmdb_id:9,type:'series'}),'/discover-title?type=series&id=9');
  assert.equal(workHref({id:null,tmdb_id:null,type:'movie',href:'https://www.thetvdb.com/movie/1'}),'https://www.thetvdb.com/movie/1');
  assert.equal(workHref({id:null,tmdb_id:null,type:'movie'}),null);
  assert.equal(workHref({id:8,type:'game'}),'/games/8');
  assert.equal(workHref({id:null,igdb_id:9,type:'game'}),'/discover-game?id=9');
});

test('game works remain a separate domain and explicit movie/series choices survive reload',()=>{
  assert.equal(contributorParams(new URLSearchParams('media_type=game&list_scope=in'),true).toString(),'media_type=game&list_scope=in');
  assert.equal(contributorParams(new URLSearchParams('domain=game&media_type=movie'),true).get('media_type'),'game');
  assert.equal(contributorParams(new URLSearchParams('domain=movies-series&media_type=all'),true).get('media_type'),'all');
  assert.equal(contributorParams(new URLSearchParams('media_type=game&list_scope=in'),false).toString(),'media_type=game');
});

import {contributorPages} from '../src/lib/contributor-pages.ts';

test('warming the sibling tab shares its in-flight read with a click and keeps domains independent',async()=>{
  const controller=new AbortController(),calls=[];
  let finish;
  const pages=contributorPages(async path=>{
    calls.push(path);
    if(path==='games'&&calls.filter(path=>path==='games').length===1)await new Promise(resolve=>finish=resolve);
    return path;
  },controller.signal);
  const background=pages.get('games');
  assert.equal(await pages.get('screen'),'screen');
  const clicked=pages.get('games');
  assert.equal(clicked,background);finish();
  assert.equal(await clicked,'games');assert.deepEqual(calls,['games','screen']);
  pages.forget(path=>path==='games');await pages.get('games');
});

test('failed reads retry and invalidation does not let an older failure evict a replacement',async()=>{
  const controller=new AbortController();let reject,calls=0;
  const pages=contributorPages(async()=>{
    if(++calls===1)return new Promise((resolve,fail)=>reject=fail);
    return calls;
  },controller.signal);
  const old=pages.get('screen');pages.clear();
  assert.equal(await pages.get('screen'),2);
  reject(new Error('offline'));await assert.rejects(old);
  assert.equal(await pages.get('screen'),2);
});

test('superseded demand aborts its unused read without cancelling another subscriber',async()=>{
  const lifetime=new AbortController(),a=new AbortController(),b=new AbortController(),calls=[];
  const pages=contributorPages((path,signal)=>new Promise((resolve,reject)=>{
    calls.push({path,signal,resolve});signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
  }),lifetime.signal);
  const first=pages.get('screen',a.signal),second=pages.get('screen',b.signal);
  a.abort();await assert.rejects(first,{name:'AbortError'});
  assert.equal(calls.length,1);assert.equal(calls[0].signal.aborted,false);
  b.abort();await assert.rejects(second,{name:'AbortError'});
  assert.equal(calls[0].signal.aborted,true);
  const retry=pages.get('screen');calls[1].resolve('fresh');assert.equal(await retry,'fresh');
});

test('intent warming shares demand and invalidation/eviction cancel bounded background reads',async()=>{
  const lifetime=new AbortController(),click=new AbortController(),calls=[];
  const pages=contributorPages((path,signal)=>new Promise((resolve,reject)=>{
    calls.push({path,signal,resolve});signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
  }),lifetime.signal);
  const warm=pages.get('games'),selected=pages.get('games',click.signal);
  click.abort();await assert.rejects(selected,{name:'AbortError'});
  assert.equal(calls[0].signal.aborted,false);calls[0].resolve('games');assert.equal(await warm,'games');
  const background=Array.from({length:6},(_,i)=>pages.get(String(i)).catch(error=>error.name));
  const extra=pages.get('extra').catch(error=>error.name);
  assert.equal(calls[1].signal.aborted,true);
  lifetime.abort();
  assert.deepEqual(await Promise.all([...background,extra]),Array(7).fill('AbortError'));
  await assert.rejects(pages.get('departed'),{name:'AbortError'});
});
