import test from 'node:test';
import assert from 'node:assert/strict';
import {listFilterLabel} from '../src/lib/list-filter-label.ts';
test('every default sort keeps its selected name on initial render and restoration',()=>{
 for(const [value,label] of [['title','Title'],['score','Score'],['progress','Progress'],['updated','Recently updated']]){
  assert.equal(listFilterLabel(value,value,'Sort',label,true),label);
  assert.equal(listFilterLabel(value,'other','Sort',label,true),label);
 }
});
test('unselected filters keep their concise placeholder and active filters show their value',()=>{
 assert.equal(listFilterLabel('','','Genre','All genres'),'Genre');
 assert.equal(listFilterLabel('Action','','Genre','Action'),'Action');
 assert.equal(listFilterLabel('','','Favorites','All entries'),'Favorites');
 assert.equal(listFilterLabel('unknown','title','Sort',undefined,true),'Sort');
});
