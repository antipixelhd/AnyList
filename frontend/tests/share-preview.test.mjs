import assert from 'node:assert/strict';
import test from 'node:test';
import {sharePath, mediaShareDescription} from '../src/lib/share-preview.ts';

test('share identities discard profile sections and reject URLs and query credentials', () => {
  assert.equal(sharePath('/user/Alex%20B/stats/'), '/user/Alex%20B');
  assert.equal(sharePath('/actors/catalogue%3A42'), '/actors/catalogue:42');
  assert.equal(sharePath('/person/42'), '/person/42');
  assert.equal(sharePath('/title/12'), '/title/12');
  for (const path of ['/title/12?token=secret', '//evil.test/title/12', '/user/%2Fadmin', '/title/0', '/%invalid']) {
    assert.equal(sharePath(path), null, path);
  }
});

test('detail metadata uses IMDb and genres without synopsis or personal score', () => {
  const description = mediaShareDescription({type:'movie', year:'2025', genres:['Drama'], imdb_score:8.2,
    score:2, tmdb_score:9, overview:'Never expose this synopsis', entry:{score:1}});
  assert.equal(description, 'Movie · 2025 · Drama · IMDb 8.2');
  assert.equal(mediaShareDescription({type:'series', genres:[]}), 'Series');
});
