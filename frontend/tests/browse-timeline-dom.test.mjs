import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';
import {timelineGroups} from '../src/lib/browse-timeline-data.ts';

class Element {
  children=[]; dataset={}; className=''; parent=null;
  append(...children) {
    for(const child of children){
      // Reattaching any existing node would risk scroll/focus state.
      assert.equal(child.parent,null,'pagination must never move an attached node');
      child.parent=this;this.children.push(child);
    }
  }
  contains(node){return this===node||this.children.some(child=>child.contains(node));}
  querySelectorAll(selector){
    const attribute=selector.startsWith('[data-')?selector.slice(6,-1).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase()):null;
    const matches=node=>attribute?attribute in node.dataset:node.className.split(' ').includes(selector.slice(1));
    return this.children.flatMap(child=>[...(matches(child)?[child]:[]),...child.querySelectorAll(selector)]);
  }
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}

test('chronological pagination appends across month/year boundaries without moving any existing card or heading',()=>{
  const source=readFileSync(new URL('../src/lib/browse-timeline.ts',import.meta.url),'utf8')
    .replace(/^import .*;\r?\n/gm,'').replace('export function','function');
  const context=vm.createContext({timelineGroups,setLocalDateTime(){},document:{createElement:()=>new Element()}});
  vm.runInContext(stripTypeScriptTypes(source),context);
  for(const ascending of [true,false]){
    const root=new Element(),items=new Map();
    const dates=ascending?['2026-10-01','2026-10-01','2026-10-02','2026-11-01','2027-01-01']:
      ['2027-01-02','2027-01-02','2027-01-01','2026-12-01','2026-11-01'];
    const cards=dates.map((release_date,index)=>{
      const card=new Element();card.dataset.browseCard='';card.focused=index===0;
      items.set(card,{title:String(index),release_date});return card;
    });
    context.appendBrowseTimeline(root,cards.slice(0,1),items,ascending);
    const firstYear=root.children[0],firstMonth=firstYear.querySelector('[data-timeline-month]');
    const firstHeading=firstYear.children[0];
    context.appendBrowseTimeline(root,cards.slice(1,4),items,ascending);
    context.appendBrowseTimeline(root,cards.slice(4),items,ascending);
    assert.deepEqual(root.querySelectorAll('[data-browse-card]'),cards);
    assert.equal(root.children[0],firstYear);
    assert.equal(firstYear.children[0],firstHeading);
    assert.equal(firstYear.querySelector('[data-timeline-month]'),firstMonth);
    assert.equal(cards[0].focused,true);
    assert.equal(root.querySelectorAll('[data-timeline-year]').length,2);
    assert.equal(root.querySelectorAll('[data-timeline-month]').length,3);
    context.appendBrowseTimeline(root,cards,items,ascending); // Repeat-safe attachment.
    assert.equal(root.querySelectorAll('[data-browse-card]').length,5);
  }
});
