import assert from 'node:assert/strict';
import test from 'node:test';
import {browseCardKey,browseCardHref,browseStatusLabel,expansionCard} from '../src/lib/browse-card-data.ts';
import {gameBrowseValues,gameBrowseIsCategories,gameBrowseRequest,gameBrowseHref,gamePlatformSelection,gameBrowseSnapshot} from '../src/lib/game-browse-data.ts';

test('restored Games windows retain earlier card data and the latest continuation',()=>{
  const rows=[{igdb_id:1},{igdb_id:2},{igdb_id:3}];
  const state={loading:false,failed:false,categories:false,values:'sort=popular',activeValues:'sort=popular',sections:[],rows,
    payload:{results:[rows[2]],cursor:'next-page',has_more:true}};
  const snapshot=gameBrowseSnapshot(state);
  assert.deepEqual(snapshot.payload.results,rows);
  assert.equal(snapshot.payload.cursor,'next-page');assert.equal(snapshot.payload.has_more,true);
  for(const pending of [{loading:true},{failed:true},{activeValues:'q=previous'},{payload:{verification_pending:true}},{payload:{verification_error:'unavailable'}}])
    assert.equal(gameBrowseSnapshot({...state,...pending}),null);
});

test('Games browse keys separate provider and catalogue identities and survive resolution',()=>{
  const remote={id:null,igdb_id:7,type:'game'};
  assert.equal(browseCardKey(remote),browseCardKey({...remote,id:50}));
  assert.notEqual(browseCardKey(remote),browseCardKey({id:7,type:'game'}));
  assert.notEqual(browseCardKey(remote),browseCardKey({id:7,type:'movie'}));
  assert.equal(browseCardHref(remote),'/discover-game?id=7');
  assert.equal(browseCardHref({...remote,id:50}),'/games/50');
  assert.equal(browseCardHref({id:50,type:'series'}),'/title/50');
  assert.equal(browseCardKey({id:50,type:'movie',release_event:'event:1'}),'event:1');
  assert.equal(browseStatusLabel('planning',true),'Planning');
  assert.equal(expansionCard({...remote,game_class:'expansion'}),true);
});

test('Games category, filter and timeline URLs keep their own query scope',()=>{
  const defaults=gameBrowseValues(new URLSearchParams('media_type=movie&provider=9&region=DE'));
  assert.equal(gameBrowseIsCategories(defaults),true);
  const selected=gameBrowseValues(new URLSearchParams('genres=igdb.genre:5&platforms=pc&min_metacritic=80'));
  assert.equal(gameBrowseIsCategories(selected),false);
  const request=new URL(gameBrowseRequest(selected,24,'token'),'https://example.test');
  assert.equal(request.searchParams.get('category'),'trending');
  assert.equal(request.searchParams.get('cursor'),'token');
  assert.equal(request.searchParams.get('platforms'),'pc');
  const most=gameBrowseValues(new URLSearchParams('sort=most_played&min_metacritic=80'));
  assert.equal(most.has('min_metacritic'),false);
  const timeline=gameBrowseValues(new URLSearchParams('sort=score&view=timeline&genres=igdb.genre:5'));
  assert.equal(timeline.get('sort'),'newest');
  const directTimeline=gameBrowseValues(new URLSearchParams('q=Dark&view=timeline'));
  assert.equal(gameBrowseValues(directTimeline).toString(),directTimeline.toString());
  assert.equal(gameBrowseValues(new URLSearchParams('q=Dark&sort=newest&view=timeline')).toString(),directTimeline.toString());
  assert.equal(new URL(gameBrowseRequest(timeline),'https://example.test').searchParams.has('view'),false);
  assert.equal(new URL(gameBrowseRequest(timeline),'https://example.test').searchParams.get('timeline'),'true');
  assert.equal(gameBrowseHref(defaults,{sort:'score'}),'/browse/games?sort=score');
});

test('Selected console devices refine their family while PC stays one logical platform',()=>{
  const groups=[{id:'pc',name:'PC',devices:[]},{id:'playstation',name:'PlayStation',devices:[{id:'igdb.platform:167',name:'PlayStation 5'},{id:'igdb.platform:48',name:'PlayStation 4'}]}];
  assert.deepEqual(gamePlatformSelection(['pc','playstation','igdb.platform:167'],groups).map(x=>x.name),['PC','PlayStation 5']);
});

test('Game release status and local day reach the timeline API without overwriting the year filter',()=>{
  const timeline=gameBrowseValues(new URLSearchParams('view=timeline&status=upcoming&platforms=pc&end=2028-12-31'));
  const request=new URL(gameBrowseRequest(timeline,24,'next','2027-03-07'),'https://example.test').searchParams;
  assert.equal(request.get('status'),'upcoming');
  assert.equal(request.get('today'),'2027-03-07');
  assert.equal(request.get('end'),'2028-12-31');
  assert.equal(request.get('platforms'),'pc');
  assert.equal(request.get('cursor'),'next');
  const grid=gameBrowseValues(new URLSearchParams('status=released'));
  assert.equal(gameBrowseIsCategories(grid),false);
  assert.equal(new URL(gameBrowseRequest(grid),'https://example.test').searchParams.get('category'),'newest');
});

test('Show all retains explicit game filters and stays out of title search',()=>{
  const values=gameBrowseValues(new URLSearchParams('view=timeline&status=upcoming&platforms=pc&unfiltered=true'));
  const request=new URL(gameBrowseRequest(values),'https://example.test').searchParams;
  assert.equal(request.get('unfiltered'),'true');
  assert.equal(request.get('status'),'upcoming');
  assert.equal(request.get('platforms'),'pc');
  assert.equal(gameBrowseIsCategories(gameBrowseValues(new URLSearchParams('unfiltered=true'))),false);
  assert.equal(gameBrowseValues(new URLSearchParams('q=Dark&unfiltered=true')).has('unfiltered'),false);
});
