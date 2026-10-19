import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';

test('header list shortcut filters once and scrolls to works while preserving modified navigation',()=>{
 const source=readFileSync(new URL('../src/lib/contributor-page.ts',import.meta.url),'utf8');
 const start=source.indexOf("  root.querySelector<HTMLAnchorElement>('.contributor-list-link')");
 const snippet=source.slice(start,source.indexOf("  root.querySelectorAll<HTMLButtonElement>('[data-contributor-view]')",start));
 let click,submitted=0,scrolled=[];const scope={checked:false},works={};
 const context={signal:new AbortController().signal,root:{querySelector:selector=>selector==='.contributor-list-link'?{addEventListener:(_,handler)=>click=handler}:works},form:{elements:{namedItem:()=>scope},requestSubmit:()=>submitted++},scrollToWorks:target=>scrolled.push(target)};
 vm.runInNewContext(stripTypeScriptTypes(snippet),context);
 const event=overrides=>({button:0,preventDefault(){this.prevented=true;},...overrides});
 const first=event();click(first);assert.equal(first.prevented,true);assert.equal(scope.checked,true);assert.equal(submitted,1);assert.equal(scrolled[0],works);
 click(event());assert.equal(submitted,1);assert.equal(scrolled[1],works);
 const modified=event({ctrlKey:true});click(modified);assert.equal(modified.prevented,undefined);assert.equal(scrolled.length,2);
});
