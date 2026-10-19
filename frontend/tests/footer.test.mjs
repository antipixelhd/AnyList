import test from 'node:test';
import assert from 'node:assert/strict';
import {footerLinks} from '../src/lib/footer.ts';

test('disabled publication hides notice navigation on normal and dev pages', () => {
  for (const pathname of ['/login', '/home', '/dev/privacy']) {
    assert.deepEqual(footerLinks(pathname, false), {brand:'/',links:[]});
  }
});

test('published dev pages include Legal notice and keep all links in dev', () => {
  const footer = footerLinks('/dev/privacy', true);
  assert.equal(footer.brand, '/dev/about');
  assert.deepEqual(footer.links.map(link => link.href), ['/dev/legal-notice','/dev/privacy','/dev/credits']);
});

test('published normal pages expose only the normal footer notices', () => {
  assert.deepEqual(footerLinks('/login', true), {
    brand:'/about', links:[{href:'/privacy',label:'Privacy'},{href:'/credits',label:'Credits & licenses'}],
  });
});
