import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';
import test from 'node:test';

const source=readFileSync(new URL('../src/lib/browse-card-actions.ts',import.meta.url),'utf8')
  .replace(/^import .*;\r?\n/gm,'').replaceAll('export ','');

function harness(type, action, fail=false) {
  const handlers={},events=[],writes=[],failures=[],reads=[];
  const item={id:7,type,title:'Title',list_status:null};
  const title={...item,subject:type==='game'?{kind:'game',catalogue_id:7}:undefined,entry:null};
  const image={currentSrc:'/poster.jpg'};
  const card={querySelector:()=>image};
  const button={disabled:false,dataset:{browseAction:action},closest:()=>card};
  const document={addEventListener(){},dispatchEvent:event=>events.push(event)};
  const context=vm.createContext({document,window:{anyListQuickRate:options=>events.push(options)},
    CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}},
    setTimeout,clearTimeout,localToday:()=> '2026-10-28',expansionCard:()=>false,
    showActionFailure:(...args)=>failures.push(args),
    editorStore:{
      get(value){assert.notEqual(value.type,'game','Games require a canonical subject');return title;},
      async loadSubject(subject){reads.push(subject);if(fail)throw new Error('Unavailable');return title;},
      warmArtwork(){},async write(value,patch){writes.push([value,patch]);return patch;},
    },
  });
  vm.runInContext(stripTypeScriptTypes(source)+'\nglobalThis.bind=bindBrowseCardActions;',context);
  const controller=new AbortController();
  context.bind({addEventListener:(name,callback)=>handlers[name]=callback},new Map([[card,item]]),controller.signal,()=>{},()=>{});
  return {click:()=>handlers.click({target:{closest:()=>button}}),title,button,events,writes,failures,reads};
}

test('game and movie cards open editors, plan, start and rate through their own identities',async()=>{
  for(const type of ['game','movie'])for(const action of ['edit','plan','watch','rate']){
    const h=harness(type,action);
    await h.click();
    assert.equal(h.failures.length,0);
    assert.equal(h.button.disabled,false);
    assert.equal(h.reads.length,type==='game'?1:0);
    if(type==='game')assert.equal(h.reads[0].catalogue_id,7);
    if(action==='edit')assert.equal(h.events[0].detail.title,h.title);
    else if(action==='rate')await h.events[0].onScore(8);
    else assert.equal(h.writes[0][1].status,action==='plan'?'planning':type==='game'?'playing':'watching');
    if(action==='rate')assert.equal(h.writes[0][0],h.title);
  }
});

test('failed game resolution reports the action failure and restores the button',async()=>{
  const h=harness('game','edit',true);
  await h.click();
  assert.equal(h.failures.length,1);
  assert.equal(h.failures[0][0],'add');
  assert.equal(h.button.disabled,false);
});
