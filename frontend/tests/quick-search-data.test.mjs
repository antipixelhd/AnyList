import assert from 'node:assert/strict';
import test from 'node:test';
import {quickSearchRequests,visibleQuickSearchSections} from '../src/lib/quick-search-data.ts';

test('desktop searches both media types and all contributors without duplicating people requests',()=>{
  const requests=quickSearchRequests('James & Co',false,'actor');
  assert.deepEqual(requests.map(row=>row.kind),['movie','series','contributors']);
  const params=new URL(requests[2].url,'https://anylist.test').searchParams;
  assert.equal(params.get('q'),'James & Co');assert.equal(params.get('kind'),'all');
});
test('mobile searches only the selected section, defaulting to series and movies',()=>{
  assert.deepEqual(quickSearchRequests('James',true,'all').map(row=>row.kind),['movie','series']);
  for(const section of ['actor','staff','studio']){
    const requests=quickSearchRequests('James',true,section);
    assert.equal(requests.length,1);assert.equal(requests[0].kind,'contributors');
    assert.equal(new URL(requests[0].url,'https://anylist.test').searchParams.get('kind'),section);
  }
});
test('desktop shows only matching sections and respects combined media; mobile displays one section',()=>{
  const counts={all:3,movie:1,series:2,actor:2,staff:0,studio:1};
  assert.deepEqual(visibleQuickSearchSections(false,'all',true,counts),['all','actor','studio']);
  assert.deepEqual(visibleQuickSearchSections(false,'all',false,counts),['movie','series','actor','studio']);
  assert.deepEqual(visibleQuickSearchSections(true,'all',false,counts),['all']);
  assert.deepEqual(visibleQuickSearchSections(true,'studio',true,counts),['studio']);
  assert.deepEqual(visibleQuickSearchSections(true,'staff',true,counts),[]);
});
