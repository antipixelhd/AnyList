import test from 'node:test';
import assert from 'node:assert/strict';
import {gameAgeRating,hasSteamRequirements,requirementRows} from '../src/lib/game-info-data.ts';
test('country prefers a named board and fallback retains its actual region',()=>{
  const info={steam_app_id:'42',age_ratings:[{board:'ESRB',rating:'Mature 17+',region:'United States / Canada',source:'steam',descriptors:['Violence']},{board:'USK',rating:'16',region:'Germany',source:'steam',descriptors:[]}]};
  assert.equal(gameAgeRating(info,'DE').label,'USK 16');assert.equal(gameAgeRating(info,'US').label,'ESRB Mature 17+');
  assert.match(gameAgeRating(info,'JP').detail,/United States \/ Canada/);assert.equal(gameAgeRating(null,'DE'),null);
});
test('requirements tab needs verified Steam PC specs and excludes console-only releases',()=>{
  const requirements={source:'steam',platform:'PC',minimum:[{label:'Memory',value:'12 GB RAM'}],recommended:[]};
  const info={steam_app_id:'42',requirements};
  assert.equal(hasSteamRequirements(info,['PC (Microsoft Windows)','PlayStation 5']),true);
  assert.equal(hasSteamRequirements(info,['Steam']),true);
  assert.equal(hasSteamRequirements(info,[]),true);
  assert.equal(hasSteamRequirements(info,['PlayStation 5']),false);
  assert.equal(hasSteamRequirements({...info,steam_app_id:null},['PC']),false);
  assert.equal(hasSteamRequirements({...info,requirements:{...requirements,minimum:[]}},['PC']),false);
  assert.equal(hasSteamRequirements({...info,requirements:{...requirements,source:'other'}},['PC']),false);
});
test('requirements omit absent tiers without requiring both or turning other platforms into PC',()=>{
  assert.deepEqual(requirementRows(null,'minimum'),[]);
  const row={label:'Memory',value:'12 GB RAM'};
  assert.deepEqual(requirementRows({requirements:{platform:'PC',minimum:[row],recommended:[]}},'minimum'),[row]);
  assert.deepEqual(requirementRows({requirements:{platform:'Mac',minimum:[row]}},'minimum'),[]);
});
