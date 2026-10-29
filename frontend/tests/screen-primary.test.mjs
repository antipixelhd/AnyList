import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

test('late screen headers fill missing slots and preserve usable text and aborted views',()=>{
  const original={textContent:'',hidden:true},tagline={textContent:'',hidden:true};
  const root={dataset:{mediaKind:'series'},querySelectorAll:()=>[],querySelector:selector=>({
    '[data-screen-original-title]':original,'[data-screen-tagline]':tagline,
  })[selector]};
  const context={prepareDetail:data=>data};
  const source=readFileSync(new URL('../src/lib/screen-primary.ts',import.meta.url),'utf8');
  runInNewContext(stripTypeScriptTypes(source.replace(/^import .*$/gm,'').replace(/export /g,''))+';globalThis.publish=publishScreenPrimary;',context);
  const data={id:3,type:'series',title:'Saved title',original_title:'Original title',tagline:'Recovered tagline'};
  const apply=(data,aborted=false)=>context.publish(root,data,3,{pending:false},{aborted});
  assert.equal(apply(data,true),false);
  assert.equal(original.textContent,'');
  assert.equal(apply({...data,id:4}),false);
  assert.equal(apply(data),true);
  assert.equal(original.textContent,'Original title');assert.equal(original.hidden,false);
  assert.equal(tagline.textContent,'Recovered tagline');assert.equal(tagline.hidden,false);
  apply({...data,original_title:'Replacement',tagline:'Replacement'});
  assert.equal(original.textContent,'Original title');assert.equal(tagline.textContent,'Recovered tagline');
  original.textContent='';original.hidden=true;
  apply({...data,original_title:'SAVED TITLE'});
  assert.equal(original.textContent,'');assert.equal(original.hidden,true);
});
