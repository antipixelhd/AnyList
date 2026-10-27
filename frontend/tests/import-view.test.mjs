import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import test from 'node:test';
const source=readFileSync(new URL('../src/scripts/streaming-import.ts',import.meta.url),'utf8');
const view=source.slice(source.indexOf('  function setWorkflow('),source.indexOf('  let uploadController',source.indexOf('  function setWorkflow(')));
function fixture(){
 let writes=0,enters=0,cancels=0;
 const node={setAttribute(){}};
 const workflow={firstElementChild:node,set innerHTML(value){writes++;this.firstElementChild=value?node:null;}};
 const context={workflow,currentViewHtml:'',currentViewKey:'',renderedView:null,reveal:()=>enters++,cancelUiMotion:()=>cancels++};
 runInNewContext(stripTypeScriptTypes(view)+';globalThis.update=setWorkflow;',context);
 return {update:context.update,writes:()=>writes,enters:()=>enters,cancels:()=>cancels};
}
test('loading feedback and optimistic fields update without replaying entrance motion',()=>{
 const f=fixture();f.update('loading queued','loading');f.update('loading queued','loading');
 assert.equal(f.writes(),1);assert.equal(f.enters(),1);
 f.update('loading 3 / 10','loading');assert.equal(f.enters(),1);
 f.update('review first title','review:1');assert.equal(f.enters(),2);
 f.update('review first title: completed','review:1');assert.equal(f.enters(),2);
 f.update('review second title','review:2');assert.equal(f.enters(),3);
 assert.equal(f.cancels(),4);
});
function heading(step,connectionId){
 const start=source.indexOf('  function heading('),end=source.indexOf('  function artwork(',start);
 const context={step,connectionId,serviceName:'Stremio',esc:value=>value};
 runInNewContext(stripTypeScriptTypes(source.slice(start,end))+';globalThis.html=heading("Review");',context);
 return context.html;
}
test('connection reviews expose three steps while setup stays unnumbered',()=>{
 for(const [internal,display] of [[3,1],[4,2],[5,3]]){
  const html=heading(internal,'6');assert.match(html,new RegExp('Step '+display+' of 3'));
  assert.equal((html.match(/<span class=/g)||[]).length,3);
 }
 const setup=heading(1,'6');assert.doesNotMatch(setup,/Step|streaming-step-bars/);
 const file=heading(3,undefined);assert.match(file,/Step 3 of 5/);
 assert.equal((file.match(/<span class=/g)||[]).length,5);
});


test('desktop progress rows stay reachable after Skip or Completed removes their review requirement',()=>{
 const series={id:'series',kind:'show',decision:{action:'confirm'},outcome:{status:'completed'},match:{candidate:{title:'Series'}},missingDate:true};
 const movie={id:'movie',kind:'movie',decision:{action:'confirm'},outcome:{status:'dropped'},match:{candidate:{title:'Movie'}},progress_reviewed:false};
 const automatic={id:'automatic',kind:'show',decision:{action:'confirm'},outcome:{status:'completed'},match:{candidate:{title:'Automatic'}}};
 const ids=[];
 const context={visibleItems:()=>[series,movie,automatic],isIncomplete:item=>item===movie&&!item.progress_reviewed,
  needsImportFinishDate:item=>item.missingDate,progressSequence:{ids,sync:values=>{for(const id of values)if(!ids.includes(id))ids.push(id)}},
  showingCompleted:false,mobileReview:{matches:false},heading:()=>'',showCard:item=>`<row>${item.id}:${item.outcome.status}</row>`};
 // A fully represented series may still need its completed date reviewed.
 series.completion_evidence={finish_date:null};
 const start=source.indexOf('  function progressView()'),end=source.indexOf('  function summaryView()',start);
 runInNewContext(stripTypeScriptTypes(source.slice(start,end))+';globalThis.render=progressView;',context);
 assert.match(context.render(),/<row>series:completed<\/row>/);
 series.outcome.status='skip';movie.outcome.status='completed';movie.progress_reviewed=true;
 const changed=context.render();
 assert.match(changed,/<row>series:skip<\/row>/);assert.match(changed,/<row>movie:completed<\/row>/);
 assert.doesNotMatch(changed,/<row>automatic:/);
 series.outcome.status='completed';series.missingDate=false;
 assert.match(context.render(),/<row>series:completed<\/row>/);
});
