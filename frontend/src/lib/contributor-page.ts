import {mountWorkProgress,workProgress} from './contributor-work-progress';
import { bindBrowseCardActions, bindBrowseCardTouch, paintBrowseCardState, sameBrowseTitle } from './browse-card-actions';
import { contributorParams, contributorYear, contributorMediaLabel,contributorCreditScope, workHref, type ContributorPage, type ContributorWork } from './contributor-page-data';
import { editorStore } from './editor-store';
import { applyResponsiveArtwork } from './responsive-artwork';
import { prepareBrowsePoster } from './browse-loading';
import { mountContributorBiography } from './contributor-biography';
import { createContentTransition, mountDetailsMotion } from './content-motion';
import { initializeScrollMotion } from './scroll-motion';
import { cancelUiMotion, reveal } from './ui-motion';
import { mountPortraitViewer } from './image-viewer';
import {createPrediction,bindPredictivePagination} from './predictive-loading';
import { setLocalDateTime } from './date-time-dom';
import { createSectionScroll } from './section-scroll';
import {browseCardKey} from './browse-card-data';
import {contributorSourceRefresh} from './contributor-source-refresh';
import {contributorAvailability,contributorMediaOptionDisabled,contributorMediaSwitchVisibility,contributorRoleState,disabledContributorOption,disabledContributorRoleOption,mergeContributorDomains,mergePersonTabRoles,personSwitchVisibility,type ContributorAvailability} from './contributor-availability';
import {mountGamePersonImages} from './game-person-images';
import {contributorPages} from './contributor-pages';
import {bindContributorTabIntent} from './contributor-tab-intent';
import {createContributorWorksMotion, orderContributorNodes} from './contributor-works-motion';
import {mountContributorBanner} from './contributor-banner';
import {replaceContributorSelection} from './contributor-selection';
import {mountContributorAppearances, paintWorkAppearance} from './contributor-appearances';
import {mountContributorEpisodeCredits} from './contributor-episode-credits';

export function initializeContributorPage(root: HTMLElement, signal: AbortSignal) {
  if(root.dataset.mediaType==='game')mountGamePersonImages(root,signal);
  mountContributorBiography(root, signal);
  mountPortraitViewer(root, signal);
  const data: ContributorPage = JSON.parse(root.querySelector('[data-contributor-data]')!.textContent!);
  const region = root.querySelector<HTMLElement>('[data-contributor-works]')!;
  const isPerson = data.kind !== 'studio';
  if (isPerson) mountContributorAppearances(region, signal);
  const cancelEpisodeCredits = isPerson ? mountContributorEpisodeCredits(region, data.key, signal) : undefined;
  const switchView = createContentTransition(region, signal);
  const worksMotion = createContributorWorksMotion(region, signal);
  let requestedView = root.dataset.view;
  const aliases = root.querySelector<HTMLDetailsElement>('.contributor-aliases');
  if (aliases) mountDetailsMotion(aliases, signal);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (!signal.aborted && root.isConnected) initializeScrollMotion(root, signal, '.browse-card-body', 'contributor-scroll-reveal', {
      // SSR content is already painted, including restored scroll positions.
      // Animate only cards that arrive later or enter from below the viewport.
      animateInitial:false, downwardOnly:true, once:true,
      appearedAttribute:'data-browse-appeared',
    });
    root.setAttribute('data-contributor-arrivals-ready','');
  }));
  signal.addEventListener('abort', () => cancelUiMotion(root), {once:true});
  const form = root.querySelector<HTMLFormElement>('[data-contributor-filters]')!;
  const listButton=form.querySelector<HTMLButtonElement>('[data-contributor-list-toggle]');
  // Layout or scroll restoration can place a new control under a stationary pointer.
  let hoveredControl:HTMLElement|null=null;
  const clearHover=()=>{
    hoveredControl?.removeAttribute('data-contributor-pointer-hover');
    hoveredControl=null;
  };
  root.addEventListener('pointermove',event=>{
    if(event.pointerType!=='mouse'||!(event.movementX||event.movementY)||!(event.target instanceof Element)||
      hoveredControl?.contains(event.target))return;
    clearHover();
    hoveredControl=event.target.closest<HTMLElement>('[data-contributor-list-toggle],[data-browse-card]');
    hoveredControl?.setAttribute('data-contributor-pointer-hover','');
  },{signal,passive:true});
  root.addEventListener('pointerout',event=>{
    if(hoveredControl&&!hoveredControl.contains(event.relatedTarget as Node|null))clearHover();
  },{signal,passive:true});
  form.addEventListener('submit',clearHover,{signal});
  signal.addEventListener('abort',clearHover,{once:true});
  const paintListButton=()=>listButton?.setAttribute('aria-pressed',String((form.elements.namedItem('list_scope') as HTMLInputElement|null)?.checked===true));
  listButton?.addEventListener('click',()=>{
    const scope=form.elements.namedItem('list_scope') as HTMLInputElement;
    scope.checked=!scope.checked;form.requestSubmit();
  },{signal});
  const template = root.querySelector<HTMLTemplateElement>('[data-contributor-template]')!;
  const more = root.querySelector<HTMLButtonElement>('[data-contributor-more]')!;
  const error = root.querySelector<HTMLElement>('[data-contributor-error]')!;
  const items = new Map<HTMLElement, ContributorWork>();
  let writeVersion=0;
  const gameEdits=new Map<number,{version:number;status:string|null;score:number|null}>();
  document.addEventListener('anylist:editor-state',event=>{
    const title=(event as CustomEvent<import('./editor-store').EditorTitle>).detail;
    if(title.type==='game'&&title.id)gameEdits.set(title.id,{version:++writeVersion,status:title.entry?.status||null,score:title.entry?.score??null});
  },{signal});
  const ready = new Set<string>();
  const params = contributorParams(new URLSearchParams(root.dataset.contributorQuery ?? location.search), data.list_count !== null);
  let sourcePending=false,pagePending=false;
  const paintProgress=mountWorkProgress(root,signal);
  const paintCaption=(caption:HTMLElement,item:{characters:string[];roles:string[];characters_pending?:boolean})=>{
    caption.textContent=[...item.characters,...item.roles.filter(role=>!['Actor','Production'].includes(role))].join(' / ');
    caption.title=caption.textContent;caption.toggleAttribute('data-character-pending',!!item.characters_pending&&!item.characters.length);
  };
  const savedRoles=data.tab_roles,savedDomains=data.tab_domains;
  let availability:ContributorAvailability={key:data.key,roles:savedRoles,domains:savedDomains,pending:false};
  const domainSwitch=form.querySelector<HTMLFieldSetElement>('[data-contributor-media-switch]')!;
  const screenSwitch=form.querySelector<HTMLFieldSetElement>('[data-contributor-screen-types]')!;
  const roleSwitch=form.querySelector<HTMLFieldSetElement>('[data-contributor-role-switch]');
  const canSwitchDomains=domainSwitch.querySelectorAll('input[name=domain]').length>1;
  const activeDomains=()=>data.kind==='studio'?availability.domains:availability.roles?.[data.kind];
  const selectedMedia=()=>params.get('media_type')||'all';
  const canRetainSelection=()=>{
    const domains=activeDomains();
    return items.size>0||!domains||!disabledContributorOption(
      selectedMedia()==='game'?'domain':'media_type',selectedMedia(),domains);
  };
  const paintAvailability=()=>{
    const domains=activeDomains();
    const media=selectedMedia(),games=media==='game',hasWorks=items.size>0;
    for(const input of form.querySelectorAll<HTMLInputElement>('input[name=domain],input[name=media_type]')){
      input.disabled=contributorMediaOptionDisabled(input.name,input.value,media,hasWorks,domains);
    }
    if(data.kind!=='studio')for(const input of form.querySelectorAll<HTMLInputElement>('input[name=role]')){
      input.disabled=disabledContributorRoleOption(availability.roles,data.kind,input.value==='staff'?'staff':'actor',media,hasWorks);
    }
    let visible={domain:true,screen:true};
    if(data.kind==='studio'){
      if(savedDomains)visible=contributorMediaSwitchVisibility(domains,media,hasWorks,savedDomains);
    }else if(roleSwitch&&savedRoles){
      const person=personSwitchVisibility(availability.roles,data.kind,media,hasWorks,savedRoles);
      roleSwitch.hidden=!person.role;
      visible=person;
    }
    domainSwitch.hidden=!canSwitchDomains||!visible.domain;
    screenSwitch.hidden=games||!visible.screen;
    screenSwitch.disabled=games;
  };
  const paintBanner=mountContributorBanner(root.querySelector<HTMLImageElement>('[data-contributor-banner]')!,signal);
  const updateBanner=()=>paintBanner([...region.querySelectorAll<HTMLElement>('[data-work-key]')]
    .filter(card=>!card.hidden).map(card=>items.get(card)!).filter(Boolean),data.kind==='studio'?data.banner:null);
  const portrait = root.querySelector<HTMLImageElement>('.contributor-portrait img');
  const missingPortrait = () => {
    if (portrait) portrait.hidden = true;
    root.querySelector<HTMLButtonElement>('[data-portrait-image]')!.disabled = true;
    root.querySelector<HTMLElement>('[data-contributor-portrait-fallback]')!.hidden = false;
  };
  portrait?.addEventListener('error', missingPortrait, {signal});
  if (portrait?.complete && !portrait.naturalWidth) missingPortrait();
  const restoreFilterControls=()=>{
    const type=params.get('media_type')||'all';
    form.querySelectorAll<HTMLInputElement>('[name=domain]').forEach(input=>input.checked=input.value===(type==='game'?'game':'movies-series'));
    form.querySelectorAll<HTMLInputElement>('[name=media_type]').forEach(input=>input.checked=input.value===(type==='game'?'all':type));
    form.querySelectorAll<HTMLInputElement>('[name=role]').forEach(input=>input.checked=input.value===data.kind);
    const scope=form.elements.namedItem('list_scope') as HTMLInputElement|null;
    if(scope)scope.checked=params.get('list_scope')==='in';
    paintListButton();
    paintAvailability();
  };
  form.addEventListener('change', () => {
    form.requestSubmit();
  }, {signal});
  const scrollToWorks = createSectionScroll(signal);
  root.querySelector<HTMLAnchorElement>('.contributor-list-link')?.addEventListener('click',event=>{
    if(event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
    const scope=form.elements.namedItem('list_scope') as HTMLInputElement|null;
    if(!scope)return;
    event.preventDefault();
    if(!scope.checked){
      scope.checked=true;
      const action=form.action;
      form.action=`${action.split('#')[0]}#contributor-works`;
      try { form.requestSubmit(); } finally { form.action=action; }
      return;
    }
    const works = root.querySelector<HTMLElement>('.contributor-works');
    if (works) scrollToWorks(works);
  },{signal});
  const compact=root.querySelector<HTMLInputElement>('[data-contributor-compact]');
  const paintLayoutControls=()=>{
    if(compact)compact.checked=requestedView==='grid';
    root.querySelectorAll<HTMLElement>('[data-contributor-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.contributorView===requestedView)));
  };
  const changeLayout=(view:string)=>{
      if (requestedView === view) return;
      requestedView = view;
      worksMotion.cancel();
      void switchView(() => {
        root.dataset.view = view;
        region.classList.toggle('browse-grid', view === 'grid');
      });
      (form.elements.namedItem('view') as HTMLInputElement).value = view;
      paintLayoutControls();
      const url = new URL(location.href); url.searchParams.set('view', view);
      history.replaceState(history.state, '', url);
  };
  compact?.addEventListener('change',()=>changeLayout(compact.checked?'grid':'timeline'),{signal});
  root.querySelectorAll<HTMLButtonElement>('[data-contributor-view]').forEach(button => {
    button.addEventListener('click',()=>changeLayout(button.dataset.contributorView!),{signal});
  });
  const showError = (message: string) => { error.textContent = message; error.hidden = false; error.setAttribute('data-motion-fade',''); reveal(error); };
  const visible = (item: ContributorWork) => params.get('list_scope') !== 'in' || !!item.list_status;
  const updateEmpty = () => {
    region.querySelectorAll<HTMLElement>('[data-contributor-year]').forEach(group => {
      group.hidden = ![...group.querySelectorAll<HTMLElement>('[data-browse-card]')].some(card => !card.hidden);
    });
    const hasWorks=[...items.keys()].some(card => !card.hidden);
    const pending=!!filterRequest||params.get('media_type')==='game'&&sourcePending&&!hasWorks;
    const busy=!!filterRequest||pagePending||params.get('media_type')==='game'&&sourcePending;
    const indicator=root.querySelector<HTMLElement>('[data-contributor-busy]')!;
    indicator.dataset.active=String(busy);indicator.setAttribute('aria-hidden',String(!busy));
    region.setAttribute('aria-busy',String(busy));
    root.querySelector<HTMLElement>('[data-contributor-empty]')!.hidden = hasWorks||pending;
    root.querySelector<HTMLElement>('[data-contributor-loading]')!.hidden = !pending;
    updateBanner();
  };
  const paint = (card: HTMLElement, item: ContributorWork) => {
    const href = workHref(item);
    card.querySelectorAll<HTMLAnchorElement>('a').forEach(link => {
      if (href) link.setAttribute('href', href); else link.removeAttribute('href');
    });
    paintBrowseCardState(card, item); card.hidden = !visible(item);
  };
  const mount = (card: HTMLElement, item: ContributorWork) => {
    items.set(card, item);
    const episodes=card.querySelector<HTMLDetailsElement>('[data-contributor-episodes]');
    if(episodes){
      episodes.hidden=data.kind!=='staff'||item.type!=='series'||!item.tmdb_id;
      if(!episodes.hidden)episodes.dataset.series=String(item.tmdb_id);
    }
    if (item.type !== 'game' && data.list_count !== null && (item.id || item.tmdb_id)) editorStore.seed(item);
    paint(card, item);
    const image = card.querySelector<HTMLImageElement>('[data-poster]')!;
    const missingPoster = () => {
      image.hidden = true;
      card.querySelector<HTMLElement>('.browse-placeholder')!.hidden = false;
    };
    image.addEventListener('error', missingPoster, {signal});
    if (!image.hidden && image.complete && !image.naturalWidth) missingPoster();
    prepareBrowsePoster(image, signal, ready);
  };
  region.querySelectorAll<HTMLElement>('[data-work-key]').forEach(card => {
    const item = data.works.find(work => work.key === card.dataset.workKey);
    if (item) mount(card, item);
  });
  bindBrowseCardTouch(region, signal);
  bindBrowseCardActions(region, items, signal, updated => {
    const matches = [...items].filter(([,item]) => sameBrowseTitle(item, updated));
    const wasListed = !!matches[0]?.[1].list_status;
    if (matches.length && data.list_count !== null) {
      data.list_count += Number(!!updated.list_status) - Number(wasListed);
      root.querySelector('[data-contributor-list-count]')!.textContent = String(data.list_count);
    }
    for (const [card,item] of matches) { Object.assign(item, updated); paint(card, item); }
    updateEmpty();
  }, showError);
  const append = (works: ContributorWork[]) => {
    for (const item of works) {
      if ([...items.values()].some(old => old.key === item.key || sameBrowseTitle(old, item))) continue;
      const year = contributorYear(item);
      let group = [...region.querySelectorAll<HTMLElement>('[data-contributor-year]')].find(group => group.dataset.contributorYear === year);
      if (!group) {
        group = document.createElement('section'); group.className = 'contributor-year';
        group.dataset.contributorYear = year; group.setAttribute('aria-label', year);
        const heading = document.createElement('h3'); heading.textContent = year;
        const children = document.createElement('div'); children.className = 'contributor-year-works';
        group.append(heading, children); region.append(group);
      }
      const card = template.content.firstElementChild!.cloneNode(true) as HTMLElement;
      card.dataset.key = browseCardKey({...item,href:item.href||undefined});
      card.dataset.workKey = item.key;
      paintWorkAppearance(card, item);
      const scope=card.querySelector<HTMLElement>('[data-work-scope]');
      if(scope){scope.textContent=contributorCreditScope(item);scope.hidden=!item.credit_sources?.length;}
      card.querySelector('.browse-poster-link')!.setAttribute('aria-label', item.title);
      card.querySelector('[data-year]')!.textContent = item.year || '';
      const credit = card.querySelector<HTMLElement>('[data-work-credit]')!;
      paintCaption(credit,item);
      const date = card.querySelector<HTMLTimeElement>('[data-work-date]')!;
      date.hidden = !item.release_date; setLocalDateTime(date, item.release_date, 'month-day');
      card.querySelector<HTMLElement>('[data-work-type]')!.textContent = contributorMediaLabel(item.type);
      const image = card.querySelector<HTMLImageElement>('[data-poster]')!;
      const poster = item.appearance?.poster || item.poster;
      image.hidden = !poster;
      if (poster) applyResponsiveArtwork(image, poster, {size:'w342',sizes:'(max-width: 760px) 40vw, 185px',route:'auto'});
      const placeholder = card.querySelector<HTMLElement>('.browse-placeholder')!;
      placeholder.hidden = !!poster; placeholder.textContent = item.title.slice(0,1);
      if (!item.id && !item.tmdb_id) card.querySelector('.browse-actions')?.remove();
      card.querySelectorAll<HTMLButtonElement>('[data-browse-action]').forEach(button => {
        const action = button.dataset.browseAction;
        button.setAttribute('aria-label', `${action === 'edit' ? 'Open list editor for' : action === 'plan' ? 'Plan to watch' : action === 'watch' ? 'Set to Watching' : 'Rate and complete'} ${item.title}`);
      });
      group.querySelector('.contributor-year-works')!.append(card); mount(card,item);
    }
    updateEmpty();
  };
  root.querySelector<HTMLElement>('[data-contributor-next]')!.hidden = true;
  more.hidden = !data.has_more;
  const waitForGameWrites=(requestSignal:AbortSignal)=>new Promise<void>((resolve,reject)=>{
    const pending=()=>[...new Set([...gameEdits.keys(),...[...items.values()].filter(item=>item.type==='game'&&item.id).map(item=>item.id!)])]
      .some(id=>editorStore.hasPendingSubject({kind:'game',catalogue_id:id}));
    const cleanup=()=>{document.removeEventListener('anylist:editor-state',changed);requestSignal.removeEventListener('abort',aborted);};
    const changed=()=>{if(!pending()){cleanup();resolve();}};
    const aborted=()=>{cleanup();reject(new DOMException('Aborted','AbortError'));};
    if(requestSignal.aborted){aborted();return;}
    if(!pending()){resolve();return;}
    document.addEventListener('anylist:editor-state',changed);requestSignal.addEventListener('abort',aborted,{once:true});
    changed();
  });
  let syncSource=()=>{};
  const sourceRefresh=contributorSourceRefresh(signal,available=>{
    if(available)syncSource();
    },data.kind==='studio'?undefined:{
    get kind(){return data.kind==='staff'?'staff':'actor';},
    ids:()=>[...items.values()].filter(item=>item.type==='game'&&item.id).map(item=>item.id!),
    apply:rows=>{
      const captions=new Map(rows.map(row=>[row.id,row]));
      for(const [card,item] of items){
        const next=item.type==='game'&&item.id?captions.get(item.id):undefined;
        if(!next)continue;
        // Only public role text changes. Do not re-seed private/optimistic
        // tracking, replace cards, alter ordering, paging or scroll position.
        item.characters=next.characters;item.roles=next.roles;item.characters_pending=next.characters_pending;
        const caption=card.querySelector<HTMLElement>('[data-work-credit]');
        if(caption)paintCaption(caption,next);
      }
    }
  },state=>{
    sourcePending=workProgress(state,data.known_works).pending&&!state?.error;
    paintProgress({...state,works_pending:sourcePending},data.known_works);
    updateEmpty();
  });
  const requestPage=async(path:string,requestSignal:AbortSignal):Promise<ContributorPage>=>{
    const games=new URLSearchParams(path.split('?')[1]||'').get('media_type')==='game';
    // Wait outside provider/DB work, and discard a private read if a write began
    // during it. This protects full counts and filtered membership as well as
    // individual cards; a partial overlay cannot repair missing filtered rows.
    for(;;){
      if(games)await waitForGameWrites(requestSignal);
      const started=writeVersion;
      const response=await fetch(path,{signal:requestSignal,cache:'no-store'});
      if(!response.ok)throw Object.assign(new Error('Unable to load more works. Please try again.'),{status:response.status});
      const next:ContributorPage=await response.json();
      if(requestSignal.aborted)throw new DOMException('Aborted','AbortError');
      if(games&&started!==writeVersion)continue;
      for(const item of next.works)if(item.type==='game'&&item.id)editorStore.invalidateSubject({kind:'game',catalogue_id:item.id});
      return next;
    }
  };
  const pages=contributorPages<ContributorPage>(requestPage,signal);
  const initialQuery=new URLSearchParams(params);
  if(!initialQuery.has('media_type'))initialQuery.set('media_type','all');
  pages.seed(`/api/proxy/tracking/${root.dataset.endpoint}?${initialQuery}`,structuredClone(data));
  const prediction=createPrediction<ContributorPage>(requestPage,signal);
  let filterRequest:AbortController|undefined,revision=0;
  signal.addEventListener('abort',()=>filterRequest?.abort(),{once:true});
  document.addEventListener('anylist:cache-invalidated',()=>{prediction.cancel();pages.clear();},{signal});
  document.addEventListener('anylist:editor-state',()=>{prediction.cancel();pages.clear();},{signal});
  const nextPath=()=>{
    const query=new URLSearchParams(params);
    query.set('page',String(data.page+1));query.set('source',data.source);
    if(data.next_cursor)query.set('cursor',data.next_cursor);else query.delete('cursor');
    return `/api/proxy/tracking/${root.dataset.endpoint}?${query}`;
  };
  const loadMore=async()=>{
    if(more.disabled||filterRequest||syncing||!data.has_more||signal.aborted)return;
    const current=revision;
    pagePending=true;updateEmpty();
    more.disabled=true;more.textContent='Loading…';error.hidden=true;loading.hidden=false;
    try{
      const path=nextPath();
      const next=await prediction.consume(path,signal)??await requestPage(path,signal);
      if(signal.aborted||current!==revision)return;
      append(next.works);
      sourceRefresh.update(next.game_company_status||next.game_person_status);
      data.page=next.page;data.source=next.source;data.next_cursor=next.next_cursor;data.has_more=next.has_more;
      more.hidden=!data.has_more;
    }catch(cause){
      if(!signal.aborted&&current===revision){
        if((cause as Error & {status?:number}).status===409)form.requestSubmit();
        else showError(cause instanceof Error?cause.message:'Unable to load more works.');
      }
    }
    finally{pagePending=false;if(!signal.aborted&&current===revision){more.disabled=false;more.textContent='Load more';updateEmpty();if(syncAgain)void synchronize();}}
  };
  more.addEventListener('click',()=>void loadMore(),{signal});
  type RetainedWorks={nodes:Node[];items:Map<HTMLElement,ContributorWork>;data:ContributorPage;params:URLSearchParams;view:string|undefined};
  const retained=new Map<string,RetainedWorks>();
  let retainCurrent=true;
  const domain=(query:URLSearchParams)=>query.get('media_type')==='game'?'game':'movies-series';
  const retainedKey=(query:URLSearchParams)=>`${query.get('role')||''}:${domain(query)}`;
  const invalidateRetained=()=>{retained.clear();retainCurrent=false;};
  document.addEventListener('anylist:cache-invalidated',invalidateRetained,{signal});
  document.addEventListener('anylist:editor-state',invalidateRetained,{signal});
  signal.addEventListener('abort',()=>retained.clear(),{once:true});
  form.addEventListener('submit',event=>{
    event.preventDefault();prediction.cancel();filterRequest?.abort();switchView.cancel();
    cancelEpisodeCredits?.();
    paintListButton();
    const worksAnchor = new URL(form.action).hash === '#contributor-works';
    const request=new AbortController(),current=++revision;
    filterRequest=request;error.hidden=true;region.setAttribute('aria-busy','true');
    updateEmpty();
    root.querySelector<HTMLElement>('[data-contributor-loading]')!.hidden=items.size>0;
    root.querySelector<HTMLElement>('[data-contributor-empty]')!.hidden=true;
    const submitted=new URLSearchParams();
    new FormData(form).forEach((value,key)=>submitted.set(key,String(value)));
    // A verified-empty selected option can still be opened by direct link.
    // Disabled controls are absent from FormData; preserve the selection while
    // the viewer changes another filter, without forcing a route switch.
    form.querySelectorAll<HTMLInputElement>('input[name=domain]:checked,input[name=media_type]:checked,input[name=role]:checked').forEach(input=>submitted.set(input.name,input.value));
    let query=contributorParams(submitted,data.list_count!==null);
    const switching=domain(query)!==domain(params);
    const cached=switching?retained.get(retainedKey(query)):undefined;
    if(switching&&retainCurrent&&!syncing){
      if(canRetainSelection())retained.set(retainedKey(params),{nodes:[...region.childNodes],items:new Map(items),data:{...data},params:new URLSearchParams(params),view:requestedView});
      else retained.delete(retainedKey(params));
    }
    if(cached){query=new URLSearchParams(cached.params);requestedView=cached.view;}
    else if(switching&&domain(query)==='movies-series')query.set('media_type','all');
    region.hidden=false;region.inert=true;
    const readSelection=async()=>{
      for(;;){
        try{return await pages.get(`/api/proxy/tracking/${root.dataset.endpoint}?${query}`,request.signal);}
        catch(cause){
          if((cause as Error).name!=='AbortError'||signal.aborted||request.signal.aborted)throw cause;
          // Invalidation discarded this snapshot, not the viewer's selection.
          // Retry with current evidence while superseded consumers stay aborted.
        }
      }
    };
    const applySelection=async(next:ContributorPage)=>{
      if(signal.aborted||current!==revision)return;
      worksMotion.cancel();
      await switchView(()=>{
        if(signal.aborted||current!==revision)return;
        replaceContributorSelection(region,()=>{
          // Replace only the works; keep biography, controls, focus and viewport intact.
          region.replaceChildren(...(cached?.nodes||[]));items.clear();
          if(cached)for(const [card,item] of cached.items)items.set(card,item);
          Object.assign(data,next);
          for(const key of [...params.keys()])params.delete(key);
          query.forEach((value,key)=>params.set(key,value));
          root.dataset.mediaType=params.get('media_type')||'all';
          sourceRefresh.update(params.get('media_type')==='game'?next.game_company_status||next.game_person_status:null);
          const count=root.querySelector('[data-contributor-list-count]');
          if(count)count.textContent=String(data.list_count||0);
          const kicker=root.querySelector<HTMLElement>('[data-contributor-kicker]')!;
          if(isPerson){
            data.primary_label=next.primary_label;
            kicker.textContent=data.primary_label||'';
          }else{
            const roles=root.querySelector<HTMLElement>('[data-contributor-roles]')!;
            roles.textContent=data.roles.slice(0,4).join(' / ');roles.hidden=!data.roles.length;
            kicker.textContent=domain(params)==='game'?'Game studio':'Production studio';
          }
          const listLink=root.querySelector<HTMLAnchorElement>('.contributor-list-link');
          if(listLink){const href=new URL(location.pathname,location.origin);href.search=query.toString();href.searchParams.set('list_scope','in');href.searchParams.set('view',requestedView||'timeline');href.hash='contributor-works';listLink.href=href.href;}
          if(!cached)append(next.works);
          retainCurrent=true;
          if(switching)restoreFilterControls();
          else paintAvailability();
          if(cached){root.dataset.view=requestedView;region.classList.toggle('browse-grid',requestedView==='grid');(form.elements.namedItem('view') as HTMLInputElement).value=requestedView||'timeline';paintLayoutControls();}
          more.hidden=!data.has_more;more.disabled=false;more.textContent='Load more';
          const notice=root.querySelector<HTMLElement>('[data-contributor-notice]')!;
          notice.textContent=next.notice||'';notice.hidden=!next.notice;
          const url=new URL(location.href);url.search=query.toString();
          if (worksAnchor) url.hash='contributor-works';
          url.searchParams.set('view',requestedView||'timeline');history.replaceState(history.state,'',url);
        });
        if (worksAnchor) {
          const works=root.querySelector<HTMLElement>('.contributor-works');
          if (works) scrollToWorks(works);
        }
      });
    };
    const finish=()=>{if(!signal.aborted&&current===revision){filterRequest=undefined;region.removeAttribute('aria-busy');region.hidden=false;region.inert=false;root.querySelector<HTMLElement>('[data-contributor-loading]')!.hidden=true;updateEmpty();more.disabled=false;more.textContent='Load more';if(syncAgain)void synchronize();}};
    void (cached?Promise.resolve(cached.data):readSelection()).then(applySelection).catch(cause=>{
      if(!signal.aborted&&!request.signal.aborted&&current===revision&&(cause as Error).name!=='AbortError'){restoreFilterControls();showError(cause instanceof Error?cause.message:'Unable to load works.');}
    }).finally(finish);
  },{signal});
  let syncing=false,syncAgain=false;
  let syncRetry:ReturnType<typeof setTimeout>|undefined,syncFailures=0;
  signal.addEventListener('abort',()=>clearTimeout(syncRetry),{once:true});
  const loading=root.querySelector<HTMLElement>('[data-contributor-loading]')!;
  const pathFor=(query:URLSearchParams)=>`/api/proxy/tracking/${root.dataset.endpoint}?${query}`;
  const warmSibling=(domain:string)=>{
    if(signal.aborted)return;
    const query=new URLSearchParams(params);
    query.delete('page');query.delete('source');query.delete('cursor');
    query.set('media_type',domain==='game'?'game':'all');
    void pages.get(pathFor(contributorParams(query,data.list_count!==null))).catch(()=>{});
  };
  const synchronize=async()=>{
    if(signal.aborted)return;
    pages.forget(path=>new URLSearchParams(path.split('?')[1]).get('media_type')==='game');
    if(syncing||filterRequest||more.disabled){syncAgain=true;return;}
    syncing=true;syncAgain=false;prediction.cancel();
    updateEmpty();
    clearTimeout(syncRetry);
    const current=revision,requestedWrites=writeVersion,windowPages=data.page;
    try{
      const query=new URLSearchParams(params);query.delete('page');query.delete('source');query.delete('cursor');
      let next=await requestPage(pathFor(query),signal);
      const works=[...next.works];
      for(let page=1;page<windowPages&&next.has_more;page++){
        query.set('page',String(next.page+1));query.set('source',next.source);
        if(next.next_cursor)query.set('cursor',next.next_cursor);else query.delete('cursor');
        next=await requestPage(pathFor(query),signal);works.push(...next.works);
      }
      if(signal.aborted||current!==revision)return;
      if(requestedWrites!==writeVersion){syncAgain=true;return;}
      // Reuse mounted cards and preserve the top visible card's position. New
      // works arrive chronologically inside the user's existing loaded window.
      const animateWorks=worksMotion.capture();
      const anchor=region.getBoundingClientRect().top<0?[...region.querySelectorAll<HTMLElement>('[data-work-key]')].find(card=>!card.hidden&&card.getBoundingClientRect().bottom>0&&card.getBoundingClientRect().top<innerHeight):undefined;
      const top=anchor?.getBoundingClientRect().top;
      const focused=document.activeElement instanceof HTMLElement?document.activeElement:null;
      const wanted=new Set(works.map(work=>work.key));
      for(const [card,item] of items)if(!wanted.has(item.key)){items.delete(card);card.remove();}
      for(const work of works){
        const existing=[...items].find(([,item])=>item.key===work.key);
        if(existing){
          Object.assign(existing[1],work);paint(existing[0],existing[1]);
          const caption=existing[0].querySelector<HTMLElement>('[data-work-credit]');
          if(caption)paintCaption(caption,work);
        }
      }
      append(works);
      for(const group of region.querySelectorAll<HTMLElement>('[data-contributor-year]')){
        if(!group.querySelector('[data-browse-card]'))group.remove();
      }
      const groups=[...region.querySelectorAll<HTMLElement>('[data-contributor-year]')];
      groups.sort((a,b)=>(b.dataset.contributorYear==='Undated'?'':b.dataset.contributorYear!).localeCompare(a.dataset.contributorYear==='Undated'?'':a.dataset.contributorYear!));
      orderContributorNodes(region,groups);
      const cardsByKey=new Map([...items].map(([card,item])=>[item.key,card]));
      for(const group of groups){
        const cards=works.filter(work=>contributorYear(work)===group.dataset.contributorYear)
          .map(work=>cardsByKey.get(work.key)!).filter(Boolean);
        orderContributorNodes(group.querySelector<HTMLElement>('.contributor-year-works')!,cards);
      }
      if(anchor?.isConnected&&top!==undefined)window.scrollBy({top:anchor.getBoundingClientRect().top-top,behavior:'instant'});
      if(focused?.isConnected&&document.activeElement!==focused)focused.focus({preventScroll:true});
      data.page=next.page;data.source=next.source;data.next_cursor=next.next_cursor;data.has_more=next.has_more;
      data.total=next.total;data.list_count=next.list_count;data.known_works=next.known_works;
      if(isPerson){
        data.primary_label=next.primary_label;
        root.querySelector<HTMLElement>('[data-contributor-kicker]')!.textContent=data.primary_label||'';
      }
      paintAvailability();
      const count=root.querySelector('[data-contributor-list-count]');if(count)count.textContent=String(next.list_count||0);
      more.hidden=!data.has_more;updateEmpty();
      animateWorks();
      syncFailures=0;
    }catch{
      // A final source revision must survive a transient browser read failure.
      if(!signal.aborted&&current===revision&&++syncFailures<=3)
        syncRetry=setTimeout(()=>void synchronize(),3000*syncFailures);
    }
    finally{syncing=false;if(!signal.aborted)updateEmpty();if(syncAgain&&!filterRequest&&!signal.aborted)void synchronize();}
  };
  syncSource=()=>{
    void synchronize();
    pages.forget(path=>new URLSearchParams(path.split('?')[1]).get('media_type')!=='game');
  };
  restoreFilterControls();
  root.dataset.enhanced = '';
  contributorAvailability(root.dataset.endpoint!,data.key,signal,next=>{
    const previousAvailability=availability.roles||availability.domains;
    const previous=activeDomains();
    availability=next.roles?{...next,roles:mergePersonTabRoles(availability.roles,next.roles)}
      :next.domains?{...next,domains:mergeContributorDomains(availability.domains,next.domains)}:next;
    const current=activeDomains();
    paintAvailability();
    if(previousAvailability&&JSON.stringify(previousAvailability)!==JSON.stringify(availability.roles||availability.domains)){
      pages.clear();invalidateRetained();
      const media=selectedMedia();
      const available=(domains:NonNullable<typeof current>)=>contributorRoleState(domains,media)==='available';
      // A verified negative can disable the selected radio without leaving or reloading it.
      if(previous&&current&&!available(previous)&&available(current)&&!items.size&&!filterRequest)form.requestSubmit();
    }
  });
  sourceRefresh.update(data.game_company_status||data.game_person_status);
  bindContributorTabIntent(form,signal,warmSibling);
  bindPredictivePagination(region,signal,()=>!filterRequest&&!syncing&&data.has_more&&!more.disabled&&error.hidden,
    ()=>prediction.prefetch(nextPath()),()=>void loadMore());

}
