import {editorStore,seedEditorPage,type EditorTitle} from './editor-store';
import {gameProgress} from './game-progress';
import {scoreLabel} from './tracker';
import {mountListCoverPreview} from './list-cover-preview';
import {mountListFilterControls} from './list-filter-controls';
import {compareGameListEntries,gameListQuery} from './game-list-data';

let listeners:AbortController|undefined;
export function mountGameList(){
  listeners?.abort();
  const root=document.querySelector<HTMLElement>('[data-game-list]');if(!root)return;
  listeners=new AbortController();const {signal}=listeners;
  document.addEventListener('astro:before-swap',()=>listeners?.abort(),{signal,once:true});
  if(root.dataset.owner==='true')seedEditorPage();
  let previewListeners:AbortController;
  const mountPreview=()=>{previewListeners?.abort();previewListeners=new AbortController();mountListCoverPreview(previewListeners.signal);};
  mountPreview();
  signal.addEventListener('abort',()=>previewListeners.abort(),{once:true});
  const counts=JSON.parse(root.dataset.statusCounts||'{}') as Record<string,number>;
  const hiddenBases=new Set<number>();
  const updateMembership=(row:HTMLElement,status:string|null)=>{
    const previous=row.dataset.entryStatus||null;
    if(previous!==status){if(previous)counts[previous]=(counts[previous]||0)-1;if(status)counts[status]=(counts[status]||0)+1;row.dataset.entryStatus=status||'';}
  };
  const updateCounts=()=>{
    root.dataset.statusCounts=JSON.stringify(counts);
    root.querySelectorAll<HTMLElement>('[data-list-status-count]').forEach(node=>node.textContent=String(node.dataset.listStatusCount==='all'?Object.values(counts).reduce((sum,value)=>sum+value,0):counts[node.dataset.listStatusCount!]||0));
  };
  const updateGroups=()=>{
    root.querySelectorAll<HTMLElement>('[data-game-group]').forEach(group=>group.hidden=!group.querySelector('.entry-row:not([hidden])'));
    root.querySelector<HTMLElement>('#list-empty')!.hidden=!!root.querySelector('.entry-row:not([hidden])');
  };
  const filters=root.querySelector<HTMLElement>('#list-filters')!;
  const controls=mountListFilterControls(filters,signal);
  const form=filters.querySelector<HTMLFormElement>('[data-list-filter-form]')!;
  const field=(name:string)=>form.elements.namedItem(name) as HTMLInputElement;
  const defaultSort=filters.querySelector<HTMLButtonElement>('#reset-filters')!.dataset.defaultSort||'title';
  let searchTimer:ReturnType<typeof setTimeout>|undefined;
  let request:AbortController|undefined,generation=0;
  const invalidate=()=>{generation++;request?.abort();};
  const submit=async()=>{
    clearTimeout(searchTimer);invalidate();
    const revision=generation;request=new AbortController();const requestSignal=request.signal;
    controls.sync(field('status').value||'all',defaultSort);
    const values=new URLSearchParams();for(const [key,value] of new FormData(form))if(typeof value==='string')values.set(key,value);
    const query=gameListQuery(values,defaultSort).query;
    const url=new URL(location.href);url.search=query.toString();
    const content=root.querySelector<HTMLElement>('.list-content')!;
    content.setAttribute('aria-busy','true');root.querySelector('[data-game-list-error]')?.remove();
    try{
      // Reuse the server's bounded, private-safe list renderer without replacing
      // the controls: typing, slider drags, mobile panel and focus stay intact.
      const response=await fetch(url,{signal:requestSignal,cache:'no-store'});
      if(!response.ok)throw new Error('Unable to filter this list.');
      const html=new DOMParser().parseFromString(await response.text(),'text/html');
      const next=html.querySelector<HTMLElement>('[data-game-list]'),nextContent=next?.querySelector('.list-content');
      if(!next||!nextContent)throw new Error('Unable to filter this list.');
      const nextCounts=JSON.parse(next.dataset.statusCounts||'{}');
      if(signal.aborted||revision!==generation)return;
      for(const key of Object.keys(counts))delete counts[key];Object.assign(counts,nextCounts);
      for(const key of ['status','favorites','sort'])root.dataset[key]=next.dataset[key];
      content.replaceChildren(...document.importNode(nextContent,true).childNodes);
      const seed=document.querySelector('#list-editor-entries'),nextSeed=html.querySelector('#list-editor-entries');
      if(seed&&nextSeed){seed.textContent=nextSeed.textContent;seedEditorPage();}
      reconcileRows();mountPreview();updateCounts();
      history.replaceState(history.state,'',url);
    }catch(error){
      if(signal.aborted||revision!==generation)return;
      const message=document.createElement('p');message.className='error-message';message.dataset.gameListError='';message.setAttribute('role','alert');
      message.append('Unable to filter this list. ');
      const retry=document.createElement('button');retry.type='button';retry.className='lab-text-button';retry.textContent='Retry';retry.addEventListener('click',submit,{signal});message.append(retry);content.prepend(message);
    }finally{if(!signal.aborted&&revision===generation){request=undefined;content.removeAttribute('aria-busy');}}
  };
  signal.addEventListener('abort',()=>{clearTimeout(searchTimer);invalidate();},{once:true});
  form.addEventListener('submit',event=>{event.preventDefault();void submit();},{signal});
  controls.sync(field('status').value||'all',defaultSort);
  filters.querySelectorAll<HTMLButtonElement>('[data-status]').forEach(button=>button.addEventListener('click',()=>{
    field('status').value=button.dataset.status==='all'?'':button.dataset.status!;submit();
  },{signal}));
  form.addEventListener('input',event=>{
    const input=event.target as HTMLInputElement;
    if(input.id==='year-filter')return;
    clearTimeout(searchTimer);invalidate();
    if(input.name==='q')searchTimer=setTimeout(submit,300);else void submit();
  },{signal});
  filters.querySelector('#year-filter')!.addEventListener('change',submit,{signal});
  filters.querySelector('#reset-filters')!.addEventListener('click',()=>{
    for(const name of ['q','genre','favorites','status','year'])field(name).value='';
    filters.querySelector<HTMLInputElement>('#year-filter')!.value='0';field('sort').value=defaultSort;submit();
  },{signal});
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
      row.querySelectorAll<HTMLElement>('[data-game-list-achievements]').forEach(achievement=>{achievement.textContent=progress.achievement||'—';achievement.dataset.known=String(progress.achievement!=null);if(achievement.classList.contains('game-inline-achievements'))achievement.setAttribute('aria-label',`Achievements ${progress.achievement||'unknown'}`);});
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
  const reconcileRows=()=>{if(root.dataset.owner==='true')root.querySelectorAll<HTMLElement>('[data-game-entry]').forEach(row=>{
    const title=editorStore.findSubject({kind:'game',catalogue_id:Number(row.dataset.gameEntry)});if(!title)return;
    const entry=title.entry,achievement=row.querySelector('[data-game-list-achievements]');
    if(!entry||entry.status!==row.dataset.entryStatus||Number(entry.score??-1)!==Number(row.dataset.score)||Number(entry.total_seconds??-1)!==Number(row.dataset.hours)||(achievement&&(gameProgress(entry).achievement||'—')!==achievement.textContent)||row.querySelector<HTMLElement>('[data-game-list-favorite]')!.hidden===!!title.favorite)paint(title);
  });};
  reconcileRows();
  document.addEventListener('anylist:editor-state',event=>{
    const title=(event as CustomEvent<EditorTitle>).detail;paint(title);
    // A read begun before an optimistic write/commit may carry an older private
    // snapshot. Restart it so it cannot seed that snapshot over the saved state.
    if(title.type==='game'&&request&&!request.signal.aborted)void submit();
  },{signal});
  root.addEventListener('click',async event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('[data-game-list-rate]');if(!button)return;
    const title=editorStore.findSubject({kind:'game',catalogue_id:Number(button.dataset.gameListRate)});if(!title)return;
    if(title.game_class==='expansion'){document.dispatchEvent(new CustomEvent('anylist:open-editor',{detail:{subject:title.subject,opener:button,title}}));return;}
    (window as any).anyListQuickRate?.({title:title.title,poster:title.poster,score:title.entry?.score,ratingMode:'manual',onScore:async(score:number|null)=>{await editorStore.write(title,{score});}});
  },{signal});
}
