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
import {contributorAvailability,disabledContributorOption} from './contributor-availability';
import {mountGamePersonImages} from './game-person-images';
import {contributorPages} from './contributor-pages';
import {bindContributorTabIntent} from './contributor-tab-intent';

export function initializeContributorPage(root: HTMLElement, signal: AbortSignal) {
  if(root.dataset.mediaType==='game')mountGamePersonImages(root,signal);
  mountContributorBiography(root, signal);
  mountPortraitViewer(root, signal);
  const data: ContributorPage = JSON.parse(root.querySelector('[data-contributor-data]')!.textContent!);
  const region = root.querySelector<HTMLElement>('[data-contributor-works]')!;
  const switchView = createContentTransition(region, signal);
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
  let sourcePending=false;
  let availabilityChanged=(_state:string)=>{};
  if(root.dataset.contributorAvailability==='true')contributorAvailability(root.dataset.endpoint!,data.key,signal,domains=>{
    form.querySelectorAll<HTMLInputElement>('input[name=domain],input[name=media_type]').forEach(input=>{
      input.disabled=disabledContributorOption(input.name,input.value,domains);
    });
    availabilityChanged(JSON.stringify(domains));
  });
  const banner = root.querySelector<HTMLImageElement>('[data-contributor-banner]');
  const missingBanner = () => {
    if (!banner) return;
    banner.closest('.contributor-banner')?.classList.remove('is-logo');
    const fallback = banner.dataset.bannerFallback;
    delete banner.dataset.bannerFallback;
    if (fallback && fallback !== banner.getAttribute('src')) banner.src = fallback;
    else banner.hidden = true;
  };
  banner?.addEventListener('error', missingBanner, {signal});
  if (banner?.complete && !banner.naturalWidth) missingBanner();
  const portrait = root.querySelector<HTMLImageElement>('.contributor-portrait img');
  const missingPortrait = () => {
    if (portrait) portrait.hidden = true;
    root.querySelector<HTMLButtonElement>('[data-portrait-image]')!.disabled = true;
    root.querySelector<HTMLElement>('[data-contributor-portrait-fallback]')!.hidden = false;
  };
  portrait?.addEventListener('error', missingPortrait, {signal});
  if (portrait?.complete && !portrait.naturalWidth) missingPortrait();
  root.dataset.enhanced = '';
  const syncDomainFields=()=>{
    const movies = form.querySelector<HTMLFieldSetElement>('[data-contributor-screen-types]')!;
    const games = (form.elements.namedItem('domain') as RadioNodeList).value === 'game';
    movies.hidden = movies.disabled = games;
  };
  const restoreFilterControls=()=>{
    const type=params.get('media_type')||'all';
    form.querySelectorAll<HTMLInputElement>('[name=domain]').forEach(input=>input.checked=input.value===(type==='game'?'game':'movies-series'));
    form.querySelectorAll<HTMLInputElement>('[name=media_type]').forEach(input=>input.checked=input.value===(type==='game'?'all':type));
    const scope=form.elements.namedItem('list_scope') as HTMLInputElement|null;
    if(scope)scope.checked=params.get('list_scope')==='in';
    syncDomainFields();
  };
  form.addEventListener('change', () => {
    syncDomainFields();
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
  root.querySelectorAll<HTMLButtonElement>('[data-contributor-view]').forEach(button => {
    button.addEventListener('click', () => {
      const view = button.dataset.contributorView!;
      if (requestedView === view) return;
      requestedView = view;
      void switchView(() => {
        root.dataset.view = view;
        region.classList.toggle('browse-grid', view === 'grid');
      });
      (form.elements.namedItem('view') as HTMLInputElement).value = view;
      root.querySelectorAll('[data-contributor-view]').forEach(other => other.setAttribute('aria-pressed', String((other as HTMLElement).dataset.contributorView === view)));
      const url = new URL(location.href); url.searchParams.set('view', view);
      history.replaceState(history.state, '', url);
    }, {signal});
  });
  const showError = (message: string) => { error.textContent = message; error.hidden = false; error.setAttribute('data-motion-fade',''); reveal(error); };
  const visible = (item: ContributorWork) => params.get('list_scope') !== 'in' || !!item.list_status;
  const updateEmpty = () => {
    region.querySelectorAll<HTMLElement>('[data-contributor-year]').forEach(group => {
      group.hidden = ![...group.querySelectorAll<HTMLElement>('[data-browse-card]')].some(card => !card.hidden);
    });
    const hasWorks=[...items.keys()].some(card => !card.hidden);
    const pending=!!filterRequest||params.get('media_type')==='game'&&sourcePending&&!hasWorks;
    root.querySelector<HTMLElement>('[data-contributor-empty]')!.hidden = hasWorks||pending;
    root.querySelector<HTMLElement>('[data-contributor-loading]')!.hidden = !pending;
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
      card.querySelector('h2')!.textContent = item.title;
      const scope=card.querySelector<HTMLElement>('[data-work-scope]');
      if(scope){scope.textContent=contributorCreditScope(item);scope.hidden=!item.credit_sources?.length;}
      card.querySelector('.browse-poster-link')!.setAttribute('aria-label', item.title);
      card.querySelector('[data-year]')!.textContent = item.year || '';
      const credit = card.querySelector<HTMLElement>('[data-work-credit]')!;
      credit.textContent = [...item.characters,...item.roles.filter(role => !['Actor','Production'].includes(role))].join(' / ');
      credit.title = credit.textContent;
      const date = card.querySelector<HTMLTimeElement>('[data-work-date]')!;
      date.hidden = !item.release_date; setLocalDateTime(date, item.release_date, 'month-day');
      card.querySelector<HTMLElement>('[data-work-type]')!.textContent = contributorMediaLabel(item.type);
      const image = card.querySelector<HTMLImageElement>('[data-poster]')!;
      image.hidden = !item.poster;
      if (item.poster) applyResponsiveArtwork(image, item.poster, {size:'w342',sizes:'(max-width: 760px) 40vw, 185px',route:'auto'});
      const placeholder = card.querySelector<HTMLElement>('.browse-placeholder')!;
      placeholder.hidden = !!item.poster; placeholder.textContent = item.title.slice(0,1);
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
    kind:data.kind,ids:()=>[...items.values()].filter(item=>item.type==='game'&&item.id).map(item=>item.id!),
    apply:rows=>{
      const captions=new Map(rows.map(row=>[row.id,row]));
      for(const [card,item] of items){
        const next=item.type==='game'&&item.id?captions.get(item.id):undefined;
        if(!next)continue;
        // Only public role text changes. Do not re-seed private/optimistic
        // tracking, replace cards, alter ordering, paging or scroll position.
        item.characters=next.characters;item.roles=next.roles;
        const caption=card.querySelector<HTMLElement>('[data-work-credit]');
        if(caption){caption.textContent=[...next.characters,...next.roles.filter(role=>!['Actor','Production'].includes(role))].join(' / ');caption.title=caption.textContent;}
      }
    }
  },state=>{
    sourcePending=!!state&&(state.pending||!!state.identity_pending)&&!state.error;
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
    more.disabled=true;more.textContent='Loading…';error.hidden=true;loading.hidden=false;
    try{
      const path=nextPath();
      const next=await prediction.consume(path,signal)??await requestPage(path,signal);
      if(signal.aborted||current!==revision)return;
      append(next.works);
      data.page=next.page;data.source=next.source;data.next_cursor=next.next_cursor;data.has_more=next.has_more;
      more.hidden=!data.has_more;
    }catch(cause){
      if(!signal.aborted&&current===revision){
        if((cause as Error & {status?:number}).status===409)form.requestSubmit();
        else showError(cause instanceof Error?cause.message:'Unable to load more works.');
      }
    }
    finally{if(!signal.aborted&&current===revision){more.disabled=false;more.textContent='Load more';updateEmpty();if(syncAgain)void synchronize();}}
  };
  more.addEventListener('click',()=>void loadMore(),{signal});
  type RetainedWorks={nodes:Node[];items:Map<HTMLElement,ContributorWork>;data:ContributorPage;params:URLSearchParams;view:string|undefined;scroll:number};
  const retained=new Map<string,RetainedWorks>();
  let retainCurrent=true;
  const domain=()=>params.get('media_type')==='game'?'game':'movies-series';
  const invalidateRetained=()=>{retained.clear();retainCurrent=false;};
  document.addEventListener('anylist:cache-invalidated',invalidateRetained,{signal});
  document.addEventListener('anylist:editor-state',invalidateRetained,{signal});
  signal.addEventListener('abort',()=>retained.clear(),{once:true});
  form.addEventListener('submit',event=>{
    event.preventDefault();prediction.cancel();filterRequest?.abort();
    const worksAnchor = new URL(form.action).hash === '#contributor-works';
    const request=new AbortController(),current=++revision;
    filterRequest=request;error.hidden=true;region.setAttribute('aria-busy','true');
    root.querySelector<HTMLElement>('[data-contributor-loading]')!.hidden=items.size>0;
    root.querySelector<HTMLElement>('[data-contributor-empty]')!.hidden=true;
    const submitted=new URLSearchParams();
    new FormData(form).forEach((value,key)=>submitted.set(key,String(value)));
    // A verified-empty selected option can still be opened by direct link.
    // Disabled controls are absent from FormData; preserve its domain while
    // the viewer changes another filter, without forcing a route switch.
    form.querySelectorAll<HTMLInputElement>('input[name=domain]:checked,input[name=media_type]:checked').forEach(input=>submitted.set(input.name,input.value));
    let query=contributorParams(submitted,data.list_count!==null);
    const switching=(query.get('media_type')==='game'?'game':'movies-series')!==domain();
    const cached=switching?retained.get(query.get('media_type')==='game'?'game':'movies-series'):undefined;
    if(switching&&retainCurrent&&!syncing)retained.set(domain(),{nodes:[...region.childNodes],items:new Map(items),data:{...data},params:new URLSearchParams(params),view:requestedView,scroll:scrollY});
    if(cached){query=new URLSearchParams(cached.params);requestedView=cached.view;}
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
    const applySelection=(next:ContributorPage)=>{
      if(signal.aborted||current!==revision)return;
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
      const roles=root.querySelector<HTMLElement>('[data-contributor-roles]')!;
      roles.textContent=data.roles.slice(0,4).join(' / ');roles.hidden=!data.roles.length;
      if(data.kind==='studio')root.querySelector('[data-contributor-kicker]')!.textContent=params.get('media_type')==='game'?'Game studio':'Production studio';
      const listLink=root.querySelector<HTMLAnchorElement>('.contributor-list-link');
      if(listLink){const href=new URL(location.pathname,location.origin);href.search=query.toString();href.searchParams.set('list_scope','in');href.searchParams.set('view',requestedView||'timeline');href.hash='contributor-works';listLink.href=href.href;}
      if(!cached)append(next.works);
      retainCurrent=true;
      if(cached){restoreFilterControls();root.dataset.view=requestedView;region.classList.toggle('browse-grid',requestedView==='grid');(form.elements.namedItem('view') as HTMLInputElement).value=requestedView||'timeline';root.querySelectorAll<HTMLElement>('[data-contributor-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.contributorView===requestedView)));}
      more.hidden=!data.has_more;more.disabled=false;more.textContent='Load more';
      const notice=root.querySelector<HTMLElement>('[data-contributor-notice]')!;
      notice.textContent=next.notice||'';notice.hidden=!next.notice;
      const url=new URL(location.href);url.search=query.toString();
      if (worksAnchor) url.hash='contributor-works';
      url.searchParams.set('view',requestedView||'timeline');history.replaceState(history.state,'',url);
      if (worksAnchor) {
        const works=root.querySelector<HTMLElement>('.contributor-works');
        if (works) scrollToWorks(works);
      }
    };
    const finish=()=>{if(!signal.aborted&&current===revision){filterRequest=undefined;region.removeAttribute('aria-busy');region.hidden=false;region.inert=false;root.querySelector<HTMLElement>('[data-contributor-loading]')!.hidden=true;updateEmpty();more.disabled=false;more.textContent='Load more';if(syncAgain)void synchronize();}};
    if(cached){applySelection(cached.data);finish();window.scrollTo(0,cached.scroll);return;}
    void readSelection().then(applySelection).catch(cause=>{
      if(!signal.aborted&&!request.signal.aborted&&current===revision&&(cause as Error).name!=='AbortError'){restoreFilterControls();showError(cause instanceof Error?cause.message:'Unable to load works.');}
    }).finally(finish);
  },{signal});
  let syncing=false,syncAgain=false;
  let syncRetry:ReturnType<typeof setTimeout>|undefined,syncFailures=0;
  signal.addEventListener('abort',()=>clearTimeout(syncRetry),{once:true});
  const loading=root.querySelector<HTMLElement>('[data-contributor-loading]')!;
  const pathFor=(query:URLSearchParams)=>`/api/proxy/tracking/${root.dataset.endpoint}?${query}`;
  const warmSibling=(domain:string)=>{
    if(root.dataset.contributorAvailability!=='true'||signal.aborted)return;
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
      const anchor=region.getBoundingClientRect().top<0?[...items.keys()].find(card=>card.getBoundingClientRect().bottom>0):undefined;
      const top=anchor?.getBoundingClientRect().top;
      const focused=document.activeElement instanceof HTMLElement?document.activeElement:null;
      const wanted=new Set(works.map(work=>work.key));
      for(const [card,item] of items)if(!wanted.has(item.key)){items.delete(card);card.remove();}
      for(const work of works){
        const existing=[...items].find(([,item])=>item.key===work.key);
        if(existing){
          Object.assign(existing[1],work);paint(existing[0],existing[1]);
          const caption=existing[0].querySelector<HTMLElement>('[data-work-credit]');
          if(caption){caption.textContent=[...work.characters,...work.roles.filter(role=>!['Actor','Production'].includes(role))].join(' / ');caption.title=caption.textContent;}
        }
      }
      append(works);
      for(const group of region.querySelectorAll<HTMLElement>('[data-contributor-year]')){
        if(!group.querySelector('[data-browse-card]'))group.remove();
      }
      const groups=[...region.querySelectorAll<HTMLElement>('[data-contributor-year]')];
      groups.sort((a,b)=>(b.dataset.contributorYear==='Undated'?'':b.dataset.contributorYear!).localeCompare(a.dataset.contributorYear==='Undated'?'':a.dataset.contributorYear!));
      for(const group of groups){
        region.append(group);
        for(const work of works.filter(work=>contributorYear(work)===group.dataset.contributorYear)){
          const card=[...items].find(([,item])=>item.key===work.key)?.[0];
          if(card)group.querySelector('.contributor-year-works')!.append(card);
        }
      }
      if(anchor?.isConnected&&top!==undefined)window.scrollBy(0,anchor.getBoundingClientRect().top-top);
      if(focused?.isConnected&&document.activeElement!==focused)focused.focus({preventScroll:true});
      data.page=next.page;data.source=next.source;data.next_cursor=next.next_cursor;data.has_more=next.has_more;
      data.total=next.total;data.list_count=next.list_count;
      const count=root.querySelector('[data-contributor-list-count]');if(count)count.textContent=String(next.list_count||0);
      more.hidden=!data.has_more;updateEmpty();
      syncFailures=0;
    }catch{
      // A final source revision must survive a transient browser read failure.
      if(!signal.aborted&&current===revision&&++syncFailures<=3)
        syncRetry=setTimeout(()=>void synchronize(),3000*syncFailures);
    }
    finally{syncing=false;if(syncAgain&&!filterRequest&&!signal.aborted)void synchronize();}
  };
  syncSource=()=>{
    void synchronize();
    pages.forget(path=>new URLSearchParams(path.split('?')[1]).get('media_type')!=='game');
  };
  let previousAvailability='';
  availabilityChanged=state=>{
    // Retry an empty active tab when background identity/availability changes.
    if(previousAvailability&&state!==previousAvailability){
      pages.clear();invalidateRetained();
      if(!items.size&&!filterRequest)form.requestSubmit();
    }
    previousAvailability=state;
  };
  sourceRefresh.update(data.game_company_status||data.game_person_status);
  bindContributorTabIntent(form,signal,warmSibling);
  bindPredictivePagination(region,signal,()=>!filterRequest&&!syncing&&data.has_more&&!more.disabled&&error.hidden,
    ()=>prediction.prefetch(nextPath()),()=>void loadMore());

}
