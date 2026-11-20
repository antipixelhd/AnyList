import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

test('late screen copy fills Information while preserving the mounted hero',()=>{
  const original={textContent:'',hidden:true},tagline={textContent:'',hidden:true};
  const children=[];
  const list={children,get firstChild(){return children[0]||null;},insertBefore(node,anchor){
    const index=children.indexOf(anchor);children.splice(index<0?children.length:index,0,node);
  },removeAttribute(){}};
  const root={dataset:{mediaKind:'series'},querySelectorAll:()=>[list],querySelector:selector=>({
    '[data-screen-original-title]':original,'[data-screen-tagline]':tagline,
  })[selector]};
  const createElement=()=>({dataset:{},children:[],append(...nodes){this.children.push(...nodes);},
    replaceWith(next){children.splice(children.indexOf(this),1,next);},remove(){children.splice(children.indexOf(this),1);}});
  const context={prepareDetail:data=>({...data,genres:[]}),detailInformationRows:()=>[],document:{createElement}};
  const source=readFileSync(new URL('../src/lib/screen-primary.ts',import.meta.url),'utf8');
  runInNewContext(stripTypeScriptTypes(source.replace(/^import .*$/gm,'').replace(/export /g,''))+';globalThis.publish=publishScreenPrimary;',context);
  const data={id:3,type:'series',title:'Saved title',original_title:'Original title',tagline:'Recovered tagline'};
  const apply=(data,aborted=false)=>context.publish(root,data,3,{pending:false},{aborted});
  assert.equal(apply(data,true),false);
  assert.equal(apply({...data,id:4}),false);
  assert.equal(children.length,0);
  assert.equal(apply(data),true);
  assert.equal(original.textContent,'');assert.equal(original.hidden,true);
  assert.equal(tagline.textContent,'');assert.equal(tagline.hidden,true);
  assert.deepEqual(children.map(row=>[row.dataset.detailFact,row.children[1].textContent]),[
    ['Original title','Original title'],['Tagline','Recovered tagline'],
  ]);
  original.textContent='Original from the page';
  original.hidden=false;
  tagline.textContent='Tagline from the page';
  tagline.hidden=false;
  apply(data);
  assert.equal(original.textContent,'Original from the page');
  assert.equal(tagline.textContent,'Tagline from the page');
  assert.equal(children.length,0);
});
