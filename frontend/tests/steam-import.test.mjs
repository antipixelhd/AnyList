import assert from 'node:assert/strict';
import test from 'node:test';
import { defaultSteamDraft, sortSteamGames, steamSelected, steamProgressPending, steamStatus, filterSteamGames, reconcileSteamCards, chooseSteamStatus, steamAccentText, steamScrollAnchor } from '../src/lib/steam-import.ts';

const games = [
  {app_id:'1',name:'Alpha',playtime_minutes:30,achievement_count:null,achievement_total:null},
  {app_id:'2',name:'Beta',playtime_minutes:0,achievement_count:1,achievement_total:2},
  {app_id:'3',name:'Gamma',playtime_minutes:15,achievement_count:3,achievement_total:20},
  {app_id:'4',name:'Delta',playtime_minutes:0,achievement_count:0,achievement_total:2},
];
test('live search covers the full library, ignores accents/case and preserves draft choices', () => {
  const library = [...games, ...Array.from({length:100}, (_,i)=>({...games[0],app_id:String(i+10),name:`Other ${i}`})), {...games[0],app_id:'500',name:'Pokémon Mystery Dungeon'}];
  const draft = defaultSteamDraft(); draft.statuses['500']='playing'; draft.overrides['500']=true;
  assert.deepEqual(filterSteamGames(library,'  DUNGEON pokemon ').map(g=>g.app_id),['500']);
  assert.equal(filterSteamGames(library,'no such game').length,0);
  assert.equal(filterSteamGames(library,' ').length,library.length);
  assert.equal(library.length,105);
  assert.equal(steamStatus(library.at(-1),draft),'playing');
  assert.equal(steamSelected(library.at(-1),draft),true);
});
test('per-game status survives serialization and enrichment, with compatibility for older drafts', () => {
  const draft = defaultSteamDraft(); draft.import_status='paused'; draft.statuses['1']='playing'; draft.statuses['2']='planning';
  const restored=JSON.parse(JSON.stringify(draft));
  assert.equal(steamStatus({...games[0],achievement_count:5},restored),'playing');
  assert.equal(steamStatus(games[1],restored),'planning');
  assert.equal(steamStatus(games[2],restored),'paused');
  assert.equal(steamStatus(games[0],defaultSteamDraft()),'completed');
});
test('status editing preserves explicit card selection and saved dates', () => {
  const draft=defaultSteamDraft(),game=games[0];
  draft.overrides[game.app_id]=false; draft.start_dates[game.app_id]='2021-06-03';
  chooseSteamStatus(game,draft,'playing');
  assert.equal(steamSelected(game,draft),false);
  assert.equal(steamStatus(game,draft),'playing');
  assert.equal(draft.start_dates[game.app_id],'2021-06-03');
  draft.overrides[game.app_id]=true;
  chooseSteamStatus(game,draft,'planning');
  assert.equal(steamSelected(game,draft),true);
  assert.equal(steamStatus(game,draft),'planning');
});
test('profile accent labels keep readable contrast across light and dark profile colors',()=>{
  assert.equal(steamAccentText('#3db4f2'),'#0b1622');
  assert.equal(steamAccentText('#facc15'),'#0b1622');
  assert.equal(steamAccentText('#22c55e'),'#0b1622');
  assert.equal(steamAccentText('#1e3a8a'),'#ffffff');
  assert.equal(steamAccentText('#000000'),'#ffffff');
  assert.equal(steamAccentText('invalid'),'#0b1622');
});
test('selected-only reconciliation anchors a surviving neighbor instead of the excluded card',()=>{
  const node=(appId,top,options={})=>({dataset:{appId},isConnected:true,parentElement:{hidden:false},
    getBoundingClientRect:()=>({top,height:100}),...options});
  const removed=node('1',250),neighbor=node('2',450),later=node('3',650);
  const bounds={top:200,bottom:800};
  assert.equal(steamScrollAnchor([removed,neighbor,later],new Set(['2','3']),bounds).element,neighbor);
  assert.equal(steamScrollAnchor([node('0',100),removed,neighbor],new Set(['0','1','2']),bounds).element,removed);
  assert.equal(steamScrollAnchor([node('1',250,{parentElement:{hidden:true}}),neighbor],new Set(['1','2']),bounds).element,neighbor);
  assert.equal(steamScrollAnchor([removed,neighbor],new Set(),bounds),undefined);
});
test('filtering preserves mounted cards and their controls while sorting keeps keyboard order', () => {
  const grid={children:[],get firstElementChild(){return this.children[0]??null;},insertBefore(node,cursor){
    const old=this.children.indexOf(node); if(old>=0)this.children.splice(old,1);
    const target=cursor===null?this.children.length:this.children.indexOf(cursor);
    this.children.splice(target,0,node); node.parentElement=this;
  }};
  const card=id=>({id,hidden:false,parentElement:null,control:{status:'completed'},
    get nextElementSibling(){const rows=this.parentElement.children;return rows[rows.indexOf(this)+1]??null;}});
  const alpha=card('alpha'),beta=card('beta');
  reconcileSteamCards(grid,[alpha,beta]); beta.control.status='playing';
  for(let i=0;i<3;i++) {
    reconcileSteamCards(grid,[alpha]);
    assert.equal(beta.parentElement,grid); assert.equal(beta.hidden,true);
    assert.equal(grid.children.length,2);
    reconcileSteamCards(grid,[beta,alpha]);
    assert.equal(beta.hidden,false); assert.equal(beta.control.status,'playing');
    assert.deepEqual(grid.children.map(row=>row.id),['beta','alpha']);
  }
  reconcileSteamCards(grid,[]);
  assert(grid.children.every(row=>row.hidden));
  assert.equal(grid.children.length,2);
});
test('late software evidence defeats manual selection and stops progress polling',()=>{
  const draft=defaultSteamDraft();draft.overrides['431960']=true;
  const game={...games[0],app_id:'431960',mapping_state:'pending',achievement_state:'loading',achievement_refresh_due:true};
  assert.equal(steamSelected(game,draft),true);
  game.mapping_state='software';
  assert.equal(steamSelected(game,draft),false);
  assert.equal(steamProgressPending(game),false);
  assert.equal(draft.overrides['431960'],true);
});
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
