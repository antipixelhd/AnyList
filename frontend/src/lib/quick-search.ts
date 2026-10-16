
import { editorStore, type EditorTitle } from './editor-store';
  import { showDialog, closeDialog, reveal } from './ui-motion';
import { responsiveArtwork } from './responsive-artwork';
import {quickSearchRequests,visibleQuickSearchSections,mergeContributorResults,readQuickSearch,type SearchSection} from './quick-search-data';

let pageListeners: AbortController;
let mountedDialog:HTMLDialogElement|null=null;
export function mountQuickSearch() {
  const nextDialog=document.querySelector<HTMLDialogElement>('[data-quick-search]');
  if(nextDialog===mountedDialog&&pageListeners&&!pageListeners.signal.aborted)return;
  pageListeners?.abort();
  pageListeners = new AbortController();
  type SearchResult = { id: number | null; tmdb_id?: number; type: 'movie' | 'series'; title: string; poster?: string | null; year?: string | null };
  const dialog = document.querySelector<HTMLDialogElement>('[data-quick-search]');
  const openButton = document.querySelector<HTMLButtonElement>('[data-quick-search-open]');
  if (!dialog || !openButton) return;
  mountedDialog=dialog;
  document.addEventListener('astro:before-swap',()=>pageListeners.abort(),{once:true,signal:pageListeners.signal});
  const mobile=matchMedia('(max-width:650px)');
  const scope=dialog.querySelector<HTMLSelectElement>('[data-search-scope]')!;
  type ContributorResult={key:string;name:string;image?:string|null;roles:string[];url:string};
  const closeButton = dialog?.querySelector<HTMLButtonElement>('[data-quick-search-close]');
  const input = dialog?.querySelector<HTMLInputElement>('#quick-search-input');
  const resultShell = dialog?.querySelector<HTMLElement>('.quick-search-results');
  const state = dialog?.querySelector<HTMLElement>('[data-search-state]');
  const announcement = dialog?.querySelector<HTMLElement>('[data-search-announcement]');
  const progress = dialog?.querySelector<HTMLElement>('[data-search-progress]');
  const addIcon = document.querySelector<HTMLTemplateElement>('[data-search-add-icon]');
  const editorTitles = new WeakMap<HTMLButtonElement, EditorTitle>();
  // Discard restoration requests from older builds; saved edits now end search.
  try { sessionStorage.removeItem('anylist.quick-search.restore'); } catch { /* Storage may be unavailable. */ }
  let controller: AbortController | null = null;
  let timer = 0;
  let progressTimer = 0;
  let latest: Record<'movie'|'series', SearchResult[]> = { movie: [], series: [] };
  let contributors:Record<'actor'|'staff'|'studio',ContributorResult[]>={actor:[],staff:[],studio:[]};
  let pending=0,failed=0;
  const selected=()=>scope.value as SearchSection;
  const updatePlaceholder=()=>{input!.placeholder=mobile.matches ? selected()==='all'?'Search movies and series…':`Search ${selected()==='staff'?'crew':selected()==='actor'?'actors':'studios'}…` : 'Search titles, people and studios…';};
  updatePlaceholder();

  const resultUrl = (item: SearchResult) => item.id ? `/title/${item.id}` : `/discover-title?type=${item.type}&id=${item.tmdb_id}`;
  function interleave(first: SearchResult[], second: SearchResult[]) {
    const merged: SearchResult[] = [];
    for (let index = 0; index < Math.max(first.length, second.length); index++) {
      if (first[index]) merged.push(first[index]);
      if (second[index]) merged.push(second[index]);
    }
    return merged;
  }
  function unique(items: SearchResult[]) {
    const seen = new Set<string>();
    return items.filter(item => {
      const key = `${item.type}:${item.title.toLocaleLowerCase()}:${item.year || ''}`;
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
    const meta = document.createElement('small'); meta.textContent = [item.year, item.type === 'movie' ? 'Movie' : 'Series'].filter(Boolean).join(' · ');
    copy.append(name, meta); link.append(copy);
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'quick-search-add';
    add.dataset.searchAdd = '';
    if (item.id) add.dataset.mediaId = String(item.id);
    else if (item.tmdb_id) add.dataset.tmdbId = String(item.tmdb_id);
    add.dataset.mediaType = item.type;
    add.setAttribute('aria-label', `Open list editor for ${item.title}`);
    add.title = `Open list editor for ${item.title}`;
    editorTitles.set(add, editorStore.seed(item as unknown as EditorTitle));
    if (addIcon) add.append(addIcon.content.cloneNode(true));
    row.append(link, add);
    return row;
  }
  function fill(kind: 'all'|'movie'|'series', items: SearchResult[]) {
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
  function render(updated?:'movie'|'series'|'contributors') {
    if(updated==='movie'||updated==='series'){
      fill(updated,latest[updated]);fill('all', interleave(latest.movie, latest.series));
    }
    if(updated==='contributors')for(const kind of ['actor','staff','studio'] as const)fillContributors(kind);
    const counts={all:latest.movie.length+latest.series.length,movie:latest.movie.length,series:latest.series.length,actor:contributors.actor.length,staff:contributors.staff.length,studio:contributors.studio.length};
    const visible=visibleQuickSearchSections(mobile.matches,selected(),dialog?.dataset.combined==='yes',counts);
    for (const kind of ['all','movie','series','actor','staff','studio'] as const) {
      const section = dialog?.querySelector<HTMLElement>(`[data-search-section="${kind}"]`);
      if (section) section.hidden = !visible.includes(kind);
    }
    if (resultShell) {
      const wasHidden=resultShell.hidden;
      resultShell.hidden = !visible.length;
      if (wasHidden&&!resultShell.hidden) reveal(resultShell);
    }
    dialog!.style.setProperty('--search-columns',String(Math.max(1,Math.min(4,visible.length))));
    dialog!.toggleAttribute('data-search-expanded',visible.length>1&&!mobile.matches);
    showState(pending ? '' : failed ? visible.length?'Some search results are unavailable.':'Search is unavailable. Try again.' : visible.length?'':'No matching results.');
    if (announcement) announcement.textContent = visible.map(kind=>`${counts[kind]} ${kind==='all'?'movie and series':kind==='staff'?'crew':kind} results`).join(', ');
  }
  async function search() {
    const query = input?.value.trim() || '';
    controller?.abort();
    clearTimeout(progressTimer);progress?.classList.remove('visible');
    latest={movie:[],series:[]};contributors={actor:[],staff:[],studio:[]};failed=0;pending=0;
    if (resultShell) resultShell.hidden = true;
    if (announcement) announcement.textContent = '';
    if (query.length < 2) {
      showState(query ? 'Type one more character.' : '');
      fill('movie',[]); fill('series',[]); fill('all',[]);
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
      const requests=quickSearchRequests(query,mobile.matches,selected());pending=requests.length;
      await Promise.all(requests.map(async request=>{
        try{
          await readQuickSearch(request,current.signal,data=>{
            if(controller!==current||current.signal.aborted)return;
            if(request.kind==='contributors'){contributors=mergeContributorResults(contributors,data.results||{},request.scope);if(data.partial)failed++;}
            else latest[request.kind]=unique(data.results||[]);
            render(request.kind);
          });
        }catch(cause){if(controller!==current||current.signal.aborted)return;failed++;}
        finally{if(controller===current&&!current.signal.aborted){pending--;render();}}
      }));
    } catch (cause) {
      if (controller === current && (cause as Error).name !== 'AbortError') showState('Search is unavailable. Try again.');
    } finally { if (controller === current) { clearTimeout(progressTimer);progress?.classList.remove('visible');resultShell?.setAttribute('aria-busy','false'); } }
  }
  function open() {
    clearTimeout(timer);
    clearTimeout(progressTimer);progress?.classList.remove('visible');
    controller?.abort();
    scope.value='all';scope.dispatchEvent(new Event('input'));updatePlaceholder();dialog?.removeAttribute('data-search-expanded');
    if (input) input.value = '';
    if (resultShell) resultShell.hidden = true;
    if (announcement) announcement.textContent = '';
    showState('');
    if (dialog) showDialog(dialog);
    requestAnimationFrame(() => input?.focus());
  }
  openButton?.addEventListener('click', open,{signal:pageListeners.signal});
  closeButton?.addEventListener('click', () => { if (dialog) void closeDialog(dialog); },{signal:pageListeners.signal});
  resultShell?.addEventListener('click', event => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('[data-search-add]');
    if (!button || button.disabled) return;
    const title = editorTitles.get(button);
    if (!title || !dialog?.open) return;
    const image = button.closest('.quick-search-result')?.querySelector<HTMLImageElement>('img');
    editorStore.warmArtwork(title, image?.currentSrc || image?.src);
    document.dispatchEvent(new CustomEvent('anylist:open-editor', {detail:{mediaId:title.id, title, opener:input || openButton}}));
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
    if (resultShell) resultShell.hidden = true;
    if (announcement) announcement.textContent = '';
    showState('');
    timer = window.setTimeout(search, 180);
  },{signal:pageListeners.signal});
  scope.addEventListener('change',()=>{clearTimeout(timer);updatePlaceholder();void search();},{signal:pageListeners.signal});
  mobile.addEventListener('change',()=>{updatePlaceholder();if(dialog.open){clearTimeout(timer);void search();}},{signal:pageListeners.signal});
  pageListeners.signal.addEventListener('abort',()=>{clearTimeout(timer);clearTimeout(progressTimer);controller?.abort();},{once:true});
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); open(); }
  }, { signal: pageListeners.signal });
}
