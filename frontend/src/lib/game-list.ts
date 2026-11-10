import {initializeEntryAnnotations} from './entry-annotations';
import {editorStore,type EditorTitle} from './editor-store';
import {gameProgress} from './game-progress';
import {scoreLabel} from './tracker';
import {mountListCoverPreview} from './list-cover-preview';
import {mountListFilterControls} from './list-filter-controls';
import {gameListQuery,gameListEditorTitle} from './game-list-data';
import {gameListPageSize,gameListPages,GameListGeometry} from './game-list-window';
import {panelActive,panelUrl,setPanelUrl} from './profile-panel';

type Page={offset:number;total:number;results:Record<string,any>[];group_counts:Record<string,number>;status_counts:Record<string,number>;genres:string[];years:string[]};
type Cached={data:Page;rows:Map<number,HTMLElement>};
export function initializeGameList(panel:HTMLElement,lifetime:AbortSignal){
  initializeEntryAnnotations();
  const candidate=panel.querySelector<HTMLElement>('[data-game-list]');if(!candidate)return;
  const root:HTMLElement=candidate;
  const listeners=new AbortController(),{signal}=listeners;
  lifetime.addEventListener('abort',()=>listeners.abort(),{once:true});
  const owner=root.dataset.owner==='true';
  if(owner)editorStore.scope(document.body.dataset.cacheUser||'anonymous');
  const filters=root.querySelector<HTMLElement>('#list-filters')!;
  const controls=mountListFilterControls(filters,signal);
  const form=filters.querySelector<HTMLFormElement>('[data-list-filter-form]')!;
  const field=(name:string)=>form.elements.namedItem(name) as HTMLInputElement;
  const defaultSort=filters.querySelector<HTMLButtonElement>('#reset-filters')!.dataset.defaultSort||'title';
  const groups=[...root.querySelectorAll<HTMLElement>('[data-game-window]')];
  let total=Number(root.dataset.gameTotal),counts=JSON.parse(root.dataset.gameCounts||'{}') as Record<string,number>;
  let query=gameListQuery(new URL(panelUrl(panel)).searchParams,defaultSort).query;
  let epoch=0,frame=0,searchTimer:ReturnType<typeof setTimeout>|undefined,retryTimer:ReturnType<typeof setTimeout>|undefined;
  let previousY=scrollY,previousTime=performance.now(),ahead=3200,dirty=false,refreshing=false;
  let requests=new AbortController(),preview=new AbortController();
  const cache=new Map<number,Cached>(),flights=new Set<number>(),wanted=new Set<number>(),blocked=new Set<number>(),retained=new Map<number,HTMLElement>();
  const layouts=new Map<string,GameListGeometry>();
  let anchor:{row:HTMLElement;top:number}|undefined;
  const rowSizes=new ResizeObserver(entries=>{
    if(!panelActive(panel))return;
    let changed=false;
    for(const entry of entries){
      const row=entry.target as HTMLElement,group=row.parentElement;
      if(!row.isConnected||!group?.dataset.gameWindow)continue;
      const layout=layouts.get(group.dataset.gameWindow);if(!layout)continue;
      const measured=layout.measure(Number(row.style.getPropertyValue('--game-row')),row.getBoundingClientRect().height);
      if(measured&&!anchor){
        const visible=[...root.querySelectorAll<HTMLElement>('[data-game-entry]')].find(node=>{const rect=node.getBoundingClientRect();return rect.bottom>0&&rect.top<innerHeight;});
        if(visible)anchor={row:visible,top:visible.getBoundingClientRect().top};
      }
      changed=measured||changed;
    }
    if(changed)schedule();
  });
  mountListCoverPreview(preview.signal,panel);
  const error=document.createElement('p');error.className='error-message game-list-error';error.hidden=true;error.setAttribute('role','alert');
  const retry=document.createElement('button');retry.type='button';retry.className='lab-text-button';retry.textContent='Retry';
  error.append('Unable to load this part of the list. ',retry);root.querySelector('.list-content')!.append(error);
  retry.addEventListener('click',()=>{error.hidden=true;clearTimeout(retryTimer);retryTimer=undefined;blocked.clear();if(refreshing)void acquire(0);else schedule();},{signal});
  function paintRow(row:HTMLElement,title:EditorTitle){
    const entry=title.entry;row.hidden=!entry;
    if(!entry)return;
    row.dataset.entryStatus=entry.status;row.dataset.score=String(entry.score??-1);row.dataset.hours=String(entry.total_seconds??-1);
    row.dataset.updated=entry.updated_at||'';row.dataset.favorite=String(!!title.favorite);
    const score=row.querySelector('[data-score-label]');if(score)score.textContent=scoreLabel(entry.score);
    row.querySelector<HTMLElement>('[data-game-list-favorite]')!.hidden=!title.favorite;
    const progress=gameProgress(entry);row.querySelector('[data-game-list-hours]')!.textContent=progress.hours||'—';
    row.querySelectorAll<HTMLElement>('[data-game-list-achievements]').forEach(node=>{node.textContent=progress.achievement||'—';node.dataset.known=String(progress.achievement!=null);});
  }
  function remember(data:Page,rows:HTMLElement[]){
    const byId=new Map(rows.map(row=>[Number(row.dataset.gameEntry),row]));
    data.results.forEach(state=>{
      const id=state.subject.catalogue_id,incoming=byId.get(id);if(!incoming)return;
      const title=owner?editorStore.seed(gameListEditorTitle(state)):undefined;
      const existing=retained.get(id);
      if(existing&&existing!==incoming){
        const annotation=incoming.querySelector('.entry-annotations'),old=existing.querySelector('.entry-annotations');
        if(annotation)old?old.replaceWith(annotation):existing.querySelector('.entry-title')!.append(annotation);else old?.remove();
        byId.set(id,existing);
      }
      if(title)paintRow(byId.get(id)!,title);
    });
    cache.set(data.offset,{data,rows:byId});
  }
  const initial=JSON.parse(panel.querySelector('#game-list-page')!.textContent!) as Page;
  remember(initial,[...root.querySelectorAll<HTMLElement>('[data-game-entry]')]);
  function geometry(data:Page){
    total=data.total;counts=data.group_counts;
    const facets=document.createElement('div'),genres=document.createElement('select'),years=document.createElement('input');
    genres.id='genre-filter';genres.append(new Option('All genres',''),...data.genres.map(genre=>new Option(genre,genre)));
    years.id='year-filter';years.dataset.years=JSON.stringify(data.years);facets.append(genres,years);
    controls.refreshFacets(facets);
    root.dataset.gameTotal=String(total);root.dataset.gameCounts=JSON.stringify(counts);
    root.dataset.statusCounts=JSON.stringify(data.status_counts);
    root.querySelectorAll<HTMLElement>('[data-list-status-count]').forEach(node=>node.textContent=String(node.dataset.listStatusCount==='all'?Object.values(data.status_counts).reduce((a,b)=>a+b,0):data.status_counts[node.dataset.listStatusCount!]||0));
    root.querySelector('.list-toolbar [aria-live]')!.textContent=`${total} entries`;
    root.querySelector<HTMLElement>('#list-empty')!.hidden=total!==0;
    groups.forEach(group=>{const count=counts[group.dataset.gameWindow!]||0;group.style.setProperty('--game-rows',String(count));group.closest<HTMLElement>('[data-game-group]')!.hidden=!count;});
  }
  geometry(initial);
  function schedule(){if(!frame&&!signal.aborted)frame=requestAnimationFrame(render);}
  async function acquire(offset:number){
    const version=epoch;flights.add(offset);let recoverable=true;
    try{
      const params=new URLSearchParams(query);params.set('offset',String(offset));
      const response=await fetch(`/user/${encodeURIComponent(root!.dataset.gameUsername!)}/games/rows?${params}`,{signal:AbortSignal.any([requests.signal,AbortSignal.timeout(15_000)]),cache:'no-store'});
      if(!response.ok){recoverable=![401,403].includes(response.status);throw new Error('List read failed');}
      const template=document.createElement('template');template.innerHTML=await response.text();const html=template.content;
      const data=JSON.parse(html.querySelector('[data-game-page]')!.textContent!) as Page;
      if(signal.aborted||version!==epoch)return;
      if(data.offset!==offset)throw new Error('Unexpected list page');
      remember(data,[...html.querySelectorAll<HTMLElement>('[data-game-entry]')]);
      if(offset===0){geometry(data);refreshing=false;}
      blocked.delete(offset);error.hidden=blocked.size===0;
    }catch{
      if(!signal.aborted&&version===epoch&&!requests.signal.aborted){
        error.hidden=false;blocked.add(offset);clearTimeout(retryTimer);if(recoverable)retryTimer=setTimeout(()=>{retryTimer=undefined;blocked.clear();if(refreshing)void acquire(0);else schedule();},5000);
      }
    }finally{if(version===epoch){flights.delete(offset);schedule();}}
  }
  function render(){
    frame=0;if(signal.aborted||!panelActive(panel)||refreshing)return;
    const rowHeight=parseFloat(getComputedStyle(root!).getPropertyValue('--game-row-height'))||56;
    wanted.clear();let base=0;
    const active=document.activeElement?.closest<HTMLElement>('[data-game-entry]');
    for(const group of groups){
      const key=group.dataset.gameWindow!,count=counts[key]||0,top=group.getBoundingClientRect().top;
      let layout=layouts.get(key);
      if(!layout||layout.estimate!==rowHeight){layout=new GameListGeometry(rowHeight);layouts.set(key,layout);}
      group.style.height=`${layout.offset(count)}px`;
      const range=layout.window(top,innerHeight,count,ahead);
      const desired:HTMLElement[]=[];
      gameListPages(base+range.start,base+range.end,total).forEach(offset=>wanted.add(offset));
      for(let index=range.start;index<range.end;index++){
        const position=base+index,offset=Math.floor(position/gameListPageSize)*gameListPageSize;
        const page=cache.get(offset),state=page?.data.results[position-offset],row=state&&page?.rows.get(state.subject.catalogue_id);
        if(row){
          row.style.setProperty('--game-row',String(index));row.style.setProperty('--game-offset',`${layout.offset(index)}px`);
          const rowKey=`${epoch}:${rowHeight}:${key}:${index}`;
          if(row.dataset.gameRowKey!==rowKey){row.dataset.gameRowKey=rowKey;rowSizes.unobserve(row);rowSizes.observe(row);}
          desired.push(row);
        }
      }
      if(active&&active.parentElement===group&&!desired.includes(active))desired.push(active);
      const keep=new Set(desired);
      for(const child of [...group.children])if(!keep.has(child as HTMLElement)){
        rowSizes.unobserve(child);delete (child as HTMLElement).dataset.gameRowKey;child.remove();
      }
      desired.sort((a,b)=>Number(a.style.getPropertyValue('--game-row'))-Number(b.style.getPropertyValue('--game-row')));
      let cursor=group.firstChild;
      desired.forEach(row=>{
        if(row!==cursor)group.insertBefore(row,cursor);
        cursor=row.nextSibling;retained.set(Number(row.dataset.gameEntry),row);
      });
      base+=count;
    }
    if(anchor){
      if(anchor.row.isConnected){const shift=anchor.row.getBoundingClientRect().top-anchor.top;if(Math.abs(shift)>.5)window.scrollBy(0,shift);}
      anchor=undefined;
    }
    // Prefetch adjacent ranges, but put visible demand first after large jumps.
    const primary=[...wanted];primary.forEach(offset=>{if(offset+gameListPageSize<total)wanted.add(offset+gameListPageSize);if(offset>0)wanted.add(offset-gameListPageSize);});
    for(const offset of wanted)if(flights.size<2&&!cache.has(offset)&&!flights.has(offset)&&!blocked.has(offset)&&retryTimer===undefined)void acquire(offset);
    const mounted=new Set([...root!.querySelectorAll<HTMLElement>('[data-game-entry]')].map(row=>Number(row.dataset.gameEntry)));
    for(const [id] of retained)if(!mounted.has(id))retained.delete(id);
    for(const [offset,page] of cache)if(cache.size>8&&!wanted.has(offset)&&![...page.rows.keys()].some(id=>mounted.has(id)))cache.delete(offset);

  }
  function reset(){
    epoch++;requests.abort();requests=new AbortController();flights.clear();blocked.clear();cache.clear();layouts.clear();anchor=undefined;clearTimeout(retryTimer);retryTimer=undefined;
    error.hidden=true;wanted.clear();refreshing=true;void acquire(0);
  }
  function submit(){
    clearTimeout(searchTimer);
    const values=new URLSearchParams();for(const [key,value] of new FormData(form))if(typeof value==='string')values.set(key,value);
    query=gameListQuery(values,defaultSort).query;controls.sync(query.get('status')||'all',defaultSort);
    root.dataset.status=query.get('status')||'';root.dataset.sort=query.get('sort')||'title';root.dataset.favorites=String(query.has('favorites'));
    const url=new URL(panelUrl(panel));url.search=query.toString();setPanelUrl(panel,url);reset();
  }
  form.addEventListener('submit',event=>{event.preventDefault();submit();},{signal});
  filters.querySelectorAll<HTMLButtonElement>('[data-status]').forEach(button=>button.addEventListener('click',()=>{field('status').value=button.dataset.status==='all'?'':button.dataset.status!;submit();},{signal}));
  form.addEventListener('input',event=>{const input=event.target as HTMLInputElement;if(input.id==='year-filter')return;clearTimeout(searchTimer);if(input.name==='q')searchTimer=setTimeout(submit,250);else submit();},{signal});
  filters.querySelector('#year-filter')!.addEventListener('change',submit,{signal});
  filters.querySelector('#reset-filters')!.addEventListener('click',()=>{for(const name of ['q','genre','favorites','status','year'])field(name).value='';filters.querySelector<HTMLInputElement>('#year-filter')!.value='0';field('sort').value=defaultSort;submit();},{signal});
  window.addEventListener('scroll',()=>{const now=performance.now();ahead=Math.min(16000,Math.max(3200,Math.abs(scrollY-previousY)/Math.max(1,now-previousTime)*700));previousY=scrollY;previousTime=now;schedule();},{signal,passive:true});
  window.addEventListener('resize',schedule,{signal});
  panel.addEventListener('anylist:panel-active',()=>{
    if(requests.signal.aborted)requests=new AbortController();blocked.clear();if(dirty){dirty=false;submit();}schedule();
  },{signal});
  panel.addEventListener('anylist:panel-inactive',()=>{
    controls.close();dirty=dirty||refreshing||searchTimer!==undefined;
    clearTimeout(searchTimer);searchTimer=undefined;clearTimeout(retryTimer);retryTimer=undefined;
    requests.abort();epoch++;flights.clear();
  },{signal});
  document.addEventListener('anylist:editor-state',event=>{
    const title=(event as CustomEvent<EditorTitle>).detail;if(!owner||title.type!=='game')return;
    const row=retained.get(title.id!);if(row)paintRow(row,title);
  },{signal});
  document.addEventListener('anylist:editor-committed',event=>{
    const title=(event as CustomEvent<{title:EditorTitle}>).detail.title;if(!owner||title.type!=='game')return;
    if(panelActive(panel))reset();else dirty=true;
  },{signal});
  root.addEventListener('click',event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('[data-game-list-rate]');if(!button)return;
    const title=editorStore.findSubject({kind:'game',catalogue_id:Number(button.dataset.gameListRate)});if(!title)return;
    if(title.game_class==='expansion'){document.dispatchEvent(new CustomEvent('anylist:open-editor',{detail:{subject:title.subject,opener:button,title}}));return;}
    (window as any).anyListQuickRate?.({title:title.title,poster:title.poster,score:title.entry?.score,ratingMode:'manual',onScore:async(score:number|null)=>{await editorStore.write(title,{score});}});
  },{signal});
  signal.addEventListener('abort',()=>{requests.abort();preview.abort();rowSizes.disconnect();clearTimeout(searchTimer);clearTimeout(retryTimer);cancelAnimationFrame(frame);cache.clear();retained.clear();layouts.clear();},{once:true});
  controls.sync(query.get('status')||'all',defaultSort);schedule();
}
