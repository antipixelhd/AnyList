
import { editorStore, type EditorTitle } from './editor-store';
import { showDialog, closeDialog } from './ui-motion';
import { responsiveArtwork } from './responsive-artwork';
import { quickSearchShortcut } from './quick-search-shortcut';
import { wireSearchWordCapitalization } from './search-word-capitalization';
import { mountQuickSearchLayout } from './quick-search-layout';
import {quickSearchRequests,rankCombinedMedia,readQuickTitles,createQuickSearchReader,quickSearchTabOrder,availableQuickSearchTabs,resolvedQuickSearchTab,steppedQuickSearchTab,mergeContributorResults,type SearchSection} from './quick-search-data';
import { animate } from 'motion/mini';
import {gameResultHref,resolveGameId,type GameSearchResult} from './game-discovery';

let pageListeners: AbortController;
let mountedDialog:HTMLDialogElement|null=null;
const SEARCH_DEBOUNCE_MS = 300;
const SKELETON_DELAY_MS = 120;
const PROGRESS_DELAY_MS = 150;
const RESULT_LIMIT = 8;
export function mountQuickSearch() {
  const nextDialog=document.querySelector<HTMLDialogElement>('[data-quick-search]');
  if(nextDialog===mountedDialog&&pageListeners&&!pageListeners.signal.aborted)return;
  pageListeners?.abort();
  pageListeners = new AbortController();
  type MediaSearchResult = { id: number | null; tmdb_id?: number; type: 'movie' | 'series'; title: string; poster?: string | null; year?: string | null; search_rank?:number };
  type SearchResult = MediaSearchResult | GameSearchResult;
  const openButton = document.querySelector<HTMLButtonElement>('[data-quick-search-open]');
  if (!nextDialog || !openButton) return;
  const dialog = nextDialog;
  mountedDialog=dialog;
  const searchReader=createQuickSearchReader(pageListeners.signal);
  document.addEventListener('anylist:cache-invalidated',()=>searchReader.clear(),{signal:pageListeners.signal});
  document.addEventListener('astro:before-swap',()=>pageListeners.abort(),{once:true,signal:pageListeners.signal});
  // A phone in landscape can be wider than the compact breakpoint while still using mobile navigation.
  const mobile = matchMedia('(max-width:650px), (max-width:1040px) and (hover:none) and (pointer:coarse)');
  const reducedMotion=matchMedia('(prefers-reduced-motion:reduce)');
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
  if (input) wireSearchWordCapitalization(input, pageListeners.signal);
  const resultShell = dialog.querySelector<HTMLElement>('.quick-search-results')!;
  const updateLayout = mountQuickSearchLayout(resultShell, mobile, pageListeners.signal);
  const panels = [...resultShell.querySelectorAll<HTMLElement>('[data-search-section]')];
  const skeletonTemplate = dialog.querySelector<HTMLTemplateElement>('[data-search-skeleton]')!;
  const panelContent = panels.map(panel => {
    const target = panel.querySelector<HTMLElement>('[data-search-results]')!;
    const skeleton = skeletonTemplate.content.firstElementChild!.cloneNode(true) as HTMLElement;
    skeleton.hidden = true;
    target.replaceChildren(skeleton);
    return {panel, target, skeleton, secondRow: skeleton.lastElementChild as HTMLElement,
      kind: panel.dataset.searchSection as SearchSection, rows: new Map<string, HTMLElement>()};
  });
  const state = dialog?.querySelector<HTMLElement>('[data-search-state]');
  const announcement = dialog?.querySelector<HTMLElement>('[data-search-announcement]');
  const progress = dialog.querySelector<HTMLElement>('[data-search-progress]')!;
  const addIcon = document.querySelector<HTMLTemplateElement>('[data-search-add-icon]');
  const editorTitles = new WeakMap<HTMLButtonElement, EditorTitle>();
  const gameResults = new WeakMap<HTMLButtonElement, GameSearchResult>();
  // Discard restoration requests from older builds; saved edits now end search.
  try { sessionStorage.removeItem('anylist.quick-search.restore'); } catch { /* Storage may be unavailable. */ }
  let controller: AbortController | null = null;
  let timer = 0;
  let revealTimer = 0;
  let progressTimer = 0;
  let revealed = false;
  let latest = { all: [] as MediaSearchResult[], movie: [] as MediaSearchResult[], series: [] as MediaSearchResult[], game: [] as GameSearchResult[] };
  let contributors:Record<'actor'|'staff'|'studio',ContributorResult[]>={actor:[],staff:[],studio:[]};
  let failed = false;
  let partialCoverage = false;
  const pending = new Set<SearchSection>();
  const exits = new Map<HTMLElement, Animation>();
  const skeletonExits = new Map<HTMLElement, Animation>();
  const panelOrder = () => quickSearchTabOrder(combined(), showMoviesSeries(), showGames());
  const updateViewport = () => {
    dialog.toggleAttribute('data-search-phone', mobile.matches);
    input!.placeholder = mobile.matches ? 'Search titles, people, studios…' : 'Search titles, people and studios…';
  };
  const resultCounts=()=>({all:latest.all.length,movie:latest.movie.length,series:latest.series.length,game:latest.game.length,actor:contributors.actor.length,staff:contributors.staff.length,studio:contributors.studio.length});
  const availableTabs=()=>revealed ? availableQuickSearchTabs(panelOrder(),resultCounts(),pending) : panelOrder();
  const tabNav=dialog.querySelector<HTMLElement>('[data-search-tabs]')!;
  function updateTabs(available=availableTabs()) {
    activeTab=resolvedQuickSearchTab(activeTab,available);
    tabNav.hidden=!available.length;
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
  updateViewport();

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
    row.dataset.detailKind=item.type;
    row.dataset.detailYear=item.year||'';
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
  function contributorNode(kind:'actor'|'staff'|'studio', item:ContributorResult) {
    const row=document.createElement('div');row.className=`quick-search-result quick-search-contributor ${kind==='studio'?'quick-search-studio':''}`;
    const link=document.createElement('a');link.className='quick-search-result-link';link.href=item.url;
    const art=responsiveArtwork(item.image,{size:'w92',sizes:'48px',route:'auto'});
    if(art){const image=document.createElement('img');image.src=art.src;if(art.srcset)image.srcset=art.srcset;if(art.sizes)image.sizes=art.sizes;image.alt='';image.loading='lazy';link.append(image);}
    else {const fallback=document.createElement('span');fallback.className='quick-search-poster';fallback.textContent=item.name.slice(0,1);link.append(fallback);}
    const copy=document.createElement('span');const name=document.createElement('strong');name.textContent=item.name;copy.append(name);
    if(item.roles.length){const meta=document.createElement('small');meta.textContent=item.roles.join(' · ');copy.append(meta);}
    link.append(copy);row.append(link);return row;
  }
  function showState(message: string) {
    if (!state) return;
    if (state.textContent !== message) state.textContent = message;
    state.hidden = !message;
  }
  function syncPanel(content: typeof panelContent[number], animateChange: boolean) {
    const {panel, target, kind, skeleton, secondRow, rows} = content;
    const people = kind === 'actor' || kind === 'staff' || kind === 'studio';
    const items = people ? contributors[kind] : latest[kind].slice(0, RESULT_LIMIT);
    const signatures = items.map(item => JSON.stringify(item));
    const wanted = new Set(signatures);
    const loading = pending.has(kind);
    const showSkeleton = loading && items.length < RESULT_LIMIT;
    for (const [signature, row] of rows) {
      if (wanted.has(signature)) continue;
      row.remove();
      rows.delete(signature);
    }
    items.forEach((item, index) => {
      const signature = signatures[index];
      let row = rows.get(signature);
      if (!row) {
        row = 'roles' in item ? contributorNode(kind as 'actor' | 'staff' | 'studio', item) : resultNode(item);
        rows.set(signature, row);
        if (animateChange) row.animate({opacity: [.4, 1]}, {duration: 160, easing: 'ease-out'});
      }
      if (target.children[index] !== row) target.insertBefore(row, target.children[index]);
    });
    panel.setAttribute('aria-busy', String(loading));
    secondRow.hidden = items.length > 0;

    const exit = skeletonExits.get(skeleton);
    if (!showSkeleton && animateChange && !skeleton.hidden) {
      if (!exit) {
        const fade = skeleton.animate({opacity: [1, 0]}, {duration: 150, easing: 'ease-out', fill: 'forwards'});
        skeletonExits.set(skeleton, fade);
        fade.onfinish = render;
        return;
      }
      if (exit.playState !== 'finished') return;
    }
    if (exit) { exit.cancel(); skeletonExits.delete(skeleton); }
    skeleton.hidden = !showSkeleton;
  }
  function updatePanel(content: typeof panelContent[number], visible: boolean, animateChange: boolean) {
    const {panel, skeleton} = content;
    const exit = exits.get(panel);
    if (!visible && animateChange && !mobile.matches && !panel.hidden) {
      if (!exit) {
        const fade = panel.animate({opacity: [Number(getComputedStyle(panel).opacity), 0]},
          {duration: 150, easing: 'ease-in', fill: 'forwards'});
        exits.set(panel, fade);
        fade.onfinish = render;
        panel.inert = true;
        return;
      }
      if (exit.playState !== 'finished') return;
    }
    if (exit) { exit.cancel(); exits.delete(panel); }
    if (!visible) {
      skeletonExits.get(skeleton)?.cancel();
      skeletonExits.delete(skeleton);
    } else syncPanel(content, animateChange && !mobile.matches && !panel.hidden);
    panel.inert = false;
    panel.hidden = !visible;
  }
  function render() {
    if (!revealed) return;
    const counts=resultCounts();
    const available=availableTabs();
    const wasStale = resultShell.inert;
    const animateChange = !wasStale && !reducedMotion.matches;
    const previousTab = activeTab;
    if (mobile.matches && !available.includes(activeTab)) {
      activeTab = available.find(kind => counts[kind]) ?? activeTab;
    }
    updateTabs(available);
    const visible = mobile.matches ? available.filter(kind => kind === activeTab) : available;
    updateLayout(() => {
      resultShell.inert = false;
      tabNav.inert = false;
      for (const content of panelContent) {
        updatePanel(content, visible.includes(content.kind), animateChange);
      }
      resultShell.hidden = panels.every(panel => panel.hidden);
    });
    if (mobile.matches && previousTab !== activeTab) resultShell.scrollTop = 0;
    const message = pending.size || exits.size ? '' : failed
      ? available.length ? 'Some search results are unavailable.' : 'Search is unavailable. Try again.'
      : available.length ? '' : partialCoverage ? 'Some matches could not be verified.' : 'No matching results.';
    showState(message);
    const summary = visible.filter(kind => counts[kind]).map(kind=>`${counts[kind]} ${kind==='all'?'movie and series':kind==='staff'?'crew':kind} results`).join(', ');
    const status = summary || (pending.size ? 'Searching.' : '');
    if (announcement && announcement.textContent !== status) announcement.textContent = status;
  }
  function setActiveTab(next:SearchSection,direction?:number) {
    if (next===activeTab || tabNav.inert) return;
    direction??=tabs.findIndex(tab=>tab.kind===next)>tabs.findIndex(tab=>tab.kind===activeTab)?1:-1;
    activeTab=next;
    resultShell.scrollTop = 0;
    if (revealed) render();
    else updateTabs();
    tabAnimations.forEach(control=>control.cancel());tabAnimations=[];
    if (!mobile.matches||reducedMotion.matches) return;
    const timing={duration:.22,ease:[.2,.7,.2,1] as [number,number,number,number]};
    const transform = [`translateX(${direction*14}px)`, 'translateX(0)'];
    tabAnimations.push(animate(tabTitle,{opacity:[.45,1],transform},timing));
    const panel = panels.find(panel => !panel.hidden);
    if (panel) tabAnimations.push(animate(panel,{opacity:[.5,1],transform},timing));
  }
  function stopSearch() {
    clearTimeout(timer);
    clearTimeout(revealTimer);
    clearTimeout(progressTimer);
    controller?.abort();
    controller = null;
    revealed = false;
    pending.clear();
    progress.classList.remove('visible');
    skeletonExits.forEach(exit => exit.cancel());
    skeletonExits.clear();
    for (const [panel, exit] of exits) {
      exit.cancel();
      panel.hidden = true;
      panel.inert = false;
    }
    exits.clear();
    tabAnimations.forEach(control => control.cancel());
    tabAnimations = [];
  }
  function prepareSearch(retainResults = true) {
    stopSearch();
    latest = {all: [], movie: [], series: [], game: []};
    contributors = {actor: [], staff: [], studio: []};
    failed = false;
    partialCoverage = false;
    const query = input?.value.trim() || '';
    // Preserve desktop panel placement during debounce while disabling links from the previous query.
    const keepLayout = !mobile.matches && retainResults && query.length >= 2 && !resultShell.hidden;
    resultShell.inert = keepLayout;
    tabNav.inert = keepLayout;
    if (!keepLayout) {
      resultShell.hidden = true;
      for (const panel of panels) panel.hidden = true;
      updateTabs();
    }
    if (announcement) announcement.textContent = '';
    showState(query.length === 1 ? 'Type one more character.' : '');
    return query;
  }
  async function search() {
    const query = input?.value.trim() || '';
    if (query.length < 2) return;
    const current = new AbortController();
    controller = current;
    const isCurrent = () => controller === current && !current.signal.aborted;
    const order = panelOrder();
    order.forEach(kind => pending.add(kind));
    const reveal = () => {
      if (!isCurrent()) return;
      clearTimeout(revealTimer);
      revealed = true;
      render();
    };
    revealTimer = window.setTimeout(reveal, SKELETON_DELAY_MS);
    progressTimer = window.setTimeout(() => {
      if (isCurrent()) progress.classList.add('visible');
    }, PROGRESS_DELAY_MS);

    const requests = quickSearchRequests(query, undefined, showMoviesSeries(), showGames());
    const titleSections = order.filter(kind => kind === 'all' || kind === 'movie' || kind === 'series');
    const titles = (async () => {
      if (!titleSections.length) return;
      try {
        const result = await readQuickTitles<MediaSearchResult>(requests, current.signal, searchReader.read, combined());
        if (!isCurrent()) return;
        latest.movie = unique(result.movie);
        latest.series = unique(result.series);
        latest.all = rankCombinedMedia(latest.movie, latest.series);
        if (result.failed || result.incomplete) failed = true;
      } catch {
        if (isCurrent()) failed = true;
      } finally {
        if (isCurrent()) {
          titleSections.forEach(kind => pending.delete(kind));
          render();
        }
      }
    })();

    await Promise.all([titles, ...requests.filter(request => request.kind === 'contributors' || request.kind === 'game').map(async request => {
      const sections: SearchSection[] = request.kind === 'game' ? ['game'] : request.scope === 'studio' ? ['studio'] : ['actor', 'staff'];
      let unavailable = false, incomplete = false;
      try {
        await searchReader.read(request, current.signal, data => {
          if (!isCurrent()) return;
          unavailable = !!(data.notice || (data.unavailable ?? data.partial));
          incomplete = !!data.partial;
          if (request.kind === 'game') latest.game = unique(data.results || []) as GameSearchResult[];
          else {
            contributors = mergeContributorResults(contributors, data.results || {}, request.scope);
            if (data.section_pending) sections.forEach(kind => {
              if (data.section_pending[kind]) pending.add(kind);
              else pending.delete(kind);
            });
            if (data.verification_pending) render();
          }
        });
      } catch {
        unavailable = true;
      } finally {
        if (isCurrent()) {
          failed ||= unavailable;
          partialCoverage ||= incomplete;
          sections.forEach(kind => pending.delete(kind));
          render();
        }
      }
    })]);
    if (!isCurrent()) return;
    clearTimeout(progressTimer);
    progress.classList.remove('visible');
    if (!revealed) reveal();
  }
  function open() {
    activeTab=combined()?'all':'movie';
    if (input) input.value = '';
    prepareSearch();
    dialog.scrollTop = 0;
    resultShell.scrollTop = 0;
    showDialog(dialog);
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
    // Keep results underneath the editor: cancel returns here; saving closes both.
    document.dispatchEvent(new CustomEvent('anylist:open-editor', {detail:{mediaId:title.id, title, opener:button}}));
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
  // A click can target the dialog after a drag starts in a child and ends outside it.
  // Dismiss only when the pointer also started on the backdrop.
  let pointerStartedOnBackdrop = false;
  dialog.addEventListener('pointerdown', event => {
    pointerStartedOnBackdrop = event.isPrimary && event.button === 0 && event.target === dialog;
  }, {signal:pageListeners.signal});
  dialog.addEventListener('pointercancel', () => { pointerStartedOnBackdrop = false; }, {signal:pageListeners.signal});
  dialog.addEventListener('click', event => {
    const dismiss = pointerStartedOnBackdrop && event.target === dialog;
    pointerStartedOnBackdrop = false;
    if (dismiss) void closeDialog(dialog);
  }, {signal:pageListeners.signal});
  dialog.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    void closeDialog(dialog, 'cancel');
  }, {signal: pageListeners.signal});
  dialog.addEventListener('close', () => { if (!dialog.open) stopSearch(); }, {signal:pageListeners.signal});
  input?.addEventListener('input', () => {
    if (prepareSearch().length >= 2) timer = window.setTimeout(search, SEARCH_DEBOUNCE_MS);
  },{signal:pageListeners.signal});
  function refreshPolicy() {
    if (dialog.open && prepareSearch(false).length >= 2) void search();
    else updateTabs();
  }
  document.addEventListener('anylist:quick-search-policy-changed',refreshPolicy,{signal:pageListeners.signal});
  tabButtons.forEach(button=>button.addEventListener('click',()=>{
    const direction=Number(button.dataset.searchTabStep);
    const next=steppedQuickSearchTab(activeTab,availableTabs(),direction);
    if(next)setActiveTab(next,direction);
  },{signal:pageListeners.signal}));
  let touchStart: Touch | undefined;
  dialog.addEventListener('touchstart',event=>{
    const surface = (event.target as Element).closest('[data-search-tabs], .quick-search-results');
    touchStart = mobile.matches && !tabNav.inert && surface && event.touches.length === 1 ? event.changedTouches[0] : undefined;
  },{passive:true,signal:pageListeners.signal});
  dialog.addEventListener('touchend',event=>{
    const start = touchStart;
    touchStart = undefined;
    if (!start || !mobile.matches || tabNav.inert) return;
    const touch = Array.from(event.changedTouches).find(touch => touch.identifier === start.identifier);
    if (!touch) return;
    const dx=touch.clientX-start.clientX,dy=touch.clientY-start.clientY;
    if(Math.abs(dx)<45||Math.abs(dx)<Math.abs(dy)*1.25)return;
    const direction=dx<0?1:-1;
    const next=steppedQuickSearchTab(activeTab,availableTabs(),direction);
    if(next){event.preventDefault();setActiveTab(next,direction);}
  },{passive:false,signal:pageListeners.signal});
  dialog.addEventListener('touchcancel',()=>{touchStart=undefined;},{signal:pageListeners.signal});
  mobile.addEventListener('change',()=>{
    touchStart = undefined;
    updateViewport();
    if (!dialog.open) return;
    if (mobile.matches && resultShell.inert) {
      tabNav.inert = false;
      resultShell.hidden = true;
      updateTabs();
    }
    render();
  },{signal:pageListeners.signal});
  reducedMotion.addEventListener('change',()=>{if(dialog.open)render();},{signal:pageListeners.signal});
  document.addEventListener('anylist:combine-lists-changed',event=>{
    dialog.dataset.combined=(event as CustomEvent<{combineLists:boolean}>).detail.combineLists?'yes':'no';
    refreshPolicy();
  },{signal:pageListeners.signal});
  pageListeners.signal.addEventListener('abort',stopSearch,{once:true});
  document.addEventListener('keydown', event => {
    const action = quickSearchShortcut(event, dialog.open, !!document.querySelector('dialog[open]:not([data-quick-search])'));
    if (!action) return;
    event.preventDefault();
    if (action === 'close') void closeDialog(dialog, 'cancel');
    else if (action === 'open') open();
  }, { signal: pageListeners.signal });
}
