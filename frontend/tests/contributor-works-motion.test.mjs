import test from 'node:test';
import assert from 'node:assert/strict';
import {createContributorWorksMotion,orderContributorNodes} from '../src/lib/contributor-works-motion.ts';

test('chronological insertion leaves correctly ordered nodes attached, including the focused card',()=>{
  const a={},b={},c={},fresh={},parent={children:[a,b,c],moves:[],
    get firstElementChild(){return this.children[0]||null;},
    insertBefore(node,cursor){this.moves.push(node);this.children=this.children.filter(child=>child!==node);this.children.splice(cursor?this.children.indexOf(cursor):this.children.length,0,node);},
  };
  for(const node of [a,b,c,fresh])Object.defineProperty(node,'nextElementSibling',{get:()=>parent.children[parent.children.indexOf(node)+1]||null});
  orderContributorNodes(parent,[a,fresh,b,c]);
  assert.deepEqual(parent.children,[a,fresh,b,c]);assert.deepEqual(parent.moves,[fresh]);
  orderContributorNodes(parent,[a,fresh,b,c]);assert.deepEqual(parent.moves,[fresh]);
  orderContributorNodes(parent,[c,a,fresh,b]);assert.deepEqual(parent.children,[c,a,fresh,b]);
  assert.deepEqual(parent.moves,[fresh,c]);
});

function fixture(run){
  const original={matchMedia:globalThis.matchMedia,innerHeight:globalThis.innerHeight};
  const reduced=new EventTarget();reduced.matches=false;
  globalThis.matchMedia=()=>reduced;globalThis.innerHeight=800;
  const controller=new AbortController(),animations=[],settled=[];
  const card=(top,left=0)=>({isConnected:true,top,left,
    querySelectorAll:()=>[{setAttribute:name=>settled.push(name)}],
    getBoundingClientRect(){return {top:this.top,bottom:this.top+120,left:this.left,width:100,height:120};},
    animate(frames,options){const animation=new EventTarget();Object.assign(animation,{frames,options,cancelled:false,cancel(){this.cancelled=true;}});animations.push(animation);return animation;},
  });
  const a=card(30),b=card(180),offscreen=card(1000),cards=[a,b,offscreen];
  const motion=createContributorWorksMotion({querySelectorAll:()=>cards},controller.signal);
  try{run({motion,a,b,offscreen,cards,card,animations,settled,reduced,controller});}
  finally{controller.abort();Object.assign(globalThis,original);}
}

test('only displaced visible existing cards slide; new and stationary cards never refade',()=>fixture(f=>{
  const finish=f.motion.capture();f.b.top+=150;f.b.left=120;f.offscreen.top=300;f.cards.push(f.card(180));finish();
  assert.equal(f.animations.length,1);
  assert.deepEqual(f.animations[0].frames,[{transform:'translate(-120px, -150px)'},{transform:'translate(0, 0)'}]);
  assert.equal(f.animations[0].options.duration,280);assert.equal(f.settled.length,3);
}));

test('repeated publications cancel previous movement and navigation cancels the current one',()=>fixture(f=>{
  let finish=f.motion.capture();f.b.top+=50;finish();const first=f.animations[0];
  finish=f.motion.capture();assert.equal(first.cancelled,true);f.b.top+=50;finish();
  const second=f.animations[1];f.controller.abort();assert.equal(second.cancelled,true);
  finish();assert.equal(f.animations.length,2);
}));

test('a visible card pushed below the viewport moves smoothly out of view',()=>fixture(f=>{
  f.b.top=650;const finish=f.motion.capture();f.b.top=850;finish();
  assert.equal(f.animations.length,1);
  assert.deepEqual(f.animations[0].frames,[{transform:'translate(0px, -200px)'},{transform:'translate(0, 0)'}]);
}));

test('reduced motion still settles CSS entrances and cancels movement immediately',()=>fixture(f=>{
  const finish=f.motion.capture();f.b.top+=50;finish();
  f.reduced.matches=true;f.reduced.dispatchEvent(new Event('change'));
  assert.equal(f.animations[0].cancelled,true);
  const next=f.motion.capture();f.b.top+=50;next();
  assert.equal(f.animations.length,1);assert.equal(f.settled.length,6);
}));
