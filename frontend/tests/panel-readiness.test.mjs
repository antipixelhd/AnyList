import assert from 'node:assert/strict';
import test from 'node:test';
import {preparePanelStyles} from '../src/lib/panel-readiness.ts';

class Style{
  constructor(text,id){this.textContent=text;this.id=id;}
  getAttribute(name){return name==='data-vite-dev-id'?this.id:null;}
}
class Link extends EventTarget{
  constructor(href,sheet=null){super();this.href=href;this.sheet=sheet;}
  getAttribute(name){return name==='href'?this.href:null;}
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
