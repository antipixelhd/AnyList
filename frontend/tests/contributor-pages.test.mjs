import assert from 'node:assert/strict';
import test from 'node:test';
import { contributorParams, contributorYear, workHref } from '../src/lib/contributor-page-data.ts';

test('contributor filters whitelist scopes and never send viewer IDs', () => {
  const input = new URLSearchParams('media_type=series&list_scope=in&status=completed&page=2&view=grid&user_id=99&cursor=abc&source=provider');
  assert.equal(contributorParams(input,true).toString(),'media_type=series&list_scope=in&status=completed&page=2&cursor=abc&source=provider');
  assert.equal(contributorParams(input,false).toString(),'media_type=series&page=2&cursor=abc&source=provider');
  assert.equal(contributorParams(new URLSearchParams('media_type=person&list_scope=x&status=x&page=-2&source=x'),true).toString(),'');
});
test('timeline groups dates and preserves internal, discover and provider destinations', () => {
  assert.equal(contributorYear({release_date:'2026-06-01',year:'2020'}),'2026');
  assert.equal(contributorYear({release_date:null,year:'2020'}),'2020');
  assert.equal(contributorYear({release_date:null,year:''}),'Undated');
  assert.equal(workHref({id:8,tmdb_id:9,type:'movie'}),'/title/8');
  assert.equal(workHref({id:null,tmdb_id:9,type:'series'}),'/discover-title?type=series&id=9');
  assert.equal(workHref({id:null,tmdb_id:null,type:'movie',href:'https://www.thetvdb.com/movie/1'}),'https://www.thetvdb.com/movie/1');
  assert.equal(workHref({id:null,tmdb_id:null,type:'movie'}),null);
});
