import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import test from 'node:test';

const source=readFileSync(new URL('../src/lib/notification-presentation.ts',import.meta.url),'utf8')
  .replace("import { statuses } from './tracker';",'const statuses=[];')
  .replace("'./date-time'",JSON.stringify(new URL('../src/lib/date-time.ts',import.meta.url).href));
const {eventImage,eventTitle,ratingMetadata,ratingPopupEnabled,notificationVersion,groupRatingNotifications,ratingTargets}=await import(
  `data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(source)).toString('base64')}`);
const series={id:12,title:'A series',type:'series',poster:'/show.jpg',year:'2020',season_count:3,episode_count:30};
const season={id:1,kind:'rating_needed',season_number:2,media:series,
  payload:{season_poster_path:'/season-2.jpg',episode_order:'tmdb:aired',rating_metadata:{episode_count:10,year:'2024'}}};

test('season prompts use their own artwork, label and episode metadata',()=>{
  assert.equal(eventTitle(season),'A series : Season 2');
  assert.equal(eventImage(season),'/season-2.jpg');
  assert.equal(ratingMetadata(season),'10 episodes · 2024');
});
test('movie and overall-series prompts carry compact, truthful metadata',()=>{
  assert.equal(ratingMetadata({kind:'rating_needed',media:{type:'movie',runtime:128,year:'2025'}}),'2h 8m · 2025');
  assert.equal(ratingMetadata({kind:'rating_needed',media:series}),'3 seasons · 30 episodes · 2020');
  assert.equal(ratingMetadata({...season,payload:{}}),'');
  assert.equal(ratingMetadata({kind:'rating_needed',media:{type:'movie',runtime:0}}),'');
});
test('popup preferences are independent and never remove notification cards',()=>{
  assert.equal(ratingPopupEnabled(season,{show_new_ratings_popup:false}),true);
  assert.equal(ratingPopupEnabled(season,{show_season_rating_popup:false}),false);
  assert.equal(ratingPopupEnabled({kind:'rating_needed',media:series},{show_season_rating_popup:false}),true);
});
test('metadata enrichment invalidates the rendered notification version',()=>{
  const movie={id:1,kind:'rating_needed',media:{id:2,type:'movie',runtime:100}};
  assert.notEqual(notificationVersion(movie,'event'),notificationVersion({...movie,media:{...movie.media,runtime:120}},'event'));
});

test('final-season and series requests share one card without losing independent targets',()=>{
  const earlier={...season,id:2,season_number:1};
  const overall={id:3,kind:'rating_needed',media:series,payload:{}};
  const source=[earlier,overall,season];
  const grouped=groupRatingNotifications(source);
  assert.equal(grouped.length,2);
  assert.equal(grouped[0],earlier);
  assert.deepEqual(ratingTargets(grouped[1]).map(event=>event.id),[1,3]);
  assert.equal(season.series_rating_request,undefined);
  assert.notEqual(notificationVersion(grouped[1],'event'),notificationVersion(season,'event'));
  assert.deepEqual(groupRatingNotifications([overall]),[overall]);
  assert.deepEqual(groupRatingNotifications([earlier,season]),[earlier,season]);
  assert.equal(groupRatingNotifications([overall,{...season,media:{...series,id:99}}]).length,2);
});
