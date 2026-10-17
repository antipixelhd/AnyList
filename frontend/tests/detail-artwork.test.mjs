import test from 'node:test';
import assert from 'node:assert/strict';
import {detailHeroArtwork, detailSceneArtwork, detailWorkArtwork} from '../src/lib/detail-artwork.ts';
import {responsiveArtwork} from '../src/lib/responsive-artwork.ts';

test('related posters use bounded responsive candidates with the active disk policy',()=>{
  const cached=detailWorkArtwork('https://image.tmdb.org/t/p/w500/poster.jpg',true,'proxy');
  assert(cached.src.startsWith('/api/proxy/media/image/'));
  assert(cached.src.includes('/w342/poster.jpg'));
  assert(cached.srcset.includes('w154/poster.jpg 154w'));
  assert.equal(cached.sizes,'(max-width:650px) calc((100vw - 50px) / 2), 65px');
  const direct=detailWorkArtwork('/poster.jpg',false,'direct');
  assert(direct.src.startsWith('https://image.tmdb.org/t/p/'));
  assert(direct.sizes.includes('74px'));
});

test('the primary scene and hero reuse identical responsive URLs across provider variants',()=>{
  for(const route of ['direct','proxy']){
    const hero=responsiveArtwork('/scene.jpg',{size:'w1280',sizes:'100vw',route});
    assert.strictEqual(detailSceneArtwork('https://image.tmdb.org/t/p/original/scene.jpg',hero,route),hero);
    assert.strictEqual(detailSceneArtwork('/api/proxy/media/image/w780/scene.jpg',hero,route),hero);
    assert.notEqual(detailSceneArtwork('/different.jpg',hero,route).src,hero.src);
  }
});

test('non-TMDB posters remain at their provider URL without fabricated resize variants',()=>{
  const url='https://artworks.thetvdb.com/banners/poster.jpg';
  assert.deepEqual(detailWorkArtwork(url,true,'direct'),{src:url});
  assert.equal(detailSceneArtwork(null,null),null);
});

test('heroes keep full resolution while previews use thumbnails and expanded sources stay canonical',()=>{
  const movie='https://images.metahub.space/background/high/tt0133093/img';
  const tvdb='https://artworks.thetvdb.com/banners/v4/movie/1/backgrounds/scene.jpg';
  for(const route of ['direct','proxy']){
    assert.deepEqual(detailHeroArtwork(movie,route),{src:movie});
    assert.equal(detailHeroArtwork('/series.jpg',route).src,route==='direct'?'https://image.tmdb.org/t/p/original/series.jpg':'/api/proxy/media/image/original/series.jpg');
    assert.equal(detailSceneArtwork('/scene.jpg',null,route).src.includes('/w780/scene.jpg'),true);
    assert.equal(detailSceneArtwork(tvdb,null,route,'https://artworks.thetvdb.com/small.jpg').src.endsWith('/small.jpg'),true);
    assert.equal(detailSceneArtwork(tvdb,null,route).src.endsWith('/banners/v4/movie/1/backgrounds/scene.jpg'),true);
  }
});
