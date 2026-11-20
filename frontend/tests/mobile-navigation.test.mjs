import assert from 'node:assert/strict';
import test from 'node:test';
import {appMobileNavigation} from '../src/lib/mobile-navigation.ts';

const navigation = (overrides = {}) => appMobileNavigation({profile:'/user/alex', pathname:'/home', signedIn:true, combineLists:true, ...overrides});

test('signed-in navigation fits eight actions plus Close and uses the viewer profile', () => {
  const {items} = navigation();
  const visible = items.filter(item => !item.hidden);
  assert.equal(visible.length, 8);
  assert.equal(visible.find(item => item.label === 'Lists').group, 'lists');
  assert.equal(visible.find(item => item.label === 'Profile').href, '/user/alex');
  assert.equal(visible.find(item => item.label === 'Search').action, 'search');
  assert.equal(visible.find(item => item.label === 'Sign out').action, 'logout');
});

test('separate lists use a submenu without exceeding the main grid', () => {
  const {items, groups} = navigation({combineLists:false, pathname:'/user/alex/series'});
  const visible = items.filter(item => !item.hidden);
  assert.equal(visible.length, 8);
  const lists = visible.find(item => item.label === 'Lists');
  assert.equal(lists.group, 'lists');
  assert.equal(lists.active, true);
  assert.deepEqual(groups[0].items.filter(item=>!item.hidden).map(item => item.href), ['/user/alex/movies','/user/alex/series','/user/alex/games']);
  assert.equal(groups[0].items.find(item=>item.label==='Series').active, true);
});

test('guest navigation has no account actions or invalid login/list links', () => {
  const {items, groups} = navigation({signedIn:false, profile:'/login'});
  assert.equal(items.length, 5);
  assert.deepEqual(groups, []);
  assert.ok(items.every(item => !item.action && !item.notifications && !item.href?.startsWith('/login/')));
  assert.ok(items.some(item => item.href === '/register'));
  assert.ok(items.some(item => item.href === '/login'));
});

test('settings subcategories keep the Settings action active', () => {
  for (const pathname of ['/settings/media', '/settings/lists', '/settings/notifications']) {
    const {items} = navigation({pathname});
    assert.equal(items.find(item => item.label === 'Settings').active, true);
  }
});

test('one visible domain has a direct list action; opt-out preserves the mobile grid',()=>{
  const screen=navigation({showGames:false});
  assert.equal(screen.items.find(item=>!item.hidden&&item.label==='Lists').href,'/user/alex/list');
  const games=navigation({showMoviesSeries:false});
  assert.equal(games.items.find(item=>!item.hidden&&item.id==='games-list').href,'/user/alex/games');
  assert.ok(games.items.filter(item=>!item.hidden).length<=8);
  const none=navigation({showMoviesSeries:false,showGames:false});
  assert.equal(none.items.filter(item=>!item.hidden).length,7);
});
