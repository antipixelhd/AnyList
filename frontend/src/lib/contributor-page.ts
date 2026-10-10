import { bindBrowseCardActions, bindBrowseCardTouch, paintBrowseCardState, sameBrowseTitle } from './browse-card-actions';
import { contributorParams, contributorYear, workHref, type ContributorPage, type ContributorWork } from './contributor-page-data';
import { editorStore } from './editor-store';
import { applyResponsiveArtwork } from './responsive-artwork';
import { prepareBrowsePoster } from './browse-loading';

export function initializeContributorPage(root: HTMLElement, signal: AbortSignal) {
  const data: ContributorPage = JSON.parse(root.querySelector('[data-contributor-data]')!.textContent!);
  const region = root.querySelector<HTMLElement>('[data-contributor-works]')!;
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
    root.querySelector<HTMLElement>('[data-contributor-portrait-fallback]')!.hidden = false;
  };
  portrait?.addEventListener('error', missingPortrait, {signal});
  if (portrait?.complete && !portrait.naturalWidth) missingPortrait();
  root.dataset.enhanced = '';
  form.addEventListener('change', () => form.requestSubmit(), {signal});
  root.querySelectorAll<HTMLButtonElement>('[data-contributor-view]').forEach(button => {
    button.addEventListener('click', () => {
      const view = button.dataset.contributorView!;
      root.dataset.view = view;
      region.classList.toggle('browse-grid', view === 'grid');
      (form.elements.namedItem('view') as HTMLInputElement).value = view;
      root.querySelectorAll('[data-contributor-view]').forEach(other => other.setAttribute('aria-pressed', String((other as HTMLElement).dataset.contributorView === view)));
      const url = new URL(location.href); url.searchParams.set('view', view);
      history.replaceState(history.state, '', url);
    }, {signal});
  });
  const showError = (message: string) => { error.textContent = message; error.hidden = false; };
  const visible = (item: ContributorWork) =>
    (params.get('list_scope') !== 'in' || !!item.list_status) &&
    (params.get('list_scope') !== 'out' || !item.list_status) &&
    (!params.get('status') || params.get('status') === item.list_status);
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
      date.hidden = !item.release_date; date.dateTime = item.release_date || ''; date.textContent = item.release_date;
      const image = card.querySelector<HTMLImageElement>('[data-poster]')!;
      image.hidden = !item.poster;
      if (item.poster) applyResponsiveArtwork(image, item.poster, {size:'w342',sizes:'(max-width: 760px) 40vw, 185px',route:'direct'});
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
  more.addEventListener('click', async () => {
    if (more.disabled) return;
    more.disabled = true; more.textContent = 'Loading…'; error.hidden = true;
    const query = new URLSearchParams(params);
    query.set('page', String(data.page + 1)); query.set('source', data.source);
    if (data.next_cursor) query.set('cursor', data.next_cursor); else query.delete('cursor');
    try {
      const response = await fetch(`/api/proxy/tracking/${root.dataset.endpoint}?${query}`, {signal,cache:'no-store'});
      if (!response.ok) throw new Error('Unable to load more works. Please try again.');
      const next: ContributorPage = await response.json();
      if (signal.aborted) return;
      append(next.works);
      data.page = next.page; data.source = next.source; data.next_cursor = next.next_cursor; data.has_more = next.has_more;
      more.hidden = !data.has_more;
    } catch (cause) { if (!signal.aborted) showError(cause instanceof Error ? cause.message : 'Unable to load more works.'); }
    finally { if (!signal.aborted) { more.disabled = false; more.textContent = 'Load more'; } }
  }, {signal});
}
