import assert from 'node:assert/strict';
import test from 'node:test';
import {generalSharePage, loginSharePage, shareLoginPath, sharePagePath, sharePath, mediaShareDescription} from '../src/lib/share-preview.ts';

test('general previews retain public page labels through the login redirect', () => {
  for (const [path, title] of [['/', 'Welcome'], ['/home', 'Home'], ['/settings', 'Account'],
    ['/settings/media', 'Movies & series'], ['/settings/notifications', 'Notifications']]) {
    const redirect = new URL(shareLoginPath(path, '?token=secret&sort=score'), 'https://anylist.test');
    assert.deepEqual(loginSharePage(redirect.searchParams.get('next')), {path, title});
    assert.ok(!redirect.href.includes('secret'));
  }
  assert.deepEqual(generalSharePage('/connections', '?section=apps&token=secret'),
    {path: '/connections?section=apps', title: 'Connected apps'});
});

test('login previews never disclose arbitrary paths or replace dedicated cards', () => {
  for (const path of ['/title/12', '/user/alex', '/actors/42', '/reset-password/secret',
    '//evil.test/home', '/home#secret', '/home\\secret', '/unknown/secret', '/%68ome']) {
    assert.equal(loginSharePage(path), null, path);
  }
  assert.equal(shareLoginPath('/title/12'), '/login');
  assert.deepEqual(loginSharePage('/home?token=secret'), {path: '/home', title: 'Home'});
});

test('share identities discard profile sections and reject URLs and query credentials', () => {
  assert.equal(sharePath('/user/Alex%20B/stats/'), '/user/Alex%20B');
  assert.equal(sharePath('/actors/catalogue%3A42'), '/actors/catalogue:42');
  assert.equal(sharePath('/person/42'), '/person/42');
  assert.equal(sharePath('/title/12'), '/title/12');
  for (const path of ['/title/12?token=secret', '//evil.test/title/12', '/user/%2Fadmin', '/title/0', '/%invalid']) {
    assert.equal(sharePath(path), null, path);
  }
});

test('profile sections retain their shared destination and reuse the profile card', () => {
  assert.equal(sharePath('/user/Alex%20B/stats'), '/user/Alex%20B');
  assert.equal(sharePagePath('/user/Alex%20B/stats', '?section=actors&media=movie&token=secret'),
    '/user/Alex%20B/stats?media=movie&section=actors');
  assert.equal(sharePagePath('/user/alex/favorites'), '/user/alex/favorites');
  assert.equal(sharePagePath('/user/alex/social'), '/user/alex/social');
});

test('general share URLs keep ordinary destinations but never expose auth tokens', () => {
  assert.equal(sharePagePath('/'), '/');
  assert.equal(sharePagePath('/home'), '/home');
  assert.equal(sharePagePath('/settings'), '/settings');
  assert.equal(sharePagePath('/settings/notifications'), '/settings/notifications');
  assert.equal(sharePagePath('/reset-password/secret-token'), '/');
  assert.equal(sharePagePath('/auth/activate/secret-token'), '/');
});

test('detail metadata uses IMDb and genres without synopsis or personal score', () => {
  const description = mediaShareDescription({type:'movie', year:'2025', genres:['Drama'], imdb_score:8.2,
    score:2, tmdb_score:9, overview:'Never expose this synopsis', entry:{score:1}});
  assert.equal(description, 'Movie · 2025 · Drama · IMDb 8.2');
  assert.equal(mediaShareDescription({type:'series', genres:[]}), 'Series');
});
