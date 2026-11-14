import assert from 'node:assert/strict';
import test from 'node:test';
import {capturePanelStyles,preparePanelStyles} from '../src/lib/panel-readiness.ts';
import {createProfileSessionCache} from '../src/lib/profile-session-cache.ts';

class Style{
  constructor(text,id){this.textContent=text;this.id=id;}
  getAttribute(name){return name==='data-vite-dev-id'?this.id:null;}
  closest(){return this.noscript?{}:null;}
}
class Link extends EventTarget{
  constructor(href,sheet=null){super();this.href=href;this.sheet=sheet;}
  getAttribute(name){return name==='href'?this.href:null;}
  closest(){return this.noscript?{}:null;}
  remove(){this.removed=true;}
}
function fixture(styles=[],links=[],existing=[]){
  const nodes=[...existing];
  const document={head:{querySelectorAll:selector=>nodes.filter(node=>!node.removed&&(selector==='style'?node instanceof Style:node instanceof Link)),append:node=>nodes.push(node)},importNode:node=>node instanceof Style?new Style(node.textContent,node.id):new Link(node.href)};
  return {nodes,document,source:{querySelectorAll:selector=>selector==='style'?styles:links}};
}

test('development inline route styles are applied before the panel can be revealed and reused by identity',async()=>{
  const f=fixture([new Style('.game-entry-groups{display:block}','game-list.css')]);
  const signal=new AbortController().signal;
  await preparePanelStyles(f.source,'https://example.test/user/alex/games',signal,f.document);
  assert.equal(f.nodes[0].textContent,'.game-entry-groups{display:block}');
  await preparePanelStyles(f.source,'https://example.test/user/alex/games',signal,f.document);
  assert.equal(f.nodes.length,1);
});

test('styles inside parsed no-script fallbacks never enter the active document',async()=>{
  const fallback = Object.assign(new Style('.browse-loading-view {display:none!important}'),{noscript:true});
  const link = Object.assign(new Link('/fallback.css'),{noscript:true});
  const f = fixture([fallback,new Style('.browse-page{display:block}')],[link]);
  await preparePanelStyles(f.source,'https://example.test/browse',new AbortController().signal,f.document);
  assert.deepEqual(f.nodes.map(node=>node.textContent),['.browse-page{display:block}']);
});

test('an existing stylesheet link is not readiness evidence while it is still loading',async()=>{
  const existing=new Link('https://example.test/stats.css'),f=fixture([], [new Link('/stats.css')],[existing]);
  let ready=false;
  const pending=preparePanelStyles(f.source,'https://example.test/user/alex/stats',new AbortController().signal,f.document).then(()=>ready=true);
  await Promise.resolve();assert.equal(ready,false);assert.equal(f.nodes.length,1);
  existing.sheet={};existing.dispatchEvent(new Event('load'));await pending;assert.equal(ready,true);
});

test('concurrent panels share a stylesheet and both wait for its load event',async()=>{
  const f=fixture([], [new Link('/stats.css')]),signal=new AbortController().signal;
  const a=preparePanelStyles(f.source,'https://example.test/a',signal,f.document);
  const b=preparePanelStyles(f.source,'https://example.test/b',signal,f.document);
  assert.equal(f.nodes.length,1);f.nodes[0].sheet={};f.nodes[0].dispatchEvent(new Event('load'));await Promise.all([a,b]);
});

test('stylesheet failures and navigation cancellation reject readiness instead of exposing an unstyled panel',async()=>{
  const f=fixture([], [new Link('/stats.css')]),controller=new AbortController();
  const pending=preparePanelStyles(f.source,'https://example.test/a',controller.signal,f.document);
  controller.abort();await assert.rejects(pending,{name:'AbortError'});assert.equal(f.nodes[0].removed,true);
  const retry=preparePanelStyles(f.source,'https://example.test/a',new AbortController().signal,f.document);
  f.nodes[1].dispatchEvent(new Event('error'));await assert.rejects(retry,/Styles unavailable/);
});

test('fetched HTML never rewrites a live stylesheet with the same identity',async()=>{
  const live=new Style('.posters{padding:20px 24px}','stats.css');
  const f=fixture([new Style('.posters{padding:0}','stats.css')],[],[live]);
  await preparePanelStyles(f.source,'https://example.test/user/alex/list',new AbortController().signal,f.document);
  assert.equal(f.nodes.length,1);assert.equal(live.textContent,'.posters{padding:20px 24px}');
});

test('saved production CSS survives a cache round trip and restores before readiness',async()=>{
  const production=[new Style('.favorite-card{position:relative}.favorite-remove{position:absolute}'),new Style('.social-person-card{width:100px;height:100px}')];
  const link=new Link('/profile.css'),fallback=Object.assign(new Style('.social-page{display:none}'),{noscript:true});
  for(const node of [...production,fallback])node.outerHTML='<style>'+node.textContent+'</style>';
  link.outerHTML='<link rel="stylesheet" href="/profile.css">';
  const nodes=[...production,link,fallback];
  const source={querySelectorAll:selector=>nodes.filter(node=>selector.split(',').some(part=>part==='style'?node instanceof Style:part==='link[rel=stylesheet][href]'&&node instanceof Link))};
  const styles=capturePanelStyles(source);
  const data=new Map(),disk={getItem:key=>data.get(key),setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};
  const cache=createProfileSessionCache(disk,'42:true',()=>10);
  cache.set('favorites',{title:'Favorites',content:'<article class="favorite-card">Poster</article>',styles,checkedAt:10});cache.flush();
  const restored=createProfileSessionCache(disk,'42:true',()=>10).get('favorites');
  assert.match(restored.styles,/favorite-remove\{position:absolute\}/);
  assert.match(restored.styles,/social-person-card\{width:100px;height:100px\}/);
  assert.doesNotMatch(restored.styles,/display:none/);
  const decoded=[...restored.styles.matchAll(/<style>(.*?)<\/style>/g)].map(match=>new Style(match[1]));
  const target=fixture(decoded,[link]);let ready=false;
  const pending=preparePanelStyles(target.source,'https://example.test/user/alex/favorites',new AbortController().signal,target.document).then(()=>ready=true);
  await Promise.resolve();assert.equal(ready,false,'linked CSS must also finish before revealing the restored cards');
  target.nodes.find(node=>node instanceof Link).dispatchEvent(new Event('load'));await pending;
  assert.deepEqual(target.nodes.filter(node=>node instanceof Style).map(node=>node.textContent),production.map(node=>node.textContent));
  await preparePanelStyles(fixture(decoded).source,'https://example.test/user/alex/social',new AbortController().signal,target.document);
  assert.equal(target.nodes.filter(node=>node instanceof Style).length,2,'repeated restores do not duplicate production styles');
});
