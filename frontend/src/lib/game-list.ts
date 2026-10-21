import {editorStore,seedEditorPage,type EditorTitle} from './editor-store';
import {gameProgress} from './game-progress';
import {scoreLabel} from './tracker';
import {reveal,dismiss} from './ui-motion';
import {mountListCoverPreview} from './list-cover-preview';
import {compareGameListEntries} from './game-list-data';

let listeners:AbortController|undefined;
export function mountGameList(){
  listeners?.abort();
  const root=document.querySelector<HTMLElement>('[data-game-list]');if(!root)return;
  listeners=new AbortController();const {signal}=listeners;
  document.addEventListener('astro:before-swap',()=>listeners?.abort(),{signal,once:true});
  if(root.dataset.owner==='true')seedEditorPage();
  mountListCoverPreview(signal);
  const counts=JSON.parse(root.dataset.statusCounts||'{}') as Record<string,number>;
  const hiddenBases=new Set<number>();
  const updateMembership=(row:HTMLElement,status:string|null)=>{
    const previous=row.dataset.entryStatus||null;
    if(previous!==status){if(previous)counts[previous]=(counts[previous]||0)-1;if(status)counts[status]=(counts[status]||0)+1;row.dataset.entryStatus=status||'';}
  };
  const updateCounts=()=>root.querySelectorAll<HTMLElement>('[data-game-status-count]').forEach(node=>node.textContent=String(node.dataset.gameStatusCount==='all'?Object.values(counts).reduce((sum,value)=>sum+value,0):counts[node.dataset.gameStatusCount!]||0));
  const updateGroups=()=>root.querySelectorAll<HTMLElement>('[data-game-group]').forEach(group=>group.hidden=!group.querySelector('.entry-row:not([hidden])'));
  const toggle=root.querySelector<HTMLButtonElement>('[data-game-filter-toggle]')!,panel=root.querySelector<HTMLElement>('#game-list-filter-panel')!,filters=root.querySelector<HTMLElement>('.filters')!;
  const mobile=matchMedia('(max-width:650px)');
  const close=()=>{toggle.setAttribute('aria-expanded','false');panel.inert=mobile.matches;void dismiss(panel).then(current=>{if(current&&mobile.matches)filters.classList.remove('open');});};
  panel.inert=mobile.matches;
  toggle.addEventListener('click',()=>{if(toggle.getAttribute('aria-expanded')==='true'){close();return;}filters.classList.add('open');panel.inert=false;toggle.setAttribute('aria-expanded','true');reveal(panel);},{signal});
  mobile.addEventListener('change',()=>{filters.classList.remove('open');toggle.setAttribute('aria-expanded','false');panel.inert=mobile.matches;reveal(panel);},{signal});
  root.addEventListener('keydown',event=>{if(event.key==='Escape'&&filters.classList.contains('open')){event.preventDefault();close();toggle.focus();}},{signal});
  const paint=(title:EditorTitle)=>{
    if(root.dataset.owner!=='true'||title.type!=='game')return;
    if(!['expansion','dlc'].includes(title.game_class||'')){
      if(title.entry)hiddenBases.delete(title.id!);else hiddenBases.add(title.id!);
      root.querySelectorAll<HTMLElement>(`[data-game-base="${title.id}"]`).forEach(child=>{
        const cached=editorStore.findSubject({kind:'game',catalogue_id:Number(child.dataset.gameEntry)});
        updateMembership(child,title.entry?cached?.entry?.status||null:null);
        child.hidden=!title.entry||!cached?.entry||(!!root.dataset.status&&cached.entry.status!==root.dataset.status);
      });
    }
    const row=root.querySelector<HTMLElement>(`[data-game-entry="${title.id}"]`);if(!row){updateCounts();updateGroups();return;}
    const entry=title.entry;
    updateMembership(row,entry?.status||null);
    row.hidden=!entry||hiddenBases.has(Number(row.dataset.gameBase))||(!!root.dataset.status&&entry.status!==root.dataset.status)||(root.dataset.favorites==='true'&&!title.favorite);
    if(entry){
      row.dataset.score=String(entry.score??-1);row.dataset.hours=String(entry.total_seconds??-1);row.dataset.updated=entry.updated_at||'';
      row.querySelector('[data-score-label]')!.textContent=scoreLabel(entry.score);
      const progress=gameProgress(entry);row.querySelector('[data-game-list-hours]')!.textContent=progress.hours||'—';
      const achievement=row.querySelector<HTMLElement>('[data-game-list-achievements]');if(achievement){achievement.textContent=progress.achievement||'—';achievement.dataset.known=String(progress.achievement!=null);}
      row.querySelector<HTMLElement>('[data-game-list-favorite]')!.hidden=!title.favorite;
      root.querySelector(`[data-game-group="${entry.status}"] .entry-table`)?.append(row);
    }
    root.querySelectorAll<HTMLElement>('[data-game-group] .entry-table').forEach(table=>{
      const rows=[...table.querySelectorAll<HTMLElement>('.entry-row')];
      const value=(row:HTMLElement)=>({id:Number(row.dataset.gameEntry),title:row.dataset.title||'',score:Number(row.dataset.score),total_seconds:Number(row.dataset.hours),updated_at:row.dataset.updated});
      rows.sort((left,right)=>compareGameListEntries(value(left),value(right),root.dataset.sort||'title')).forEach(row=>table.append(row));
    });
    updateCounts();
    updateGroups();
  };
  if(root.dataset.owner==='true')root.querySelectorAll<HTMLElement>('[data-game-entry]').forEach(row=>{
    const title=editorStore.findSubject({kind:'game',catalogue_id:Number(row.dataset.gameEntry)});if(!title)return;
    const entry=title.entry,achievement=row.querySelector('[data-game-list-achievements]');
    if(!entry||entry.status!==row.dataset.entryStatus||Number(entry.score??-1)!==Number(row.dataset.score)||Number(entry.total_seconds??-1)!==Number(row.dataset.hours)||(achievement&&(gameProgress(entry).achievement||'—')!==achievement.textContent))paint(title);
  });
  document.addEventListener('anylist:editor-state',event=>paint((event as CustomEvent<EditorTitle>).detail),{signal});
  root.addEventListener('click',async event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('[data-game-list-rate]');if(!button)return;
    const title=editorStore.findSubject({kind:'game',catalogue_id:Number(button.dataset.gameListRate)});if(!title)return;
    if(title.game_class==='expansion'){document.dispatchEvent(new CustomEvent('anylist:open-editor',{detail:{subject:title.subject,opener:button,title}}));return;}
    (window as any).anyListQuickRate?.({title:title.title,poster:title.poster,score:title.entry?.score,ratingMode:'manual',onScore:async(score:number|null)=>{await editorStore.write(title,{score});}});
  },{signal});
}
