import test from 'node:test';
import assert from 'node:assert/strict';
import {publicLegalPage} from '../src/lib/legal-routes.ts';
import {legalDraft} from '../src/lib/legal.ts';

test('legal notices stay public without opening neighboring settings or API routes', () => {
  for(const path of ['/impressum','/privacy','/cookies','/credits','/about/']) assert.equal(publicLegalPage(path),true,path);
  for(const path of ['/settings','/privacy/private','/api/legal/config','//privacy','/user/privacy']) assert.equal(publicLegalPage(path),false,path);
});

test('missing operator or operational facts keep the notice in draft state', () => {
  const values = {operator_name:'Example', operator_address:'Example Street', contact_email:'contact@example.test',hosting_details:'Host',retention_details:'Retention',transfer_details:'Transfers'};
  assert.equal(legalDraft(values),false);
  for(const key of Object.keys(values)) assert.equal(legalDraft({...values,[key]:' '}),true,key);
});
