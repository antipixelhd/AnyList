import test from 'node:test';
import assert from 'node:assert/strict';
import {footerLinks} from '../src/lib/footer.ts';

test('regular beta pages retain draft footer links without publishing Legal notice', () => {
  assert.deepEqual(footerLinks('/login', false, 'beta'), {
    brand:'/dev/about',
    links:[{href:'/dev/privacy',label:'Privacy'},{href:'/dev/credits',label:'Credits & licenses'}],
  });
});

test('draft pages include Legal notice and keep all links in dev', () => {
  const footer = footerLinks('/dev/privacy', false);
  assert.equal(footer.brand, '/dev/about');
  assert.deepEqual(footer.links.map(link => link.href), ['/dev/legal-notice','/dev/privacy','/dev/credits']);
});

test('disabled production keeps its footer brand without exposing draft links', () => {
  assert.deepEqual(footerLinks('/login', false), {brand:'/',links:[]});
  assert.deepEqual(footerLinks('/login', true).links.map(link => link.href), ['/privacy','/credits']);
});
