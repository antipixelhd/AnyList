import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';
import test from 'node:test';
import {expansionCard,browseCardCanEdit,browseCardQuickActions} from '../src/lib/browse-card-data.ts';

const source=readFileSync(new URL('../src/lib/browse-card-actions.ts',import.meta.url),'utf8')
  .replace(/^import .*;\r?\n/gm,'').replaceAll('export ','');

function harness(type, action, fail=false, extra={}) {
  const handlers={},documentHandlers={},events=[],writes=[],failures=[],reads=[],updates=[];
  const item={id:7,type,title:'Title',list_status:null,...extra};
  const title={...item,subject:type==='game'?{kind:'game',catalogue_id:7}:undefined,entry:null};
  const image={currentSrc:'/poster.jpg'};
  const card={querySelector:()=>image};
  const button={disabled:false,dataset:{browseAction:action},closest:()=>card};
  const document={addEventListener:(name,callback)=>documentHandlers[name]=callback,dispatchEvent:event=>events.push(event)};
  const context=vm.createContext({document,window:{anyListQuickRate:options=>events.push(options)},
    CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}},
    setTimeout,clearTimeout,localToday:()=> '2026-10-28',expansionCard,browseCardCanEdit,browseCardQuickActions,
    showActionFailure:(...args)=>failures.push(args),
    editorStore:{
      get(value){assert.notEqual(value.type,'game','Games require a canonical subject');return title;},
      async loadSubject(subject){reads.push(subject);if(fail)throw new Error('Unavailable');return title;},
      warmArtwork(){},async write(value,patch){writes.push([value,patch]);return patch;},
    },
  });
  vm.runInContext(stripTypeScriptTypes(source)+'\nglobalThis.bind=bindBrowseCardActions;',context);
  const controller=new AbortController();
  context.bind({addEventListener:(name,callback)=>handlers[name]=callback},new Map([[card,item]]),controller.signal,
    updated=>{updates.push(updated);Object.assign(item,updated);},()=>{});
  return {click:()=>handlers.click({target:{closest:()=>button}}),
    editorState:detail=>documentHandlers['anylist:editor-state']({detail}),title,button,events,writes,failures,reads,updates};
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

test('expansion hover actions require the base and write to the expansion identity',async()=>{
  for(const action of ['edit','plan','watch','rate']){
    const blocked=harness('game',action,false,{game_class:'expansion',base_listed:false});
    await blocked.click();
    assert.equal(blocked.reads.length,0);
    assert.equal(blocked.events.length,0);
    assert.equal(blocked.writes.length,0);
    const allowed=harness('game',action,false,{game_class:'expansion',base_listed:true});
    await allowed.click();
    assert.equal(allowed.failures.length,0);
    assert.equal(allowed.reads[0].catalogue_id,7);
    if(action==='edit')assert.equal(allowed.events[0].detail.subject.catalogue_id,7);
    else{
      if(action==='rate')await allowed.events[0].onScore(9);
      assert.equal(allowed.writes[0][0],allowed.title);
      assert.equal(allowed.writes[0][1].status,action==='watch'?'playing':action==='rate'?'completed':'planning');
    }
  }
  const unknown=harness('game','watch',false,{game_class:'expansion'});
  await unknown.click();
  assert.equal(unknown.events[0].type,'anylist:open-editor');
  assert.equal(unknown.writes.length,0);
});

test('base-game changes update expansion controls without an extra read',()=>{
  const h=harness('game','edit',false,{game_class:'expansion',base_work_id:12,base_listed:true,list_status:'completed',score:9});
  h.editorState({id:12,type:'game',entry:null});
  assert.equal(h.updates.at(-1).base_listed,false);
  assert.equal(h.updates.at(-1).list_status,null);
  assert.equal(h.updates.at(-1).score,null);
  h.editorState({id:12,type:'game',entry:{status:'planning'}});
  assert.equal(h.updates.at(-1).base_listed,true);
  assert.equal(h.reads.length,0);
});
