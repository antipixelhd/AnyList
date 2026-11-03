import assert from 'node:assert/strict';
import test from 'node:test';
import { defaultSteamDraft, sortSteamGames, steamSelected, steamProgressPending } from '../src/lib/steam-import.ts';

const games = [
  {app_id:'1',name:'Alpha',playtime_minutes:30,achievement_count:null,achievement_total:null},
  {app_id:'2',name:'Beta',playtime_minutes:0,achievement_count:1,achievement_total:2},
  {app_id:'3',name:'Gamma',playtime_minutes:15,achievement_count:3,achievement_total:20},
  {app_id:'4',name:'Delta',playtime_minutes:0,achievement_count:0,achievement_total:2},
];
test('pending mappings stay selectable; unavailable mappings override saved selection without deleting it',()=>{
  const draft=defaultSteamDraft();draft.overrides['1']=true;
  const game={...games[0],mapping_state:'pending',achievement_refresh_due:false,achievement_state:'ready'};
  assert.equal(steamSelected(game,draft),true);
  assert.equal(steamProgressPending(game),true);
  game.mapping_state='unavailable';
  assert.equal(steamSelected(game,draft),false);
  assert.equal(steamProgressPending(game),false);
  assert.equal(draft.overrides['1'],true);
  game.mapping_state='ready';
  assert.equal(steamSelected(game,draft),true);
});
test('combined apps remain independently selectable',()=>{
  const draft=defaultSteamDraft();
  const main={...games[0],app_id:'202970',mapping_state:'ready'};
  const component={...games[0],app_id:'212910',mapping_state:'ready'};
  draft.overrides[main.app_id]=false;draft.overrides[component.app_id]=true;
  assert.equal(steamSelected(main,draft),false);
  assert.equal(steamSelected(component,draft),true);
});
test('default OR thresholds select played games or a single achievement without hiding rows', () => {
  const draft = defaultSteamDraft();
  assert.deepEqual(games.map(g=>steamSelected(g,draft)),[true,true,true,false]);
  assert.equal(sortSteamGames(games,'playtime').length,4);
});
test('manual choices survive filter and sorting changes, reset clears all choices', () => {
  let draft = defaultSteamDraft();
  draft.overrides={'1':false,'4':true};
  draft.minimum_playtime=0;draft.minimum_achievements=0;
  assert.equal(steamSelected(games[0],draft),false);
  draft.minimum_playtime=100;draft.minimum_achievements=100;
  assert.equal(steamSelected(games[3],draft),true);
  assert.deepEqual(sortSteamGames(games,'completion').map(g=>g.app_id),['2','3','4','1']);
  draft=defaultSteamDraft();
  assert.equal(steamSelected(games[0],draft),true);
  assert.equal(steamSelected(games[3],draft),false);
});
test('disabled thresholds and unknown achievements do not auto select',()=>{
  const draft=defaultSteamDraft();draft.playtime_enabled=false;draft.minimum_achievements=0;
  assert.equal(steamSelected(games[0],draft),false);
  draft.achievements_enabled=false;
  assert(games.every(game=>!steamSelected(game,draft)));
});
test('late counts reselect automatically without changing manual choices or the full library',()=>{
  const loading=structuredClone(games).map(game=>({...game,achievement_count:null,achievement_total:null,achievement_state:'loading'}));
  const draft=defaultSteamDraft();draft.overrides={'1':false,'4':true};
  assert.deepEqual(loading.map(game=>steamSelected(game,draft)),[false,false,false,true]);
  loading[1].achievement_count=1;loading[1].achievement_total=2;loading[1].achievement_state='ready';
  assert.deepEqual(loading.map(game=>steamSelected(game,draft)),[false,true,false,true]);
  assert.equal(loading.length,4);
  assert.deepEqual(draft.overrides,{'1':false,'4':true});
});
test('completion sorting uses the current denominator when achievements are added or removed',()=>{
  const rows=structuredClone(games);
  assert.deepEqual(sortSteamGames(rows,'completion').map(game=>game.app_id),['2','3','4','1']);
  rows[1].achievement_total=20;
  assert.deepEqual(sortSteamGames(rows,'completion').map(game=>game.app_id),['3','2','4','1']);
  rows[1].achievement_total=1;
  assert.deepEqual(sortSteamGames(rows,'completion').map(game=>game.app_id),['2','3','4','1']);
});
test('reload keeps observing an in-flight cache claim without repeating provider work',()=>{
  const game={...games[0],achievement_refresh_due:false,achievement_state:'loading',achievement_refreshing:true};
  assert.equal(steamProgressPending(game),true);
  game.achievement_state='ready';
  assert.equal(steamProgressPending(game),true);
  game.achievement_refreshing=false;
  assert.equal(steamProgressPending(game),false);
  game.achievement_state='unavailable';
  assert.equal(steamProgressPending(game),false);
});
