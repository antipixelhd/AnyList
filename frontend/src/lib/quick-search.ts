
import { editorStore, type EditorTitle } from './editor-store';
  import { showDialog, closeDialog } from './ui-motion';
import { responsiveArtwork } from './responsive-artwork';
import {quickSearchRequests,rankCombinedMedia,readQuickTitles,createQuickSearchReader,visibleQuickSearchSections,availableQuickSearchTabs,resolvedQuickSearchTab,steppedQuickSearchTab,mergeContributorResults,type SearchSection} from './quick-search-data';
import { animate } from 'motion/mini';
import {gameResultHref,resolveGameId,type GameSearchResult} from './game-discovery';

let pageListeners: AbortController;
let mountedDialog:HTMLDialogElement|null=null;
export function mountQuickSearch() {
  const nextDialog=document.querySelector<HTMLDialogElement>('[data-quick-search]');
  if(nextDialog===mountedDialog&&pageListeners&&!pageListeners.signal.aborted)return;
  pageListeners?.abort();
  pageListeners = new AbortController();
  type MediaSearchResult = { id: number | null; tmdb_id?: number; type: 'movie' | 'series'; title: string; poster?: string | null; year?: string | null; search_rank?:number };
  type SearchResult = MediaSearchResult | GameSearchResult;
  const dialog = document.querySelector<HTMLDialogElement>('[data-quick-search]');
  const openButton = document.querySelector<HTMLButtonElement>('[data-quick-search-open]');
  if (!dialog || !openButton) return;
  mountedDialog=dialog;
  const searchReader=createQuickSearchReader(pageListeners.signal);
  document.addEventListener('anylist:cache-invalidated',()=>searchReader.clear(),{signal:pageListeners.signal});
  document.addEventListener('astro:before-swap',()=>pageListeners.abort(),{once:true,signal:pageListeners.signal});
  const mobile=matchMedia('(max-width:650px)');
  const combined=()=>dialog.dataset.combined==='yes';
  const showMoviesSeries=()=>dialog.dataset.showMoviesSeries!=='false';
  const showGames=()=>dialog.dataset.showGames!=='false';
  const tabs: ReadonlyArray<{kind:SearchSection;label:string}> = [
    {kind:'all',label:'Movies & Series'}, {kind:'movie',label:'Movies'}, {kind:'series',label:'Series'}, {kind:'game',label:'Games'}, {kind:'actor',label:'Actors'},
    {kind:'staff',label:'Crew'}, {kind:'studio',label:'Studios'},
  ];
  let activeTab:SearchSection=combined()?'all':'movie';
  const tabTitle=dialog.querySelector<HTMLElement>('[data-search-tab-title]')!;
  const tabButtons=[...dialog.querySelectorAll<HTMLButtonElement>('[data-search-tab-step]')];
  const tabMarkers=[...dialog.querySelectorAll<HTMLElement>('[data-search-tab-marker]')];
  let tabAnimations:Array<ReturnType<typeof animate>>=[];
  type ContributorResult={key:string;name:string;image?:string|null;roles:string[];url:string};
  const closeButton = dialog?.querySelector<HTMLButtonElement>('[data-quick-search-close]');
  const input = dialog?.querySelector<HTMLInputElement>('#quick-search-input');
  const resultShell = dialog?.querySelector<HTMLElement>('.quick-search-results');
  const state = dialog?.querySelector<HTMLElement>('[data-search-state]');
  const announcement = dialog?.querySelector<HTMLElement>('[data-search-announcement]');
  const progress = dialog?.querySelector<HTMLElement>('[data-search-progress]');
  const addIcon = document.querySelector<HTMLTemplateElement>('[data-search-add-icon]');
  const editorTitles = new WeakMap<HTMLButtonElement, EditorTitle>();
  const gameResults = new WeakMap<HTMLButtonElement, GameSearchResult>();
  // Discard restoration requests from older builds; saved edits now end search.
  try { sessionStorage.removeItem('anylist.quick-search.restore'); } catch { /* Storage may be unavailable. */ }
  let controller: AbortController | null = null;
  let timer = 0;
  let progressTimer = 0;
  let latest:{movie:MediaSearchResult[];series:MediaSearchResult[];game:GameSearchResult[]} = { movie: [], series: [], game: [] };
  let contributors:Record<'actor'|'staff'|'studio',ContributorResult[]>={actor:[],staff:[],studio:[]};
  let pending=0,failed=0,mediaPending=0,gamePending=false;
  const selected=()=>activeTab;
  const updatePlaceholder=()=>{input!.placeholder=mobile.matches?'Search titles, people, studios…':'Search titles, people and studios…';};
  const resultCounts=()=>({all:latest.movie.length+latest.series.length,movie:latest.movie.length,series:latest.series.length,game:latest.game.length,actor:contributors.actor.length,staff:contributors.staff.length,studio:contributors.studio.length});
  const availableTabs=()=>availableQuickSearchTabs(resultCounts(),(input?.value.trim().length||0)>=2,mediaPending>0,combined(),showMoviesSeries(),showGames(),gamePending);
  const tabNav=dialog.querySelector<HTMLElement>('[data-search-tabs]')!;
  function updateTabs(available=availableTabs()) {
    activeTab=resolvedQuickSearchTab(activeTab,available);
    tabNav.hidden=mobile.matches&&(input?.value.trim().length||0)>=2&&!available.length;
    tabTitle.textContent=tabs.find(tab=>tab.kind===activeTab)!.label;
    tabButtons.forEach(button=>{
      const next=steppedQuickSearchTab(activeTab,available,Number(button.dataset.searchTabStep));
      button.hidden=!next;
      button.disabled=!next;
      if(next)button.setAttribute('aria-label',`Show ${tabs.find(tab=>tab.kind===next)!.label}`);
    });
    tabMarkers.forEach(marker=>{
      marker.hidden=!available.includes(marker.dataset.searchTabMarker as SearchSection);
      marker.toggleAttribute('data-active',available.includes(activeTab)&&marker.dataset.searchTabMarker===activeTab);
    });
  }
  updateTabs();
  updatePlaceholder();

  const resultUrl = (item: SearchResult) => item.type==='game'?gameResultHref(item):item.id ? `/title/${item.id}` : `/discover-title?type=${item.type}&id=${item.tmdb_id}`;
  function unique<T extends SearchResult>(items: T[]):T[] {
    const seen = new Set<string>();
    return items.filter(item => {
      const key = item.type==='game'?`game:${item.igdb_id}`:`${item.type}:${item.title.toLocaleLowerCase()}:${item.year || ''}`;
      if (seen.has(key)) return false;
      seen.add(key); return true;
    });
  }
  function resultNode(item: SearchResult) {
    const row = document.createElement('div');
    row.className = 'quick-search-result';
    const link = document.createElement('a');
    link.className = 'quick-search-result-link';
    link.href = resultUrl(item);
    const artwork = responsiveArtwork(item.poster, {
      size: 'w92',
      sizes: '(max-width: 650px) 52px, 48px',
      route:'auto',
    });
    if (artwork?.src) {
      const poster = document.createElement('img');
      if (artwork.srcset) poster.srcset = artwork.srcset;
      if (artwork.sizes) poster.sizes = artwork.sizes;
      poster.src = artwork.src;
      poster.alt = ''; poster.loading = 'lazy';
      link.append(poster);
    } else {
      const fallback = document.createElement('span');
      fallback.className = 'quick-search-poster'; fallback.textContent = item.title.slice(0,1);
      link.append(fallback);
    }
    const copy = document.createElement('span');
    const name = document.createElement('strong'); name.textContent = item.title;
    const meta = document.createElement('small'); meta.textContent = [item.year, item.type === 'game'?(item.game_class==='expansion'?'Expansion':'Game'):item.type === 'movie' ? 'Movie' : 'Series'].filter(Boolean).join(' · ');
    copy.append(name, meta); link.append(copy);
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'quick-search-add';
    add.dataset.searchAdd = '';
    if (item.id&&item.type!=='game') add.dataset.mediaId = String(item.id);
    else if (item.type!=='game'&&item.tmdb_id) add.dataset.tmdbId = String(item.tmdb_id);
    add.dataset.mediaType = item.type;
    add.setAttribute('aria-label', `Open list editor for ${item.title}`);
    add.title = `Open list editor for ${item.title}`;
    if(item.type==='game')gameResults.set(add,item);
    else editorTitles.set(add, editorStore.seed(item as unknown as EditorTitle));
    if (addIcon) add.append(addIcon.content.cloneNode(true));
    const addLabel = document.createElement('span');
    addLabel.textContent = 'List';
    add.append(addLabel);
    row.append(link, add);
    return row;
  }
  function fill(kind: 'all'|'movie'|'series'|'game', items: SearchResult[]) {
    const target = dialog?.querySelector<HTMLElement>(`[data-search-results="${kind}"]`);
    if (!target) return;
    target.replaceChildren(...items.slice(0,8).map(resultNode));
  }
  function fillContributors(kind:'actor'|'staff'|'studio') {
    const target=dialog!.querySelector<HTMLElement>(`[data-search-results="${kind}"]`)!;
    target.replaceChildren(...contributors[kind].map(item=>{
      const row=document.createElement('div');row.className=`quick-search-result quick-search-contributor ${kind==='studio'?'quick-search-studio':''}`;
      const link=document.createElement('a');link.className='quick-search-result-link';link.href=item.url;
      const art=responsiveArtwork(item.image,{size:'w92',sizes:'48px',route:'auto'});
      if(art){const image=document.createElement('img');image.src=art.src;if(art.srcset)image.srcset=art.srcset;if(art.sizes)image.sizes=art.sizes;image.alt='';image.loading='lazy';link.append(image);}
      else {const fallback=document.createElement('span');fallback.className='quick-search-poster';fallback.textContent=item.name.slice(0,1);link.append(fallback);}
      const copy=document.createElement('span');const name=document.createElement('strong');name.textContent=item.name;copy.append(name);
      if(item.roles.length){const meta=document.createElement('small');meta.textContent=item.roles.join(' · ');copy.append(meta);}
      link.append(copy);row.append(link);return row;
    }));
  }
  function showState(message: string) {
    if (!state) return;
    state.textContent = message;
    state.hidden = !message;
  }
  function render(updated?:'titles'|'games'|'contributors') {
    if(updated==='titles'){
      fill('movie',latest.movie);fill('series',latest.series);fill('all', rankCombinedMedia(latest.movie, latest.series));
    }
    if(updated==='contributors')for(const kind of ['actor','staff','studio'] as const)fillContributors(kind);
    if(updated==='games')fill('game',latest.game);
    const counts=resultCounts();
    const available=availableTabs();
    updateTabs(available);
    const visible=visibleQuickSearchSections(mobile.matches,selected(),dialog?.dataset.combined==='yes',counts,showMoviesSeries(),showGames());
    for (const kind of ['all','movie','series','game','actor','staff','studio'] as const) {
      const section = dialog?.querySelector<HTMLElement>(`[data-search-section="${kind}"]`);
      if (section) section.hidden = !visible.includes(kind);
    }
    if (resultShell) {
      resultShell.hidden = !visible.length;
    }
    dialog!.style.setProperty('--search-columns',String(Math.max(1,Math.min(4,visible.length))));
    dialog!.toggleAttribute('data-search-expanded',visible.length>1&&!mobile.matches);
    showState(pending ? '' : failed ? available.length?'Some search results are unavailable.':'Search is unavailable. Try again.' : visible.length?'':'No matching results.');
    if (announcement) announcement.textContent = visible.map(kind=>`${counts[kind]} ${kind==='all'?'movie and series':kind==='staff'?'crew':kind} results`).join(', ');
  }
  function setActiveTab(next:SearchSection,direction?:number) {
    if (next===activeTab) return;
    direction??=tabs.findIndex(tab=>tab.kind===next)>tabs.findIndex(tab=>tab.kind===activeTab)?1:-1;
    activeTab=next;
    updateTabs();
    if ((input?.value.trim().length||0)>=2) render();
    tabAnimations.forEach(control=>control.cancel());tabAnimations=[];
    if (!mobile.matches||matchMedia('(prefers-reduced-motion:reduce)').matches) return;
    const timing={duration:.22,ease:[.2,.7,.2,1] as [number,number,number,number]};
    tabAnimations.push(animate(tabTitle,{opacity:[.45,1],transform:[`translateX(${direction*14}px)`,'translateX(0)']},timing));
    const content=resultShell&&!resultShell.hidden?resultShell:state&&!state.hidden?state:null;
    if(content)tabAnimations.push(animate(content,{opacity:[.5,1],transform:['translateY(6px)','translateY(0)']},timing));
  }
  async function search() {
    const query = input?.value.trim() || '';
    controller?.abort();
    clearTimeout(progressTimer);progress?.classList.remove('visible');
    latest={movie:[],series:[],game:[]};contributors={actor:[],staff:[],studio:[]};failed=0;pending=0;mediaPending=query.length>=2&&showMoviesSeries()?2:0;gamePending=query.length>=2&&showGames();
    if (resultShell) resultShell.hidden = true;
    if (announcement) announcement.textContent = '';
    if (query.length < 2) {
      updateTabs();
      showState(query ? 'Type one more character.' : '');
      fill('movie',[]); fill('series',[]); fill('all',[]);fill('game',[]);
      if (resultShell) resultShell.setAttribute('aria-busy','false');
      return;
    }
    const current = new AbortController();
    controller = current;
    if (resultShell) resultShell.setAttribute('aria-busy','true');
    showState('');
    clearTimeout(progressTimer);
    progressTimer=window.setTimeout(()=>{if(controller===current)progress?.classList.add('visible');},150);
    try {
      const requests=quickSearchRequests(query,undefined,showMoviesSeries(),showGames());pending=requests.length;
      updateTabs();
      const titles = (async () => {
        try {
          const result = await readQuickTitles<MediaSearchResult>(requests, current.signal, searchReader.read, combined());
          if (controller !== current || current.signal.aborted) return;
          latest = {...latest, movie: unique(result.movie), series: unique(result.series)};
          failed += result.failed;
        } catch { if (controller === current && !current.signal.aborted) failed++; }
        finally { if (controller === current && !current.signal.aborted) {
          pending -= requests.filter(request=>request.kind==='movie'||request.kind==='series').length; mediaPending = 0; render('titles');
        } }
      })();
      await Promise.all([titles, ...requests.filter(request => request.kind === 'contributors'||request.kind==='game').map(async request=>{
        let gameRows:GameSearchResult[]=[];
        let requestPartial=false;
        try{
          await searchReader.read(request,current.signal,data=>{
            if(controller!==current||current.signal.aborted)return;
            if(request.kind==='game')gameRows=unique(data.results||[]) as GameSearchResult[];
            else{contributors=mergeContributorResults(contributors,data.results||{},request.scope);
              if(!!data.partial!==requestPartial){failed+=data.partial?1:-1;requestPartial=!!data.partial;}
              render('contributors');}
          });
        }catch(cause){if(controller!==current||current.signal.aborted)return;if(!requestPartial)failed++;}
        finally{if(controller===current&&!current.signal.aborted){
          pending--;
          if(request.kind==='game'){gamePending=false;latest.game=gameRows;render('games');}
          render();
        }}
      })]);
    } catch (cause) {
      if (controller === current && (cause as Error).name !== 'AbortError') showState('Search is unavailable. Try again.');
    } finally { if (controller === current) { clearTimeout(progressTimer);progress?.classList.remove('visible');resultShell?.setAttribute('aria-busy','false'); } }
  }
  function open() {
    clearTimeout(timer);
    clearTimeout(progressTimer);progress?.classList.remove('visible');
    controller?.abort();
    activeTab=combined()?'all':'movie';updatePlaceholder();dialog?.removeAttribute('data-search-expanded');
    tabAnimations.forEach(control=>control.cancel());tabAnimations=[];
    if (input) input.value = '';
    latest={movie:[],series:[],game:[]};contributors={actor:[],staff:[],studio:[]};mediaPending=0;gamePending=false;
    updateTabs();
    if (resultShell) resultShell.hidden = true;
    if (announcement) announcement.textContent = '';
    showState('');
    if (dialog) showDialog(dialog);
    requestAnimationFrame(() => input?.focus());
  }
  openButton?.addEventListener('click', open,{signal:pageListeners.signal});
  closeButton?.addEventListener('click', () => { if (dialog) void closeDialog(dialog); },{signal:pageListeners.signal});
  resultShell?.addEventListener('click', async event => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('[data-search-add]');
    if (!button || button.disabled) return;
    const game=gameResults.get(button);
    if(game){
      const current=controller;
      if(!current||current.signal.aborted||!dialog.open)return;
      button.disabled=true;
      try{
        const id=await resolveGameId(game.igdb_id,current.signal);
        const subject={kind:'game' as const,catalogue_id:id};
        const title=await editorStore.loadSubject(subject);
        if(controller!==current||current.signal.aborted||!dialog.open)return;
        const image=button.closest('.quick-search-result')?.querySelector<HTMLImageElement>('img');
        editorStore.warmArtwork(title,image?.currentSrc||image?.src);
        dialog.close();
        document.dispatchEvent(new CustomEvent('anylist:open-editor',{detail:{subject,title,opener:openButton}}));
      }catch(cause){if(!current.signal.aborted)showState((cause as Error).message);}
      finally{button.disabled=false;}
      return;
    }
    const title = editorTitles.get(button);
    if (!title || !dialog?.open) return;
    const image = button.closest('.quick-search-result')?.querySelector<HTMLImageElement>('img');
    editorStore.warmArtwork(title, image?.currentSrc || image?.src);
    dialog.close();
    const opener=mobile.matches?document.querySelector<HTMLElement>('[data-mobile-navigation] .mobile-navigation-trigger')||openButton:openButton;
    document.dispatchEvent(new CustomEvent('anylist:open-editor', {detail:{mediaId:title.id, title, opener}}));
  }, {signal: pageListeners.signal});
  const prepareEditor = (event: Event) => {
    const button = (event.target as Element).closest<HTMLButtonElement>('[data-search-add]');
    const title = button && editorTitles.get(button);
    if (!button || !title) return;
    const image = button.closest('.quick-search-result')?.querySelector<HTMLImageElement>('img');
    editorStore.warmArtwork(title, image?.currentSrc || image?.src);
  };
  resultShell?.addEventListener('pointerover', prepareEditor, {signal:pageListeners.signal});
  resultShell?.addEventListener('focusin', prepareEditor, {signal:pageListeners.signal});
  dialog?.addEventListener('click', event => { if (event.target === dialog) void closeDialog(dialog); },{signal:pageListeners.signal});
  dialog.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    void closeDialog(dialog, 'cancel');
  }, {signal: pageListeners.signal});
  dialog?.addEventListener('close', () => { clearTimeout(timer);clearTimeout(progressTimer);progress?.classList.remove('visible');controller?.abort(); },{signal:pageListeners.signal});
  input?.addEventListener('input', () => {
    clearTimeout(timer);clearTimeout(progressTimer);controller?.abort();controller=null;progress?.classList.remove('visible');
    latest={movie:[],series:[],game:[]};contributors={actor:[],staff:[],studio:[]};mediaPending=input.value.trim().length>=2&&showMoviesSeries()?2:0;gamePending=input.value.trim().length>=2&&showGames();
    if (resultShell) resultShell.hidden = true;
    if (announcement) announcement.textContent = '';
    showState('');
    updateTabs();
    timer = window.setTimeout(search, 60);
  },{signal:pageListeners.signal});
  document.addEventListener('anylist:quick-search-policy-changed',()=>{
    controller?.abort();latest={movie:[],series:[],game:[]};mediaPending=0;gamePending=false;
    if(dialog.open)void search();else updateTabs();
  },{signal:pageListeners.signal});
  tabButtons.forEach(button=>button.addEventListener('click',()=>{
    const direction=Number(button.dataset.searchTabStep);
    const next=steppedQuickSearchTab(activeTab,availableTabs(),direction);
    if(next)setActiveTab(next,direction);
  },{signal:pageListeners.signal}));
  let touchStart:{x:number;y:number}|null=null;
  dialog.addEventListener('touchstart',event=>{
    touchStart=null;
    if((event.target as Element).closest('.quick-search-bar'))return;
    const touch=event.changedTouches[0];touchStart={x:touch.clientX,y:touch.clientY};
  },{passive:true,signal:pageListeners.signal});
  dialog.addEventListener('touchend',event=>{
    if(!touchStart||!mobile.matches)return;
    const touch=event.changedTouches[0];
    const dx=touch.clientX-touchStart.x,dy=touch.clientY-touchStart.y;
    touchStart=null;
    if(Math.abs(dx)<45||Math.abs(dx)<Math.abs(dy)*1.25)return;
    const direction=dx<0?1:-1;
    const next=steppedQuickSearchTab(activeTab,availableTabs(),direction);
    if(next){event.preventDefault();setActiveTab(next,direction);}
  },{passive:false,signal:pageListeners.signal});
  dialog.addEventListener('touchcancel',()=>{touchStart=null;},{signal:pageListeners.signal});
  mobile.addEventListener('change',()=>{updatePlaceholder();if(dialog.open&&(input?.value.trim().length||0)>=2)render();},{signal:pageListeners.signal});
  document.addEventListener('anylist:combine-lists-changed',event=>{
    dialog.dataset.combined=(event as CustomEvent<{combineLists:boolean}>).detail.combineLists?'yes':'no';
    if(dialog.open&&(input?.value.trim().length||0)>=2)void search();
    else updateTabs();
  },{signal:pageListeners.signal});
  pageListeners.signal.addEventListener('abort',()=>{clearTimeout(timer);clearTimeout(progressTimer);controller?.abort();tabAnimations.forEach(control=>control.cancel());},{once:true});
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); open(); }
  }, { signal: pageListeners.signal });
}
