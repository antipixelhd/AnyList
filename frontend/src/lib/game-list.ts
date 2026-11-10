import {initializeEntryAnnotations} from './entry-annotations';
import {editorStore,type EditorTitle} from './editor-store';
import {mountListCoverPreview} from './list-cover-preview';
import {mountListFilterControls} from './list-filter-controls';
import {gameListQuery,gameListEditorTitle} from './game-list-data';
import {gameListCollection,gameListRenderBatch,gameListViewCookie,nextGameListLimit,type GameListState} from './game-list-progressive';
import {createGameListRow,paintGameListRow} from './game-list-row';
import {panelActive,panelUrl,setPanelUrl} from './profile-panel';

/** AniList-style collection loading and independent 30-row section expansion. */
export function initializeGameList(panel:HTMLElement,lifetime:AbortSignal){
  initializeEntryAnnotations();
  const candidate=panel.querySelector<HTMLElement>('[data-game-list]');if(!candidate)return;
  const root:HTMLElement=candidate,owner=root.dataset.owner==='true';
  const listeners=new AbortController(),{signal}=listeners;
  lifetime.addEventListener('abort',()=>listeners.abort(),{once:true});
  if(owner)editorStore.scope(document.body.dataset.cacheUser||'anonymous');
  mountListCoverPreview(signal,panel);
  const template=panel.querySelector<HTMLTemplateElement>('[data-game-row-template]')!;
  const filters=root.querySelector<HTMLElement>('#list-filters')!,controls=mountListFilterControls(filters,signal);
  const form=filters.querySelector<HTMLFormElement>('[data-list-filter-form]')!;
  const field=(name:string)=>form.elements.namedItem(name) as HTMLInputElement;
  const defaultSort=filters.querySelector<HTMLButtonElement>('#reset-filters')!.dataset.defaultSort||'title';
  let query=gameListQuery(new URL(panelUrl(panel)).searchParams,defaultSort).query;
  const sections=new Map([...root.querySelectorAll<HTMLElement>('[data-game-section]')].map(section=>[section.dataset.gameSection!,section]));
  const rows=new Map([...root.querySelectorAll<HTMLElement>('[data-game-entry]')].map(row=>[Number(row.dataset.gameEntry),row]));
  const data=new Map<number,GameListState>(),dirtyRows=new Set<number>();
  const initial=JSON.parse(panel.querySelector('#game-list-page')!.textContent!);
  let limits=JSON.parse(root.dataset.gameLimits||'{}') as Record<string,number>;
  let collection:ReturnType<typeof gameListCollection>;
  let frame=0,searchTimer:ReturnType<typeof setTimeout>|undefined,dirty=false,pendingSearch=false;
  function remember(title:EditorTitle){
    const id=title.id!,previous=data.get(id)||{};
    data.set(id,{...previous,...title.game_state,...title.entry,subject:title.subject,
      title:title.title,image_url:title.poster,year:title.year,genres:title.genres,
      game_class:title.game_class,base_work_id:title.base_work_id,listed:!!title.entry,favorite:!!title.favorite});
    dirtyRows.add(id);
  }
  for(const state of initial.results){
    data.set(state.subject.catalogue_id,state);
    if(owner){
      const pending=editorStore.hasPendingSubject(state.subject);
      remember(editorStore.seed(gameListEditorTitle(state)));
      if(!pending)dirtyRows.delete(state.subject.catalogue_id);
    }
  }
  function saveView(){
    root.dataset.gameLimits=JSON.stringify(limits);
    const key=`${document.body.dataset.cacheUser||'anonymous'}:${root.dataset.gameUsername}:${query}`;
    const value=encodeURIComponent(JSON.stringify({key,limits}));
    document.cookie=`${gameListViewCookie}=${value}; Path=/user/${encodeURIComponent(root.dataset.gameUsername!)}/games; SameSite=Lax${location.protocol==='https:'?'; Secure':''}`;
  }
  function rowFor(state:GameListState){
    const id=state.subject.catalogue_id;let row=rows.get(id);
    if(!row){row=createGameListRow(template,state);rows.set(id,row);}
    else if(dirtyRows.has(id))paintGameListRow(row,state,template);
    dirtyRows.delete(id);return row;
  }
  function reconcile(status:string){
    const section=sections.get(status)!,entries=collection.groups.get(status)||[];
    section.closest<HTMLElement>('[data-game-group]')!.hidden=!entries.length;
    const desired=entries.slice(0,limits[status]||gameListRenderBatch).map(rowFor);
    const keep=new Set(desired);
    for(const child of [...section.children])if(!keep.has(child as HTMLElement))child.remove();
    let cursor=section.firstChild;
    for(const row of desired){if(row!==cursor)section.insertBefore(row,cursor);cursor=row.nextSibling;}
  }
  function schedule(){if(!frame&&!signal.aborted)frame=requestAnimationFrame(fill);}
  function fill(){
    frame=0;if(signal.aborted||!panelActive(panel))return;
    let changed=false;
    for(const [status,section] of sections){
      const total=collection.groups.get(status)?.length||0,current=limits[status]||gameListRenderBatch;
      const next=nextGameListLimit(current,total,section.getBoundingClientRect().bottom,innerHeight);
      if(next!==current){
        section.append(...collection.groups.get(status)!.slice(current,next).map(rowFor));
        limits[status]=next;changed=true;
      }
    }
    if(changed){saveView();schedule();}
  }
  function render(reset=false){
    if(!panelActive(panel)){dirty=true;return;}
    collection=gameListCollection([...data.values()],query);
    if(reset)limits={};
    for(const status of sections.keys()){limits[status]=Math.min(collection.groups.get(status)?.length||0,limits[status]||gameListRenderBatch);reconcile(status);}
    root.dataset.gameTotal=String(collection.total);root.dataset.gameLimits=JSON.stringify(limits);root.dataset.statusCounts=JSON.stringify(collection.counts);
    root.querySelectorAll<HTMLElement>('[data-list-status-count]').forEach(node=>node.textContent=String(node.dataset.listStatusCount==='all'?Object.values(collection.counts).reduce((sum,value)=>sum+value,0):collection.counts[node.dataset.listStatusCount!]||0));
    root.querySelector('.list-toolbar [aria-live]')!.textContent=`${collection.total} entries`;
    root.querySelector<HTMLElement>('#list-empty')!.hidden=collection.total!==0;
    const facets=document.createElement('div'),genres=document.createElement('select'),years=document.createElement('input');
    genres.id='genre-filter';genres.append(new Option('All genres',''),...[...new Set(collection.eligible.flatMap(state=>state.genres||[]))].sort().map(genre=>new Option(genre,genre)));
    years.id='year-filter';years.dataset.years=JSON.stringify([...new Set(collection.eligible.map(state=>state.year).filter(Boolean))]);facets.append(genres,years);controls.refreshFacets(facets);
    controls.sync(query.get('status')||'all',defaultSort);saveView();schedule();
  }
  function submit(){
    clearTimeout(searchTimer);searchTimer=undefined;
    const values=new URLSearchParams();for(const [key,value] of new FormData(form))if(typeof value==='string')values.set(key,value);
    query=gameListQuery(values,defaultSort).query;
    root.dataset.status=query.get('status')||'';root.dataset.sort=query.get('sort')||'title';root.dataset.favorites=String(query.has('favorites'));
    const url=new URL(panelUrl(panel));url.search=query.toString();setPanelUrl(panel,url);render(true);
  }
  form.addEventListener('submit',event=>{event.preventDefault();submit();},{signal});
  filters.querySelectorAll<HTMLButtonElement>('[data-status]').forEach(button=>button.addEventListener('click',()=>{field('status').value=button.dataset.status==='all'?'':button.dataset.status!;submit();},{signal}));
  form.addEventListener('input',event=>{const input=event.target as HTMLInputElement;if(input.id==='year-filter')return;clearTimeout(searchTimer);if(input.name==='q')searchTimer=setTimeout(submit,250);else submit();},{signal});
  filters.querySelector('#year-filter')!.addEventListener('change',submit,{signal});
  filters.querySelector('#reset-filters')!.addEventListener('click',()=>{for(const name of ['q','genre','favorites','status','year'])field(name).value='';filters.querySelector<HTMLInputElement>('#year-filter')!.value='0';field('sort').value=defaultSort;submit();},{signal});
  window.addEventListener('scroll',schedule,{signal,passive:true});window.addEventListener('resize',schedule,{signal});
  panel.addEventListener('anylist:panel-inactive',()=>{controls.close();if(searchTimer!==undefined){clearTimeout(searchTimer);searchTimer=undefined;pendingSearch=true;}},{signal});
  panel.addEventListener('anylist:panel-active',()=>{if(pendingSearch){pendingSearch=false;submit();}else if(dirty){dirty=false;render();}schedule();},{signal});
  document.addEventListener('anylist:editor-state',event=>{
    const title=(event as CustomEvent<EditorTitle>).detail;if(!owner||title.type!=='game')return;
    remember(title);render();
  },{signal});
  document.addEventListener('anylist:editor-committed',event=>{
    const title=(event as CustomEvent<{title:EditorTitle}>).detail.title;if(!owner||title.type!=='game')return;
    if(!title.entry&&!['expansion','dlc'].includes(title.game_class||''))for(const [id,state] of data)if(state.base_work_id===title.id){data.set(id,{...state,listed:false});dirtyRows.add(id);}
    remember(title);render();
  },{signal});
  root.addEventListener('click',event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('[data-game-list-rate]');if(!button)return;
    const title=editorStore.findSubject({kind:'game',catalogue_id:Number(button.dataset.gameListRate)});if(!title)return;
    if(title.game_class==='expansion'){document.dispatchEvent(new CustomEvent('anylist:open-editor',{detail:{subject:title.subject,opener:button,title}}));return;}
    (window as any).anyListQuickRate?.({title:title.title,poster:title.poster,score:title.entry?.score,ratingMode:'manual',onScore:async(score:number|null)=>{await editorStore.write(title,{score});}});
  },{signal});
  signal.addEventListener('abort',()=>{clearTimeout(searchTimer);cancelAnimationFrame(frame);rows.clear();data.clear();dirtyRows.clear();},{once:true});
  render();
}
