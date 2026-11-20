import test from 'node:test';
import assert from 'node:assert/strict';
import {detailSources,publishDetailSources} from '../src/lib/detail-sources.ts';

test('Steam snapshot and identity links share one canonical label and URL',()=>{
  assert.deepEqual(detailSources([
    {name:'STEAM',url:'http://store.steampowered.com/app/42/Game/?utm_source=igdb'},
    {name:'steam',url:'https://store.steampowered.com/app/42'},
    {name:'Steam',url:'https://store.steampowered.com/app/42/'},
    {name:'IGDB',url:'https://www.igdb.com/games/example'},
  ]),[{name:'Steam',url:'https://store.steampowered.com/app/42/'},{name:'IGDB',url:'https://www.igdb.com/games/example'}]);
});

test('source links reject unsafe URLs and retain distinct destinations',()=>{
  assert.deepEqual(detailSources([{name:'Bad',url:'javascript:alert(1)'},{name:'Bad',url:'broken'}]),[]);
  assert.equal(detailSources([{name:'Steam',url:'https://store.steampowered.com/app/42/'},
    {name:'Steam',url:'https://store.steampowered.com/app/43/'}]).length,2);
});

test('a Steam-supplied critic URL is labelled for its actual destination',()=>{
  assert.deepEqual(detailSources([{name:'STEAM',url:'https://www.metacritic.com/game/example'}]),
    [{name:'Metacritic',url:'https://www.metacritic.com/game/example'}]);
});

test('game links use the requested order and derive artwork links without a lookup',()=>{
  const official={name:'Official website',url:'https://example.com/game'};
  const steam={name:'Steam',url:'https://store.steampowered.com/app/42/'};
  const igdb={name:'IGDB',url:'https://www.igdb.com/games/example'};
  const critic={name:'STEAM',url:'https://www.metacritic.com/game/example'};
  assert.deepEqual(detailSources([critic,igdb,steam,official],true),[official,steam,igdb,
    {name:'SteamGridDB · Art',url:'https://www.steamgriddb.com/steam/42'}]);
  assert.deepEqual(detailSources([critic,igdb],true),[igdb]);
});

test('merged games render at most one link per source, including derived artwork',()=>{
  const rows=[
    {name:'Official website',url:'https://example.com/main'},
    {name:'Official website',url:'https://example.com/multiplayer'},
    {name:'Steam',url:'https://store.steampowered.com/app/202970/'},
    {name:'Steam',url:'https://store.steampowered.com/app/202990/'},
    {name:'IGDB',url:'https://www.igdb.com/games/main'},
    {name:'IGDB',url:'https://www.igdb.com/games/multiplayer'},
  ];
  assert.deepEqual(detailSources(rows,true),[rows[0],rows[2],rows[4],
    {name:'SteamGridDB · Art',url:'https://www.steamgriddb.com/steam/202970'}]);
});

test('late ratings and primary updates reconcile links without losing the other section',()=>{
  const previous=globalThis.document;
  globalThis.document={createElement:()=>({append(){}})};
  try{
    const nav={hidden:true,children:[],querySelectorAll(){return this.children;},replaceChildren(...rows){this.children=rows;}};
    const root={querySelector:selector=>selector==='[data-detail-sources]'?nav:null};
    const steam={name:'Steam',url:'https://store.steampowered.com/app/42/'};
    const critic={name:'Metacritic',url:'https://www.metacritic.com/game/example'};
    publishDetailSources(root,[critic],'ratings');assert.equal(nav.hidden,false);
    publishDetailSources(root,[steam]);assert.deepEqual(nav.children.map(row=>row.textContent),['Steam','Metacritic']);
    publishDetailSources(root,[steam,steam],'ratings');assert.equal(nav.children.length,1);
    publishDetailSources(root,[]);assert.equal(nav.children.length,1);
    publishDetailSources(root,[],'ratings');assert.equal(nav.hidden,true);
  }finally{globalThis.document=previous;}
});
