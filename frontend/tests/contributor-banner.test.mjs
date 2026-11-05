import assert from 'node:assert/strict';
import test from 'node:test';
import {contributorBanner,mountContributorBanner} from '../src/lib/contributor-banner.ts';

const movie={backdrop:'/movie.jpg',list_status:null};
const series={backdrop:'/series.jpg',list_status:'watching'};
const game={backdrop:'https://images.igdb.com/igdb/image/upload/t_1080p/game.jpg',list_status:'playing'};

test('hero follows list order, skips missing backdrops and respects in-list membership',()=>{
  assert.equal(contributorBanner([{},movie,series,game]).src,'https://image.tmdb.org/t/p/w1280/movie.jpg');
  assert.equal(contributorBanner([movie,series,game],null,true).src,'https://image.tmdb.org/t/p/w1280/series.jpg');
  assert.match(contributorBanner([game,movie]).src,/game\.jpg$/);
  assert.equal(contributorBanner([]).src,'');
});

test('studio artwork is a fallback after the current works, rather than overriding them',()=>{
  const logo='https://example.com/studio.png';
  assert.equal(contributorBanner([movie],logo).isLogo,false);
  assert.match(contributorBanner([movie],logo).src,/movie\.jpg$/);
  assert.deepEqual(contributorBanner([],logo),{src:logo,isLogo:true});
});

function fixture(){
  const image=new EventTarget(),controller=new AbortController();
  const attrs=new Map(),classes=new Set();let writes=0;
  Object.assign(image,{hidden:false,complete:false,naturalWidth:0,
    getAttribute:name=>attrs.get(name)||null,
    removeAttribute:name=>attrs.delete(name),
    closest:()=>({classList:{toggle:(name,on)=>on?classes.add(name):classes.delete(name)}}),
  });
  Object.defineProperty(image,'src',{get:()=>attrs.get('src'),set:value=>{writes++;attrs.set('src',value);image.complete=false;image.naturalWidth=0;}});
  return {image,controller,classes,update:mountContributorBanner(image,controller.signal),writes:()=>writes,
    load:()=>{image.complete=true;image.naturalWidth=1280;image.dispatchEvent(new Event('load'));}};
}

test('domain/toggle changes and cached returns reveal current artwork only, without repeated loads',()=>{
  const f=fixture();
  f.update([game]);assert.equal(f.image.hidden,true);f.load();assert.equal(f.image.hidden,false);
  f.update([movie,series]);assert.match(f.image.src,/movie\.jpg$/);assert.equal(f.image.hidden,true);
  f.load();f.update([movie,series],null,true);assert.match(f.image.src,/series\.jpg$/);
  f.load();f.update([game]);f.load();
  const writes=f.writes();f.update([game]);assert.equal(f.writes(),writes);assert.equal(f.image.hidden,false);
  f.update([]);assert.equal(f.image.getAttribute('src'),null);assert.equal(f.image.hidden,true);
  f.controller.abort();f.update([movie]);assert.equal(f.image.getAttribute('src'),null);
});

test('empty initial heroes can acquire artwork; failed artwork stays hidden and later changes recover',()=>{
  const f=fixture();
  f.update([movie]);f.image.dispatchEvent(new Event('error'));assert.equal(f.image.hidden,true);
  f.update([], 'https://example.com/studio.png');f.load();assert.equal(f.classes.has('is-logo'),true);
  f.update([series]);assert.equal(f.classes.has('is-logo'),false);f.load();assert.equal(f.image.hidden,false);
  f.controller.abort();
});
