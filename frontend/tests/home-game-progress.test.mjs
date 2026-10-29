import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {runInNewContext} from 'node:vm';
import {gameProgress} from '../src/lib/game-progress.ts';
const source=readFileSync(new URL('../src/lib/home-game-progress.ts',import.meta.url),'utf8');
const {paintHomeGameProgress}=runInNewContext(stripTypeScriptTypes(source).replace(/^import .*;$/gm,'').replace('export function','function')+';({paintHomeGameProgress})',
  {gameProgress,scoreLabel:score=>String(Number(score.toFixed(1)))});

test('game shelf follows optimistic edits and rollback without replacing cards or colliding with series ids',()=>{
  const label={textContent:''},otherLabel={textContent:'other'};
  const card={dataset:{gamePlayingCard:'7'},hidden:false,querySelector:()=>label};
  const other={dataset:{gamePlayingCard:'8'},hidden:false,querySelector:()=>otherLabel};
  const shelf={dataset:{showAchievements:'false'},querySelectorAll:()=>[card,other]};
  const title={id:7,type:'game',entry:{status:'playing',total_seconds:7200,score:8,achievements:{mode:'percentage',percentage:50}}};
  paintHomeGameProgress(shelf,title);
  assert.equal(label.textContent,'2h · 8 / 10');
  assert.equal(otherLabel.textContent,'other');
  paintHomeGameProgress(shelf,{...title,entry:{...title.entry,status:'completed'}});
  assert.equal(card.hidden,true);
  paintHomeGameProgress(shelf,title);
  assert.equal(card.hidden,false);
  paintHomeGameProgress(shelf,{id:7,type:'series',entry:null});
  assert.equal(card.hidden,false);
  shelf.dataset.showAchievements='true';paintHomeGameProgress(shelf,title);
  assert.match(label.textContent,/50%/);
  paintHomeGameProgress(shelf,{...title,entry:null});assert.equal(card.hidden,true);
});
