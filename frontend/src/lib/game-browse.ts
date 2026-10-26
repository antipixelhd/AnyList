import {editorStore,type EditorTitle} from './editor-store';
import {bindBrowseCardActions,bindBrowseCardTouch,paintBrowseCardState,type BrowseItem} from './browse-card-actions';
import {browseCardKey,browseCardHref} from './browse-card-data';
import {createBrowseCard} from './browse-card-render';
import {prepareBrowsePoster,createBrowseLoading} from './browse-loading';
import {createBrowseCache} from './browse-cache';
import {readVerifiedGamePage,gameBrowseCacheable,type GameVerificationPage} from './game-browse-verification';
import {initializeMobileBrowseFilters} from './browse-mobile-filters';
import {initializeBrowseMenus} from './browse-menus';
import {initializeBrowseRanges} from './browse-ranges';
import {initializeBrowseSort} from './browse-sort';
import {initializeScrollMotion} from './scroll-motion';
import {createBrowsePaginationDemand} from './browse-pagination';
import {appendBrowseTimeline} from './browse-timeline';
import {genreSelectionLabel} from './browse-view';
import {formatDate} from './date-time';
import {gameBrowseValues,gameBrowseIsCategories,gameBrowseHasFilters,gameBrowseRequest,gameBrowseHref,gamePlatformSelection,gameSourceContext,type GamePlatformGroup} from './game-browse-data';

type Payload=GameVerificationPage&{results:BrowseItem[];has_more:boolean;cursor?:string|null;source?:string;source_at?:number|null;fetched_at?:string|null;partial?:boolean;stale?:boolean;coverage_limited?:boolean};
type Shelf={key:string;title:string;payload:Payload|null};
class BrowseReadError extends Error {status:number;constructor(message:string,status:number){super(message);this.status=status;}}

/** Games supplies retrieval/progress semantics; the visible controls and cards are shared. */
export function initializeGameBrowse(root:HTMLElement,signal:AbortSignal){
  root.dataset.enhanced='';root.dataset.browseScripted='';
  editorStore.scope(document.body.dataset.cacheUser||'anonymous');
  const find=<T extends HTMLElement>(selector:string)=>root.querySelector<T>(selector)!;
  const form=find<HTMLFormElement>('[data-browse-form]');
  const field=(name:string)=>form.elements.namedItem(name) as HTMLInputElement|HTMLSelectElement;
  initializeMobileBrowseFilters(form,signal);initializeBrowseMenus(form,signal);
  const paintRanges=initializeBrowseRanges(form,signal,'min_metacritic');
  const initial=JSON.parse(find('#game-browse-data').textContent||'{}');
  const groups=initial.facets.platform_groups as GamePlatformGroup[];
  const initialValues=new URLSearchParams(initial.values);
  const selectedPlatforms=new Set((field('platforms').value||'').split(',').filter(Boolean));
  const sections=find('[data-browse-sections]'),grid=find('[data-browse-results]'),region=find('[data-browse-region]');
  const template=find<HTMLTemplateElement>('[data-browse-template]');
  const skeleton=find('[data-browse-skeleton]'),loadingView=createBrowseLoading(region,skeleton);
  const error=find('[data-browse-error]'),notice=find('[data-browse-notice]'),empty=find('[data-browse-empty]');
  const more=find<HTMLButtonElement>('[data-browse-more]'),end=find('[data-browse-end]');
  const shelves=new Map<string,Shelf>((initial.sections as Shelf[]).map(s=>[s.key,s]));
  const cache=createBrowseCache<Payload>(),items=new Map<HTMLElement,BrowseItem>(),readyPosters=new Set<string>();
  const seen=new Set<string>();
  const edits=new Map<number,{version:number;status:string|null;score:number|null}>();
  let writeVersion=0,generation=0,request:AbortController|undefined,timer:ReturnType<typeof setTimeout>|undefined;
  let loading=false,presentedCategories=initial.categories,paintedTimeline=initialValues.get('view')==='timeline';
  let payload=initial.payload as Payload,activeValues=initialValues.toString(),retryAppend=false,syncing=false;
  const values=()=>{
    const result=new URLSearchParams();
    for(const key of ['q','sort','genres','platforms','start','end','min_metacritic','view']){const value=field(key).value.trim();if(value)result.set(key,value);}
    return gameBrowseValues(result);
  };
  const refreshSelect=(select:HTMLInputElement|HTMLSelectElement)=>select.dispatchEvent(new Event('input',{bubbles:true}));
  const syncSort=initializeBrowseSort(root,signal,()=>changed(),undefined,{defaultFilteredSort:'trending',hasFilters:gameBrowseHasFilters});
  const syncItem=(updated:BrowseItem)=>{
    const same=(item:BrowseItem)=>item.type==='game'&&(!!item.id&&item.id===updated.id||!!item.igdb_id&&item.igdb_id===updated.igdb_id);
    const state={id:updated.id,list_status:updated.list_status,score:updated.score};
    for(const page of cache.values())for(const item of page.results)if(same(item))Object.assign(item,state);
    for(const [card,item] of items)if(same(item)){
      Object.assign(item,state);paintBrowseCardState(card,item);
      const href=browseCardHref(item);if(href)card.querySelectorAll<HTMLAnchorElement>('a').forEach(link=>link.href=href);
    }
  };
  document.addEventListener('anylist:game-resolved',event=>{
    const resolved=(event as CustomEvent<{igdb_id:number;id:number}>).detail;
    for(const page of cache.values())for(const item of page.results)if(item.igdb_id===resolved.igdb_id)item.id=resolved.id;
    for(const [card,item] of items)if(item.igdb_id===resolved.igdb_id){item.id=resolved.id;const href=browseCardHref(item);if(href)card.querySelectorAll<HTMLAnchorElement>('a').forEach(link=>link.href=href);}
  },{signal});
  document.addEventListener('anylist:editor-state',event=>{
    const title=(event as CustomEvent<EditorTitle>).detail;if(title.type!=='game'||!title.id)return;
    edits.set(title.id,{version:++writeVersion,status:title.entry?.status||null,score:title.entry?.score??null});
  },{signal});
  const reconcile=(page:Payload,started:number)=>{
    for(const item of page.results){
      if(!item.id)continue;
      const subject={kind:'game' as const,catalogue_id:item.id},edit=edits.get(item.id);
      if(edit&&edit.version>started){item.list_status=edit.status;item.score=edit.score;continue;}
      if(editorStore.hasPendingSubject(subject)){
        const title=editorStore.findSubject(subject);if(title){item.list_status=title.entry?.status||null;item.score=title.entry?.score??null;}
      }else editorStore.invalidateSubject(subject);
    }
    return page;
  };
  const json=async(path:string,requestSignal:AbortSignal)=>{
    const cached=cache.get(path);if(cached)return reconcile(cached,-1);
    const started=writeVersion,response=await fetch(`/api/proxy/tracking/${path}`,{signal:requestSignal});
    const body=await response.json().catch(()=>null);
    if(!response.ok)throw new BrowseReadError(typeof body?.detail==='string'?body.detail:'Unable to load games. Try again.',response.status);
    if(!Array.isArray(body?.results))throw new Error('Unable to load games. Try again.');
    if(requestSignal.aborted||signal.aborted)throw new DOMException('Aborted','AbortError');
    const page=reconcile(body,started);if(gameBrowseCacheable(page))cache.set(path,page);return page;
  };
  const register=(container:HTMLElement,rows:BrowseItem[])=>{
    const byKey=new Map(rows.map(item=>[browseCardKey(item),item]));
    container.querySelectorAll<HTMLElement>('[data-browse-card]').forEach(card=>{
      const item=byKey.get(card.dataset.key!);if(!item)return;
      items.set(card,item);paintBrowseCardState(card,item);
      const image=card.querySelector<HTMLImageElement>('[data-poster]');if(image)prepareBrowsePoster(image,signal,readyPosters);
      if(container===grid)seen.add(browseCardKey(item));
    });
  };
  const clear=(container:HTMLElement)=>{for(const card of items.keys())if(container.contains(card))items.delete(card);container.replaceChildren();};
  const render=(rows:BrowseItem[],container:HTMLElement,timeline=false,dedupe=false)=>{
    const cards:HTMLElement[]=[];
    for(const item of rows){const key=browseCardKey(item);if(dedupe&&seen.has(key))continue;if(dedupe)seen.add(key);const card=createBrowseCard(item,template,signal,readyPosters);items.set(card,item);cards.push(card);}
    if(timeline)appendBrowseTimeline(container,cards,items);else container.append(...cards);
  };
  const footer=()=>{
    find('[data-browse-sentinel]').hidden=presentedCategories;
    more.hidden=!payload.has_more;more.disabled=loading;
    end.hidden=loading||!error.hidden||!!payload.verification_pending||payload.has_more||!grid.querySelector('[data-browse-card]');
    end.textContent=payload.partial?'End of verified results':"You're all caught up";
    empty.hidden=presentedCategories||loading||!!payload.verification_pending||payload.has_more||!!grid.querySelector('[data-browse-card]')||!error.hidden;
    empty.querySelector('h2')!.textContent=payload.coverage_limited?'No verified games in this search window':'No matching games';
  };
  const paintSource=(shelf:Shelf)=>{
    const host=find(`[data-game-shelf="${shelf.key}"]`),context=shelf.payload?gameSourceContext(shelf.payload):'';
    host.querySelectorAll<HTMLElement>('[data-game-view-all]').forEach(link=>{link.title=context;link.setAttribute('aria-description',context);});
  };
  for(const shelf of shelves.values())if(shelf.payload){
    reconcile(shelf.payload,-1);if(gameBrowseCacheable(shelf.payload))cache.set(gameBrowseRequest(new URLSearchParams({sort:shelf.key}),6),shelf.payload);
    register(find(`[data-game-shelf="${shelf.key}"]`),shelf.payload.results);paintSource(shelf);
  }
  if(!initial.categories){reconcile(payload,-1);register(grid,payload.results);if(!initial.error&&gameBrowseCacheable(payload))cache.set(gameBrowseRequest(initialValues),payload);}
  requestAnimationFrame(()=>requestAnimationFrame(()=>{if(!signal.aborted)initializeScrollMotion(root,signal,'.browse-card-body, .browse-skeleton-body','browse-scroll-reveal',{animateInitial:false,downwardOnly:true,scaleEntrance:true,once:true,appearedAttribute:'data-browse-appeared'});}));
  bindBrowseCardActions(region,items,signal,syncItem,message=>{notice.textContent=message;notice.hidden=false;});bindBrowseCardTouch(region,signal);
  function updateControls(){
    syncing=true;
    const current=values(),category=gameBrowseIsCategories(current),timeline=current.get('view')==='timeline';
    if(current.get('sort')&&current.get('sort')!=='trending'){field('min_metacritic').value='0';paintRanges();}
    root.dataset.browseView=timeline?'timeline':'grid';
    syncSort(!!current.get('q'),true,gameBrowseHasFilters(current));
    find('[data-game-critic-filter]').hidden=!!current.get('sort')&&current.get('sort')!=='trending'||timeline;
    find<HTMLButtonElement>('[data-browse-search-clear]').hidden=!field('q').value;
    root.querySelectorAll<HTMLAnchorElement>('[data-game-layout]').forEach(link=>{
      const selected=link.dataset.gameLayout===(timeline?'timeline':'grid');
      if(selected)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
      link.href=gameBrowseHref(current,{view:link.dataset.gameLayout==='timeline'?'timeline':null});
    });
    const chips=find('[data-active-filters]');chips.replaceChildren();
    const chip=(name:string,label:string)=>{const button=document.createElement('button');button.type='button';button.textContent=label;button.append(find<HTMLTemplateElement>('[data-browse-close-icon]').content.cloneNode(true));button.setAttribute('aria-label',`Remove ${label} filter`);button.dataset.gameChip=name;chips.append(button);};
    const genres=field('genres').value.split(',');for(const genre of initial.facets.genres)if(genres.includes(String(genre.id)))chip(`genre:${genre.id}`,genre.name);
    for(const platform of gamePlatformSelection(field('platforms').value.split(','),groups))chip(`platform:${platform.id}`,platform.name);
    for(const key of ['start','end'])if(current.has(key))chip(key,`${key==='start'?'After':'Before'} ${formatDate(current.get(key))}`);
    if(current.has('min_metacritic'))chip('min_metacritic',`${current.get('min_metacritic')}+ Metacritic`);
    if(current.get('q'))chip('q',`“${current.get('q')}”`);
    if(chips.childElementCount){const reset=document.createElement('button');reset.type='button';reset.dataset.reset='';reset.textContent='Clear all';chips.append(reset);}
    root.toggleAttribute('data-browse-chips',!!chips.childElementCount);
    for(const group of groups){const input=form.querySelector<HTMLInputElement>(`[name=platform][value="${group.id}"]`);if(input)input.indeterminate=group.devices.some(device=>field('platforms').value.split(',').includes(device.id));}
    syncing=false;return {current,category,timeline};
  }
  const syncGenres=()=>{field('genres').value=[...form.querySelectorAll<HTMLInputElement>('[name=genre]:checked')].map(input=>input.value).join(',');find('#browse-genres-value').textContent=genreSelectionLabel(field('genres').value,initial.facets.genres);};
  const syncPlatforms=()=>{
    field('platforms').value=[...selectedPlatforms].join(',');
    form.querySelectorAll<HTMLInputElement>('[name=platform]').forEach(input=>input.checked=selectedPlatforms.has(input.value));
    const logical=gamePlatformSelection([...selectedPlatforms],groups);find('#browse-platforms-value').textContent=logical.length===1?logical[0].name:logical.length?`${logical.length} selected`:'Any';
  };
  const picker=find<HTMLDetailsElement>('[data-game-device-picker]'),deviceSearch=find<HTMLInputElement>('[data-game-device-search]');
  const deviceList=find('[data-game-device-list]'),deviceMore=find<HTMLButtonElement>('[data-game-devices-more]');
  const devices=groups.flatMap(group=>group.devices.map(device=>({...device,group:group.id})));
  let deviceLimit=40;
  const renderDevices=()=>{
    const query=deviceSearch.value.trim().toLocaleLowerCase(),matches=devices.filter(device=>device.name.toLocaleLowerCase().includes(query));
    const fragment=document.createDocumentFragment();
    for(const device of matches.slice(0,deviceLimit)){
      const label=document.createElement('label'),input=document.createElement('input'),span=document.createElement('span');
      label.dataset.gameDevice='';label.dataset.deviceGroup=device.group;input.type='checkbox';input.name='platform';input.value=device.id;input.checked=selectedPlatforms.has(device.id);span.textContent=device.name;label.append(input,span);fragment.append(label);
    }
    deviceList.replaceChildren(fragment);deviceMore.hidden=matches.length<=deviceLimit;
    if(!matches.length){const message=document.createElement('p');message.textContent='No matching devices';deviceList.append(message);}
  };
  picker.addEventListener('toggle',()=>{
    form.querySelectorAll<HTMLElement>('[data-game-platform-family]').forEach(label=>label.hidden=picker.open);
    find('[data-game-device-label]').textContent=picker.open?'Platform groups':'Choose devices';
    if(picker.open)renderDevices();find('[data-game-platform-options]').scrollTop=0;
  },{signal});
  deviceMore.addEventListener('click',()=>{deviceLimit+=40;renderDevices();},{signal});
  async function shelfLoad(key:string,epoch:number,requestSignal:AbortSignal){
    const shelf=shelves.get(key)!,host=find(`[data-game-shelf="${key}"]`),cards=host.querySelector<HTMLElement>('[data-game-shelf-cards]')!;
    if(shelf.payload)return;
    host.setAttribute('aria-busy','true');host.querySelector<HTMLElement>('[data-game-shelf-error]')!.hidden=true;
    try{
      const page=await json(gameBrowseRequest(new URLSearchParams({sort:key}),6),requestSignal);
      if(signal.aborted||requestSignal.aborted||epoch!==generation)return;
      shelf.payload=page;clear(cards);render(page.results,cards);paintSource(shelf);
      if(!page.results.length){const text=document.createElement('p');text.className='browse-category-empty';text.textContent=page.has_more?'No verified matches in this source segment. Use View all to continue.':'No games available from this source.';cards.append(text);}
    }catch(cause){if(!signal.aborted&&!requestSignal.aborted&&epoch===generation){clear(cards);host.querySelector<HTMLElement>('[data-game-shelf-error]')!.hidden=false;}}
    finally{if(!signal.aborted&&epoch===generation)host.setAttribute('aria-busy','false');}
  }
  async function loadShelves(epoch:number,requestSignal:AbortSignal){
    const queue=[...shelves.keys()].filter(key=>!shelves.get(key)!.payload);
    await Promise.all([0,1].map(async()=>{while(queue.length&&!requestSignal.aborted&&!signal.aborted&&epoch===generation)await shelfLoad(queue.shift()!,epoch,requestSignal);}));
  }
  const demand=createBrowsePaginationDemand(scrollY);
  async function load(append=false,delay=0){
    if(signal.aborted||append&&(loading||presentedCategories||!payload.has_more))return;
    const config=updateControls(),current=config.current,path=gameBrowseRequest(current,24,append?payload.cursor:null);
    if(append&&activeValues!==current.toString())return;
    request?.abort();request=new AbortController();const currentRequest=request,epoch=++generation;
    loading=true;retryAppend=append;error.hidden=true;empty.hidden=true;demand.reset(scrollY);
    clearTimeout(timer);
    if(!append){
      grid.hidden=!(payload.verification_pending&&activeValues===current.toString());sections.hidden=true;
      if(config.category){sections.hidden=false;loadingView.finish();}
      else loadingView.show(false,false,config.timeline);
    }
    region.setAttribute('aria-busy',String(!config.category));footer();
    if(delay){await new Promise<void>(resolve=>{const finish=()=>{clearTimeout(timer);currentRequest.signal.removeEventListener('abort',finish);resolve();};timer=setTimeout(finish,delay);currentRequest.signal.addEventListener('abort',finish,{once:true});});}
    if(signal.aborted||currentRequest.signal.aborted||epoch!==generation)return;
    try{
      if(config.category){
        presentedCategories=true;activeValues=current.toString();notice.textContent='';
        history.replaceState(history.state,'',gameBrowseHref(current));
        loading=false;region.setAttribute('aria-busy','false');footer();
        await loadShelves(epoch,currentRequest.signal);return;
      }
      let painted=false;
      await readVerifiedGamePage(()=>json(path,currentRequest.signal),result=>{
        if(signal.aborted||currentRequest.signal.aborted||epoch!==generation)return;
        if(!append){
          const existing=[...grid.querySelectorAll<HTMLElement>('[data-browse-card]')].map(card=>card.dataset.key);
          const desired=result.results.map(browseCardKey);
          const samePrefix=existing.every((key,index)=>key===desired[index]);
          if((!painted&&activeValues!==current.toString())||!samePrefix){clear(grid);seen.clear();}
        }
        grid.className=config.timeline?'browse-timeline':'browse-grid';
        render(result.results,grid,config.timeline,true);painted=true;
        if(!result.verification_pending&&!result.verification_error)payload=result;
        presentedCategories=false;paintedTimeline=config.timeline;activeValues=current.toString();
        if(result.results.length||!result.verification_pending)loadingView.finish();
        sections.hidden=true;grid.hidden=false;
        notice.textContent=result.verification_pending?'':current.get('q')&&!result.coverage_limited?current.has('min_metacritic')&&result.partial?'Some games have no verified critic score.':'':gameSourceContext(result);
        history.replaceState(history.state,'',gameBrowseHref(current));
      },currentRequest.signal);
    }catch(cause){
      if(signal.aborted||currentRequest.signal.aborted||epoch!==generation)return;
      loadingView.finish();sections.hidden=!presentedCategories;grid.hidden=presentedCategories;
      root.dataset.browseView=paintedTimeline?'timeline':'grid';
      error.querySelector('span')!.textContent=cause instanceof Error?cause.message:'Unable to load games. Try again.';error.hidden=false;
      if(cause instanceof BrowseReadError&&cause.status===409)retryAppend=false;
      find('[data-browse-retry]').textContent=cause instanceof BrowseReadError&&cause.status===409?'Refresh results':'Try again';
    }finally{if(!signal.aborted&&epoch===generation){loading=false;region.setAttribute('aria-busy','false');footer();}}
  }
  function changed(delay=0){void load(false,delay);}
  function reset(){
    deviceSearch.value='';deviceLimit=40;picker.open=false;deviceList.replaceChildren();
    for(const key of ['q','genres','platforms','start','end'])field(key).value='';field('sort').value='all';field('min_metacritic').value='0';field('view').value='grid';
    selectedPlatforms.clear();form.querySelectorAll<HTMLInputElement>('[type=checkbox]').forEach(input=>input.checked=false);syncGenres();syncPlatforms();paintRanges();refreshSelect(field('sort'));changed();
  }
  form.addEventListener('submit',event=>{event.preventDefault();changed();},{signal});
  form.addEventListener('input',event=>{
    if(syncing)return;const target=event.target as HTMLInputElement;
    if(target.hasAttribute('data-game-device-search')){deviceLimit=40;renderDevices();return;}
    if(target.name==='q'||target.name==='min_metacritic'||target.name==='start'||target.name==='end')changed(target.name==='q'?300:200);
  },{signal});
  form.addEventListener('change',event=>{
    if(syncing)return;const target=event.target as HTMLInputElement;
    if(target.name==='genre'){
      if(form.querySelectorAll('[name=genre]:checked').length>12){target.checked=false;notice.textContent='Choose up to 12 genres.';return;}syncGenres();
    }else if(target.name==='platform'){
      const group=groups.find(group=>group.id===target.value);
      if(target.checked)selectedPlatforms.add(target.value);else selectedPlatforms.delete(target.value);
      if(group){for(const device of group.devices)selectedPlatforms.delete(device.id);}
      else{const parent=groups.find(group=>group.devices.some(device=>device.id===target.value));if(parent)selectedPlatforms.delete(parent.id);}
      syncPlatforms();
    }else if(!['sort','start','end','min_metacritic'].includes(target.name))return;
    if(target.name==='sort'&&target.value!=='all'&&target.value!=='trending')field('min_metacritic').value='0';
    paintRanges();changed();
  },{signal});
  find('[data-browse-search-clear]').addEventListener('click',()=>{field('q').value='';field('q').focus();changed();},{signal});
  find('[data-game-high-rated]').addEventListener('click',()=>{field('min_metacritic').value='80';paintRanges();changed();},{signal});
  root.addEventListener('click',event=>{
    const target=event.target as Element,resetButton=target.closest('[data-reset],[data-browse-clear]');if(resetButton){reset();return;}
    const link=target.closest<HTMLAnchorElement>('[data-game-layout],[data-game-view-all]');
    if(link){if(event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;event.preventDefault();if(link.dataset.gameLayout)field('view').value=link.dataset.gameLayout;else{field('view').value='grid';field('sort').value=link.dataset.gameViewAll!;}if(field('view').value==='timeline'){field('sort').value='newest';field('min_metacritic').value='0';}refreshSelect(field('sort'));changed();return;}
    const retry=target.closest<HTMLElement>('[data-game-shelf-retry]');if(retry){request??=new AbortController();void shelfLoad(retry.dataset.gameShelfRetry!,generation,request.signal);return;}
    const chip=target.closest<HTMLElement>('[data-game-chip]');if(!chip)return;
    const key=chip.dataset.gameChip!;
    if(key.startsWith('genre:')){const id=key.slice(6);const input=form.querySelector<HTMLInputElement>(`[name=genre][value="${id}"]`);if(input)input.checked=false;syncGenres();}
    else if(key.startsWith('platform:')){const id=key.slice(9),group=groups.find(group=>group.id===id);for(const value of [id,...(group?.devices.map(device=>device.id)||[])])selectedPlatforms.delete(value);syncPlatforms();}
    else field(key).value=key==='min_metacritic'?'0':'';
    paintRanges();changed();
  },{signal});
  more.addEventListener('click',()=>void load(true),{signal});find('[data-browse-retry]').addEventListener('click',()=>void load(retryAppend),{signal});
  let near=false,frame=0;
  const observer=new IntersectionObserver(entries=>{near=entries.some(entry=>entry.isIntersecting);},{rootMargin:'400px'});observer.observe(find('[data-browse-sentinel]'));
  window.addEventListener('scroll',()=>{demand.advance(scrollY,!loading&&!presentedCategories);if(frame)return;frame=requestAnimationFrame(()=>{frame=0;if(near&&!loading&&!presentedCategories&&payload.has_more&&demand.consume())void load(true);});},{passive:true,signal});
  document.addEventListener('anylist:cache-invalidated',()=>cache.clear(),{signal});
  signal.addEventListener('abort',()=>{request?.abort();clearTimeout(timer);observer.disconnect();cancelAnimationFrame(frame);},{once:true});
  syncGenres();syncPlatforms();updateControls();footer();
  if(initial.categories){request=new AbortController();void loadShelves(generation,request.signal);}
  else if(payload.verification_pending)void load(false);
}
