import test from 'node:test';
import assert from 'node:assert/strict';
import {publicLegalPage} from '../src/lib/legal-routes.ts';
import {legalDraft, legalHref, legalConfig} from '../src/lib/legal.ts';

test('notice routes are exact matches without opening neighboring settings or APIs', () => {
  for(const path of ['/legal-notice','/impressum','/privacy','/cookies','/credits','/about/']) assert.equal(publicLegalPage(path),true,path);
  for(const path of ['/settings','/privacy/private','/api/legal/config','//privacy','/user/privacy']) assert.equal(publicLegalPage(path),false,path);
});

test('publication fails closed, filters fields and shares concurrent disclosure reads', async (t) => {
  let now = 10000;
  t.mock.method(Date, 'now', () => now);
  let data = {enabled:false, operator_name:'Do not expose'};
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => ({ok:true, json:async () => data}));
  const [first, second] = await Promise.all([legalConfig(), legalConfig()]);
  assert.equal(first, second);
  assert.equal(fetchMock.mock.callCount(), 1);
  assert.equal(first.enabled, false);
  assert.equal(first.operator_name, '');
  now += 6000;
  data = {enabled:true, operator_name:'Incomplete'};
  assert.equal((await legalConfig()).enabled, false);
  now += 6000;
  data = {enabled:true, operator_name:'Example', operator_address:'Street', contact_email:'legal@example.test', hosting_details:'Host', retention_details:'Retention', transfer_details:'Transfers', secret_key:'never-export'};
  const published = await legalConfig();
  assert.equal(published.enabled, true);
  assert.equal(published.operator_name, 'Example');
  assert.equal('secret_key' in published, false);
});

test('missing operator or operational facts keep the notice in draft state', () => {
  const values = {operator_name:'Example', operator_address:'Example Street', contact_email:'contact@example.test',hosting_details:'Host',retention_details:'Retention',transfer_details:'Transfers'};
  assert.equal(legalDraft(values),false);
  for(const key of Object.keys(values)) assert.equal(legalDraft({...values,[key]:' '}),true,key);
});

test('draft links stay inside the preview namespace', () => {
  for (const page of ['privacy', 'legal-notice', 'credits', 'about']) {
    assert.equal(legalHref('/dev/privacy', page), `/dev/${page}`);
    assert.equal(legalHref('/privacy', page), `/${page}`);
  }
});
