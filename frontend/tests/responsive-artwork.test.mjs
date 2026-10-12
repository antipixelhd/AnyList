import assert from 'node:assert/strict';
import test from 'node:test';
import {responsiveArtwork,artworkIdentity,ARTWORK_WIDTHS} from '../src/lib/responsive-artwork.ts';

test('automatic routing follows the optional disk cache for TMDB and TVDB',()=>{
  const previous=globalThis.document;
  try {
    globalThis.document={body:{dataset:{imageCache:'false'}}};
    assert.equal(responsiveArtwork('/poster.jpg',{size:'w342',route:'auto'}).src,'https://image.tmdb.org/t/p/w342/poster.jpg');
    globalThis.document.body.dataset.imageCache='true';
    assert.equal(responsiveArtwork('https://image.tmdb.org/t/p/original/poster.jpg',{size:'w342',route:'auto'}).src,'/api/proxy/media/image/w342/poster.jpg');
    assert.equal(responsiveArtwork('https://artworks.thetvdb.com/banners/person.jpg',{route:'auto'}).src,'/api/proxy/media/image/tvdb/banners/person.jpg');
    globalThis.document.body.dataset.imageCache='false';
    assert.equal(responsiveArtwork('/api/proxy/media/image/tvdb/banners/person.jpg',{route:'auto'}).src,'https://artworks.thetvdb.com/banners/person.jpg');
  }finally{if(previous===undefined)delete globalThis.document;else globalThis.document=previous;}
});
test('size variants and same-origin disk URLs share an image identity',()=>{
  const previous=globalThis.location;
  try {
    globalThis.location={origin:'https://anylist.test'};
    const identity=artworkIdentity('https://image.tmdb.org/t/p/original/poster.jpg');
    assert.equal(artworkIdentity('/poster.jpg'),identity);
    assert.equal(artworkIdentity('/api/proxy/media/image/w185/poster.jpg'),identity);
    assert.equal(artworkIdentity('https://anylist.test/api/proxy/media/image/w342/poster.jpg'),identity);
    assert.notEqual(artworkIdentity('https://unrelated.test/api/proxy/media/image/w342/poster.jpg'),identity);
  }finally{if(previous===undefined)delete globalThis.location;else globalThis.location=previous;}
});

test('every responsive image bucket is accepted by the optional disk cache',async()=>{
  const {readFileSync}=await import('node:fs');
  const source=readFileSync(new URL('../../backend/core/image_cache.py',import.meta.url),'utf8');
  const buckets=new Set([...source.match(/ALLOWED_SIZES = \{([^}]+)\}/)[1].matchAll(/"([^"]+)"/g)].map(match=>match[1]));
  for(const width of ARTWORK_WIDTHS)assert(buckets.has(`w${width}`));
  assert(responsiveArtwork('/still.jpg',{size:'w300',sizes:'55px',route:'proxy'}).srcset.includes('w300/still.jpg 300w'));
});
