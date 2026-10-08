import assert from 'node:assert/strict';
import test from 'node:test';
import {appMobileNavigation} from '../src/lib/mobile-navigation.ts';

const navigation = (overrides = {}) => appMobileNavigation({profile:'/user/alex', pathname:'/home', signedIn:true, combineLists:true, ...overrides});

test('signed-in navigation fits eight actions plus Close and uses the viewer profile', () => {
  const {items} = navigation();
  const visible = items.filter(item => !item.hidden);
  assert.equal(visible.length, 8);
  assert.equal(visible.find(item => item.label === 'Lists').href, '/user/alex/list');
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
  assert.deepEqual(groups[0].items.map(item => item.href), ['/user/alex/movies','/user/alex/series']);
  assert.equal(groups[0].items[1].active, true);
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
