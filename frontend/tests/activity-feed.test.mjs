import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import test from 'node:test';
const source=readFileSync(new URL('../src/lib/activity-feed.ts',import.meta.url),'utf8').replace("'./date-time'",JSON.stringify(new URL('../src/lib/date-time.ts',import.meta.url).href));
const {mergeActivityFeeds,activityPresentation}=await import(`data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(source)).toString('base64')}`);

test('activity windows retain separate game/movie identities and deterministically merge recent cards',()=>{
  const movie={key:'1:7:day',created_at:'2026-10-21T10:00:00',media:{id:7,type:'movie'}};
  const game={key:'game:1:7:window',created_at:'2026-10-21T11:00:00',media:{id:7,type:'game'}};
  assert.deepEqual(mergeActivityFeeds([[movie],[game,game]]).map(row=>row.key),[game.key,movie.key]);
  assert.deepEqual(mergeActivityFeeds([[movie],[game]],1),[game]);
  assert.equal(mergeActivityFeeds([[game],[{...game,created_at:'2026-10-21T12:00:00'}]])[0].created_at,'2026-10-21T12:00:00');
});
test('browser activity seed contains card fields, excluding private notes and evidence arrays',()=>{
  const projected=activityPresentation({key:'game:1:7:window',status:'playing',score:8,created_at:'2026-10-21T11:00:00',
    notes:'Private',payload:{status_changed:true,hours_changed:true,completion_seconds:0,notes:'Private',watched_start_ids:[1,2,3],window_started_at:'hidden'},
    media:{id:7,type:'game',title:'Game',poster:'/cover.jpg',provider_blob:{private:true}}});
  assert.equal(projected.media.type,'game');assert.equal(projected.payload.hours_changed,true);
  assert.equal(projected.payload.completion_seconds,0);
  assert.equal(JSON.stringify(projected).includes('Private'),false);assert.equal('watched_start_ids' in projected.payload,false);
  assert.equal('provider_blob' in projected.media,false);
});
