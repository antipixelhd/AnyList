import { bindBrowseCardActions, bindBrowseCardTouch, paintBrowseCardState, sameBrowseTitle } from './browse-card-actions';
import { contributorParams, contributorYear, contributorMediaLabel, workHref, type ContributorPage, type ContributorWork } from './contributor-page-data';
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

export function initializeContributorPage(root: HTMLElement, signal: AbortSignal) {
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
  const ready = new Set<string>();
  const params = contributorParams(new URLSearchParams(location.search), data.list_count !== null);
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
  form.addEventListener('change', () => form.requestSubmit(), {signal});
  const scrollToWorks = createSectionScroll(signal);
  root.querySelector<HTMLAnchorElement>('.contributor-list-link')?.addEventListener('click',event=>{
    if(event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
    const scope=form.elements.namedItem('list_scope') as HTMLInputElement|null;
    if(!scope)return;
    event.preventDefault();
    if(!scope.checked){scope.checked=true;form.requestSubmit();}
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
    root.querySelector<HTMLElement>('[data-contributor-empty]')!.hidden = [...items.keys()].some(card => !card.hidden);
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
    if (data.list_count !== null && (item.id || item.tmdb_id)) editorStore.seed(item);
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
      card.dataset.workKey = item.key;
      card.querySelector('h2')!.textContent = item.title;
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
  const requestPage=async(path:string,requestSignal:AbortSignal):Promise<ContributorPage>=>{
    const response=await fetch(path,{signal:requestSignal,cache:'no-store'});
    if(!response.ok)throw new Error('Unable to load more works. Please try again.');
    return response.json();
  };
  const prediction=createPrediction<ContributorPage>(requestPage,signal);
  let filterRequest:AbortController|undefined,revision=0;
  signal.addEventListener('abort',()=>filterRequest?.abort(),{once:true});
  document.addEventListener('anylist:cache-invalidated',prediction.cancel,{signal});
  const nextPath=()=>{
    const query=new URLSearchParams(params);
    query.set('page',String(data.page+1));query.set('source',data.source);
    if(data.next_cursor)query.set('cursor',data.next_cursor);else query.delete('cursor');
    return `/api/proxy/tracking/${root.dataset.endpoint}?${query}`;
  };
  const loadMore=async()=>{
    if(more.disabled||filterRequest||!data.has_more||signal.aborted)return;
    const current=revision;
    more.disabled=true;more.textContent='Loading…';error.hidden=true;
    try{
      const path=nextPath();
      const next=await prediction.consume(path,signal)??await requestPage(path,signal);
      if(signal.aborted||current!==revision)return;
      append(next.works);
      data.page=next.page;data.source=next.source;data.next_cursor=next.next_cursor;data.has_more=next.has_more;
      more.hidden=!data.has_more;
    }catch(cause){if(!signal.aborted&&current===revision)showError(cause instanceof Error?cause.message:'Unable to load more works.');}
    finally{if(!signal.aborted&&current===revision){more.disabled=false;more.textContent='Load more';}}
  };
  more.addEventListener('click',()=>void loadMore(),{signal});
  form.addEventListener('submit',event=>{
    event.preventDefault();prediction.cancel();filterRequest?.abort();
    const request=new AbortController(),current=++revision;
    filterRequest=request;error.hidden=true;region.setAttribute('aria-busy','true');
    const submitted=new URLSearchParams();
    new FormData(form).forEach((value,key)=>submitted.set(key,String(value)));
    const query=contributorParams(submitted,data.list_count!==null);
    void requestPage(`/api/proxy/tracking/${root.dataset.endpoint}?${query}`,request.signal).then(next=>{
      if(signal.aborted||current!==revision)return;
      // Replace only the works; keep biography, controls, focus and viewport intact.
      region.replaceChildren();items.clear();
      Object.assign(data,next);
      for(const key of [...params.keys()])params.delete(key);
      query.forEach((value,key)=>params.set(key,value));
      root.dataset.mediaType=params.get('media_type')||'all';
      append(next.works);more.hidden=!data.has_more;more.disabled=false;more.textContent='Load more';
      const notice=root.querySelector<HTMLElement>('[data-contributor-notice]')!;
      notice.textContent=next.notice||'';notice.hidden=!next.notice;
      const url=new URL(location.href);url.search=query.toString();
      url.searchParams.set('view',requestedView||'timeline');history.replaceState(history.state,'',url);
    }).catch(cause=>{
      if(!signal.aborted&&!request.signal.aborted)showError(cause instanceof Error?cause.message:'Unable to load works.');
    }).finally(()=>{
      if(!signal.aborted&&current===revision){
        filterRequest=undefined;region.removeAttribute('aria-busy');
        more.disabled=false;more.textContent='Load more';
      }
    });
  },{signal});
  bindPredictivePagination(region,signal,()=>!filterRequest&&data.has_more&&!more.disabled&&error.hidden,
    ()=>prediction.prefetch(nextPath()),()=>void loadMore());

}
