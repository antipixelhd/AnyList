import {initializeEntryAnnotations} from './entry-annotations';
import {editorStore,type EditorTitle} from './editor-store';
import {GameListIndex,type GameListIndexResponse,type GameListState} from './game-list-index';
import {createGameListRow,paintGameListRow} from './game-list-row';
import {mountListCoverPreview} from './list-cover-preview';
import {mountListFilterControls} from './list-filter-controls';
import {gameListQuery,gameListEditorTitle} from './game-list-data';
import {gameListPageSize,gameListPages,GameListGeometry} from './game-list-window';
import {panelActive,panelUrl,setPanelUrl} from './profile-panel';

type Page={offset:number;total:number;results:Record<string,any>[];group_counts:Record<string,number>;status_counts:Record<string,number>;genres:string[];years:string[]};
const stateLimit=10_000;
const stateTextBudget=4*1024*1024;
const rowNodeLimit=450;
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
  let requests=new AbortController();
  const template=panel.querySelector<HTMLTemplateElement>('[data-game-row-template]')!;
  const index=new GameListIndex(),states=new Map<number,GameListState>(),rowCache=new Map<number,HTMLElement>();
  const stateSizes=new Map<number,number>();
  let stateTextSize=0;
  const endpoint=`/api/proxy/tracking/games/users/${encodeURIComponent(root.dataset.gameUsername!)}`;
  let local:ReturnType<GameListIndex['select']>=null,writeVersion=0;
  let warmTimer:ReturnType<typeof setTimeout>|undefined,warming:AbortController|undefined,warmAttempts=0,indexDisabled=false;
  const pages=new Map<number,number[]>(),flights=new Set<number>(),wanted=new Set<number>(),blocked=new Set<number>();
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
  const preview=mountListCoverPreview(signal,panel,{reuseThumbnail:true});
  const error=document.createElement('p');error.className='error-message game-list-error';error.hidden=true;error.setAttribute('role','alert');
  const retry=document.createElement('button');retry.type='button';retry.className='lab-text-button';retry.textContent='Retry';
  error.append('Unable to load this part of the list. ',retry);root.querySelector('.list-content')!.append(error);
  retry.addEventListener('click',()=>{error.hidden=true;clearTimeout(retryTimer);retryTimer=undefined;blocked.clear();if(refreshing)void acquire(0);else schedule();},{signal});
  function fromTitle(title:EditorTitle):GameListState{
    return {...states.get(title.id!),...title.game_state,...title.entry,subject:title.subject,
      title:title.title,image_url:title.poster,year:title.year,genres:title.genres,
      game_class:title.game_class,base_work_id:title.base_work_id,listed:!!title.entry,favorite:!!title.favorite};
  }
  function remember(state:GameListState){
    const id=state.subject.catalogue_id;
    if(states.get(id)===state)return;
    const length=JSON.stringify(state).length;
    stateTextSize+=length-(stateSizes.get(id)||0);
    stateSizes.set(id,length);
    states.delete(id);states.set(id,state);
    const row=rowCache.get(id);if(row)paintGameListRow(row,state,template);
  }
  function rememberRead(state:GameListState){
    const pending=owner&&editorStore.hasPendingSubject(state.subject)&&editorStore.findSubject(state.subject);
    remember(pending?fromTitle(pending):state);
  }
  function pageIds(offset:number){
    return local?local.ids.slice(offset,offset+gameListPageSize):pages.get(offset);
  }
  const initial=JSON.parse(panel.querySelector('#game-list-page')!.textContent!) as Page;
  for(const row of root.querySelectorAll<HTMLElement>('[data-game-entry]'))rowCache.set(Number(row.dataset.gameEntry),row);
  initial.results.forEach(rememberRead);
  pages.set(initial.offset,initial.results.map(state=>state.subject.catalogue_id));
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
      const readSignal=AbortSignal.any([requests.signal,AbortSignal.timeout(15_000)]);
      if(local){
        const missing=pageIds(offset)!.filter(id=>!states.has(id));
        if(!missing.length)return;
        const params=new URLSearchParams();missing.forEach(id=>params.append('ids',String(id)));
        const response=await fetch(`${endpoint}/rows?${params}`,{signal:readSignal,cache:'no-store'});
        if(!response.ok){recoverable=![401,403].includes(response.status);throw new Error('List read failed');}
        const incoming:GameListState[]=await response.json();
        if(signal.aborted||version!==epoch)return;
        const loaded=new Set(incoming.map(state=>state.subject.catalogue_id));
        // Another tab may have changed membership since this index was acquired.
        if(missing.some(id=>!loaded.has(id))){index.invalidate();queueWarm();reset();return;}
        incoming.forEach(rememberRead);
      }else{
        const params=new URLSearchParams(query);params.set('offset',String(offset));params.set('limit',String(gameListPageSize));params.set('grouped','true');
        const response=await fetch(`${endpoint}/list?${params}`,{signal:readSignal,cache:'no-store'});
        if(!response.ok){recoverable=![401,403].includes(response.status);throw new Error('List read failed');}
        const data:Page=await response.json();
        if(signal.aborted||version!==epoch)return;
        if(data.offset!==offset)throw new Error('Unexpected list page');
        data.results.forEach(rememberRead);
        pages.set(offset,data.results.map(state=>state.subject.catalogue_id));
        if(offset===0){geometry(data);refreshing=false;queueWarm();}
      }
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
        const id=local?local.ids[position]:pages.get(offset)?.[position-offset];
        const state=id===undefined?undefined:states.get(id);
        if(id!==undefined&&state){
          const row=rowCache.get(id)||createGameListRow(template,state);
          rowCache.delete(id);rowCache.set(id,row);
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
        cursor=row.nextSibling;
      });
      base+=count;
    }
    if(anchor){
      if(anchor.row.isConnected){const shift=anchor.row.getBoundingClientRect().top-anchor.top;if(Math.abs(shift)>.5)window.scrollBy(0,shift);}
      anchor=undefined;
    }
    // Prefetch adjacent ranges, but put visible demand first after large jumps.
    const primary=[...wanted];primary.forEach(offset=>{if(offset+gameListPageSize<total)wanted.add(offset+gameListPageSize);if(offset>0)wanted.add(offset-gameListPageSize);});
    const nearbyIds=new Set<number>();
    for(const offset of wanted){
      const ids=pageIds(offset);
      ids?.forEach(id=>nearbyIds.add(id));
      if(flights.size<2&&!ids?.every(id=>states.has(id))&&!flights.has(offset)&&!blocked.has(offset)&&retryTimer===undefined)void acquire(offset);
    }
    const mounted=new Set([...root!.querySelectorAll<HTMLElement>('[data-game-entry]')].map(row=>Number(row.dataset.gameEntry)));
    for(const id of rowCache.keys()){
      if(rowCache.size<=rowNodeLimit)break;
      if(!mounted.has(id))rowCache.delete(id);
    }
    if(states.size>stateLimit||stateTextSize>stateTextBudget){
      // Keep the active window resident when row data reaches its own budget.
      for(const id of nearbyIds){const state=states.get(id);if(state){states.delete(id);states.set(id,state);}}
      for(const id of states.keys()){
        if(states.size<=stateLimit&&stateTextSize<=stateTextBudget)break;
        if(mounted.has(id)||nearbyIds.has(id)||(owner&&editorStore.hasPendingSubject({kind:'game',catalogue_id:id})))continue;
        stateTextSize-=stateSizes.get(id)!;
        stateSizes.delete(id);states.delete(id);rowCache.delete(id);
      }
      for(const [offset,ids] of pages)if(!ids.some(id=>states.has(id)))pages.delete(offset);
    }

  }
  function queueWarm(){
    const connection=(navigator as Navigator&{connection?:{saveData?:boolean;effectiveType?:string}}).connection;
    const size=Object.values(JSON.parse(root.dataset.statusCounts||'{}') as Record<string,number>).reduce((a,b)=>a+b,0);
    if(signal.aborted||!panelActive(panel)||warming||warmTimer||indexDisabled||warmAttempts>=3||editorStore.hasPendingWrites()||index.ready||size>10000||connection?.saveData||/^(slow-)?2g$/.test(connection?.effectiveType||''))return;
    warmTimer=setTimeout(()=>{warmTimer=undefined;void warmIndex();},warmAttempts?30000:1000);
  }
  async function warmIndex(){
    if(!panelActive(panel)||signal.aborted||editorStore.hasPendingWrites())return;
    const version=writeVersion,controller=new AbortController();warming=controller;warmAttempts++;
    try{
      const response=await fetch(`${endpoint}/index`,{signal:AbortSignal.any([controller.signal,AbortSignal.timeout(15_000)]),cache:'no-store'});
      if(!response.ok){if([401,403].includes(response.status))indexDisabled=true;throw new Error('Index read failed');}
      const snapshot:GameListIndexResponse=await response.json();
      if(signal.aborted||controller.signal.aborted||version!==writeVersion||editorStore.hasPendingWrites())return;
      if(!snapshot.complete){indexDisabled=true;return;}
      index.publish(snapshot);reset(true);
    }catch{/* Optional warming never blocks the usable server-backed list. */}
    finally{if(warming===controller)warming=undefined;queueWarm();}
  }

  function reset(preserveGeometry=false){
    epoch++;requests.abort();requests=new AbortController();flights.clear();blocked.clear();pages.clear();if(!preserveGeometry)layouts.clear();anchor=undefined;clearTimeout(retryTimer);retryTimer=undefined;
    local=index.select(query);root.dataset.gameIndex=local?'ready':'server';
    error.hidden=true;wanted.clear();refreshing=true;
    if(local){geometry({...local,offset:0,results:[]});refreshing=false;schedule();}
    else if(panelActive(panel))void acquire(0);else dirty=true;
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
    if(requests.signal.aborted)requests=new AbortController();blocked.clear();if(dirty){dirty=false;submit();}schedule();queueWarm();
  },{signal});
  panel.addEventListener('anylist:panel-inactive',()=>{
    controls.close();preview?.hide();dirty=dirty||refreshing||searchTimer!==undefined;
    clearTimeout(searchTimer);searchTimer=undefined;clearTimeout(retryTimer);retryTimer=undefined;
    requests.abort();epoch++;flights.clear();warming?.abort();clearTimeout(warmTimer);warmTimer=undefined;
  },{signal});
  document.addEventListener('anylist:editor-state',event=>{
    const title=(event as CustomEvent<EditorTitle>).detail;if(!owner||title.type!=='game')return;
    writeVersion++;warming?.abort();warmAttempts=0;
    const state=fromTitle(title);remember(state);index.update(state);
    // Reorder complete local membership immediately, preserving shared pending state.
    if(local)reset();
    queueWarm();
  },{signal});
  document.addEventListener('anylist:editor-committed',event=>{
    const title=(event as CustomEvent<{title:EditorTitle}>).detail.title;if(!owner||title.type!=='game')return;
    writeVersion++;warming?.abort();warmAttempts=0;
    const state=fromTitle(title);remember(state);index.update(state);
    if(!title.entry)for(const child of [...states.values()])if(child.base_work_id===title.id){
      const removed={...child,listed:false};remember(removed);index.update(removed);
    }
    reset();queueWarm();
  },{signal});
  function seedEditor(event:Event){
    const button=(event.target as Element).closest<HTMLElement>('[data-edit-game],[data-game-list-rate]');
    if(!owner||!button)return;
    const id=Number(button.dataset.editGame||button.dataset.gameListRate),state=states.get(id);
    if(state)editorStore.seed(gameListEditorTitle(state));
  }
  for(const event of ['pointerover','focusin','click'])root.addEventListener(event,seedEditor,{signal});
  root.addEventListener('click',event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('[data-game-list-rate]');if(!button)return;
    const title=editorStore.findSubject({kind:'game',catalogue_id:Number(button.dataset.gameListRate)});if(!title)return;
    if(title.game_class==='expansion'){document.dispatchEvent(new CustomEvent('anylist:open-editor',{detail:{subject:title.subject,opener:button,title}}));return;}
    (window as any).anyListQuickRate?.({title:title.title,poster:title.poster,score:title.entry?.score,ratingMode:'manual',onScore:async(score:number|null)=>{await editorStore.write(title,{score});}});
  },{signal});
  signal.addEventListener('abort',()=>{warming?.abort();clearTimeout(warmTimer);states.clear();stateSizes.clear();rowCache.clear();index.invalidate();requests.abort();rowSizes.disconnect();clearTimeout(searchTimer);clearTimeout(retryTimer);cancelAnimationFrame(frame);pages.clear();layouts.clear();},{once:true});
  controls.sync(query.get('status')||'all',defaultSort);schedule();queueWarm();
}
