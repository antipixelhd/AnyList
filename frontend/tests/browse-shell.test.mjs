import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {transpile} from 'typescript';
import assert from 'node:assert/strict';
import test from 'node:test';
import * as view from '../src/lib/browse-view.ts';
import {releaseYearBounds,releaseYearLabel} from '../src/lib/browse-ranges.ts';

const page=readFileSync(new URL('../src/pages/browse.astro',import.meta.url),'utf8');
const frontmatter=page.slice(3,page.indexOf('\n---',3)).replace(/^import[\s\S]*?;\r?\n/gm,'');
const script=transpile(`(async()=>{${frontmatter};return {params,region,categories,payload,facets};})()`);

test('browse renders without waiting for discovery or facets and preserves URL filters',{timeout:1000},async()=>{
  const calls=[];
  const result=await runInNewContext(script,{
    viewerMediaPreferences:async()=>({show_movies_series:true,show_games:true}),
    viewerProfilePreferences:async()=>({country:'DE'}),
    ...view,releaseYearBounds,releaseYearLabel,URLSearchParams,Intl,Date,
    Astro:{url:new URL('https://example.test/browse?type=series&genres=18&provider=8&view=timeline'),locals:{token:'viewer',user:{id:1}},cookies:{get:()=>undefined},request:{headers:{get:()=>null}}},
    api:{profile:{get:async()=>({country:'DE'})}},
    tracker:path=>{calls.push(path);return new Promise(()=>{});},
    STREAMING_REGION_COOKIE:'region',streamingRegionPreference:()=>null,streamingCountry:value=>value,
    requestDateLocale:()=> 'en',formatDate:value=>value,
  });
  assert.deepEqual(calls,[]);
  assert.equal(result.region,'DE');
  assert.equal(result.categories,false);
  assert.equal(result.params.get('media_type'),'series');
  assert.equal(result.params.get('provider'),'8');
  assert.equal(result.params.get('genres'),'18');
  assert.equal(result.params.get('view'),'timeline');
  assert.equal(result.payload.results.length,0);
});
