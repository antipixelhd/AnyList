import assert from 'node:assert/strict';
import test from 'node:test';
import {SHARE_IMAGE_REVISION, shareImagePath, crawlerShareResponse, generalSharePage, loginSharePage, loginShareIdentity, shareMetadata, shareLoginPath, sharePagePath, sharePath, mediaShareDescription} from '../src/lib/share-preview.ts';

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
  assert.equal(shareLoginPath('/title/12'), '/login?next=%2Ftitle%2F12');
  assert.deepEqual(loginSharePage('/home?token=secret'), {path: '/home', title: 'Home'});
});

test('detail identities survive login for title, actor, crew, studio and profile cards', async () => {
  for (const path of ['/title/102', '/games/102', '/actors/catalogue:42', '/person/tmdb:42', '/studio/42', '/user/alex/stats', '/user/alex/games']) {
    const redirect = new URL(shareLoginPath(path, '?token=secret'), 'https://anylist.test');
    const identity = loginShareIdentity(redirect.searchParams.get('next'));
    assert.equal(identity, sharePath(path));
    assert.ok(!redirect.href.includes('secret'));
    let calls = 0;
    const metadata = await shareMetadata(identity, async (url, options) => {
      calls++;
      assert.equal(new URL(url).searchParams.get('path'), identity);
      assert.equal(options.headers, undefined);
      return Response.json({title: 'Custom card', description: 'Public metadata'});
    });
    assert.equal(calls, 1);
    assert.equal(metadata.title, 'Custom card');
  }
});

test('private, missing and failed metadata cannot populate a login card', async () => {
  for (const status of [401, 403, 404, 503]) {
    assert.equal(await shareMetadata('/user/alex', async () => new Response(null, {status})), null);
  }
  for (const path of ['//evil.test/title/12', '/reset-password/secret', '/title/12#secret', '/user/a%2fb']) {
    assert.equal(loginShareIdentity(path), null);
  }
  assert.equal(await shareMetadata('//evil.test/title/12', () => assert.fail('invalid path must not fetch')), null);
  assert.equal(await shareMetadata('/title/12', async () => {throw new Error('offline');}), null);
});

test('messaging crawlers get custom metadata directly without a login redirect', async () => {
  for (const path of ['/title/102', '/games/102', '/actors/catalogue:42', '/person/tmdb:42', '/studio/42', '/user/alex/stats', '/user/alex/games']) {
    const response = await crawlerShareResponse(new URL(`${path}?token=secret`, 'https://anylist.test'),
      'Mozilla/5.0 (compatible; Discordbot/2.0)', 'https://anylist.test', async () =>
        Response.json({title: 'A title <script> & "quoted"', description: 'Movie · 2026'}));
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    const html = await response.text();
    assert.ok(html.includes('property="og:title" content="A title &lt;script&gt; &amp; &quot;quoted&quot;"'));
    assert.ok(html.includes('/api/proxy/sharing/image?path='));
    assert.ok(html.includes('name="twitter:image"'));
    assert.ok(html.includes(`v=${SHARE_IMAGE_REVISION}`));
    assert.ok(!html.includes('secret'));
    assert.ok(!html.includes('/login'));
  }
  for (const status of [401, 403, 404, 503]) {
    assert.equal(await crawlerShareResponse(new URL('https://anylist.test/title/102'), 'WhatsApp',
      'https://anylist.test', async () => new Response(null, {status})), null);
  }
  assert.equal(await crawlerShareResponse(new URL('https://anylist.test/title/102'), 'Mozilla/5.0',
    'https://anylist.test', () => assert.fail('normal navigation must not fetch previews')), null);
});

test('share images change URL by revision without changing the underlying identity', () => {
  const url = new URL(shareImagePath('/title/102'), 'https://anylist.test');
  assert.equal(url.searchParams.get('path'), '/title/102');
  assert.equal(url.searchParams.get('v'), SHARE_IMAGE_REVISION);
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
