import { bindBrowseCardActions, bindBrowseCardTouch, paintBrowseCardState, type BrowseItem } from './browse-card-actions';
import { formatDate } from './date-time';
import { wireSearchWordCapitalization } from './search-word-capitalization';
import { STREAMING_REGION_COOKIE, deviceStreamingCountry, streamingRegionPreference } from './streaming-region';
import {
  isCategoryView,
  hasBrowseFilters,
  genreSelectionLabel,
  browseRequestParams,
  sectionHref,
} from "./browse-view";
import { applyResponsiveArtwork } from "./responsive-artwork";
import { createBrowseLoading, prepareBrowsePoster } from "./browse-loading";
import { initializeBrowseRanges } from "./browse-ranges";
import { createBrowsePaginationDemand } from "./browse-pagination";
import { initializeMobileBrowseFilters } from "./browse-mobile-filters";
import { BROWSE_FACET_TTL, browseResultTtl, createBrowseCache, waitForBrowseRequest } from "./browse-cache";
import {createPrediction, paginationPosition} from "./predictive-loading";
import { initializeBrowseSort } from "./browse-sort";
import { createBrowseOverride } from './browse-override';
import {createSearchExpansion, shouldBroadenSearch, broadSearchParams} from './search-fallback';
import { appendBrowseTimeline } from './browse-timeline';
import { initializeScrollMotion } from "./scroll-motion";
import { editorStore, type EditorTitle } from "./editor-store";
import { cancelUiMotion, showMenu, hideMenu, dismiss } from "./ui-motion";

type Item = BrowseItem & {search_rank?: number};
type Payload = {
  results: Item[];
  has_more: boolean;
  page: number;
  source: string;
  cursor?: string | null;
  notice?: string;
  partial?: boolean;
  curation_expanded?: boolean;
};
type Section = {
  title: string;
  filters: Record<string, string>;
  results: Item[];
};
type SectionsPayload = { sections: Section[]; notice?: string };
type Facet = { id: number | string; name: string };
export function initializeBrowse(root: HTMLElement, signal: AbortSignal) {
  root.dataset.enhanced = "";
  root.dataset.browseScripted = "";
  // Server-rendered cards stay painted while controls enhance. Only new cards
  // and later scroll entries need an entrance.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (signal.aborted || !root.isConnected) return;
    initializeScrollMotion(root, signal, '.browse-card-body, .browse-skeleton-body', 'browse-scroll-reveal', {
      animateInitial: false, downwardOnly: true, scaleEntrance: true, once: true,
      appearedAttribute: 'data-browse-appeared',
    });
  }));
  const find = <T extends HTMLElement>(selector: string) =>
    root.querySelector<T>(selector)!;
  const form = find<HTMLFormElement>("[data-browse-form]");
  initializeMobileBrowseFilters(form, signal);
  const searchClear = find<HTMLButtonElement>("[data-browse-search-clear]");
  const field = (name: string) =>
    form.elements.namedItem(name) as HTMLInputElement | HTMLSelectElement;
  wireSearchWordCapitalization(field('q') as HTMLInputElement, signal);
  const grid = find<HTMLElement>("[data-browse-results]");
  const sections = find<HTMLElement>("[data-browse-sections]");
  const paintRanges = initializeBrowseRanges(form, signal);
  const advancedFilters = find<HTMLDetailsElement>(".browse-more");
  const advancedPanel = find<HTMLElement>(".browse-secondary-filters");
  const skeleton = find<HTMLElement>("[data-browse-skeleton]");
  const region = find<HTMLElement>("[data-browse-region]");
  const loadingView = createBrowseLoading(region, skeleton);
  const empty = find<HTMLElement>("[data-browse-empty]");
  const notice = find<HTMLElement>("[data-browse-notice]");
  const error = find<HTMLElement>("[data-browse-error]");
  const more = find<HTMLButtonElement>("[data-browse-more]");
  const showAll = find<HTMLButtonElement>('[data-browse-show-all]');
  const curated = find<HTMLButtonElement>('[data-browse-curated]');
  const resultOverride = createBrowseOverride(field('q').value);
  const automaticExpansion = createSearchExpansion();
  const end = find<HTMLElement>("[data-browse-end]");
  const sort = find<HTMLSelectElement>("[data-browse-sort]");
  const syncSort = initializeBrowseSort(root, signal, changed);
  const chips = find<HTMLElement>("[data-active-filters]");
  const template = find<HTMLTemplateElement>("[data-browse-template]");
  const initial = JSON.parse(
    find<HTMLElement>("#browse-data").textContent || "{}",
  );
  let payload: Payload = initial.payload;
  let displayedCategories = initial.pending ? initial.categories : !sections.hidden;
  let facets: { genres: Facet[]; providers: Facet[] } = initial.facets;
  let storage: Storage | undefined;
  try { storage = window.sessionStorage; } catch { /* Continue with a page-local cache. */ }
  const viewer = document.body.dataset.cacheUser || 'anonymous';
  const responseCache = createBrowseCache<Payload | SectionsPayload>(undefined, browseResultTtl, undefined,
    storage && {storage, name: 'results', viewer});
  const prediction=createPrediction<Payload | SectionsPayload>(async(path,requestSignal)=>json(path,{signal:requestSignal}),signal);
  const facetCache = createBrowseCache<typeof facets>(undefined, BROWSE_FACET_TTL, undefined,
    storage && {storage, name: 'facets', viewer});
  let contextRevision = 0;
  document.addEventListener('anylist:editor-state', () => { contextRevision++; }, {signal});
  document.addEventListener('anylist:cache-invalidated',()=>{
    contextRevision++;
    prediction.cancel();responseCache.clear();facetCache.clear();
  },{signal});
  const readyPosters = new Set<string>();
  let currentPath: string | undefined;
  let refreshingSelect = false;
  const refreshSelect = (select: HTMLInputElement | HTMLSelectElement) => {
    refreshingSelect = true;
    select.dispatchEvent(new Event('change', {bubbles: true}));
    refreshingSelect = false;
  };
  const items = new Map<HTMLElement, Item>();
  const seen = new Set<string>();
  let request: AbortController | undefined,
    facetRequest: AbortController | undefined;
  let loading = false,
    suspended = false;
  let generation = 0;
  const paginationDemand = createBrowsePaginationDemand(scrollY);
  const titleKey = (item: Item) => `${item.type}:${item.tmdb_id || item.id}`;
  const key = (item: Item) => item.release_event || titleKey(item);
  const params = () => {
    const values = new URLSearchParams();
    for (const name of [
      "q",
      "genres",
      "start",
      "end",
      "status",
      "provider",
      "region",
      "min_score",
    ]) {
      const value = field(name).value.trim();
      if (value) values.set(name, value);
    }
    values.set("media_type", field("type").value);
    if(field('type').value==='movie'&&(field('cinema') as HTMLInputElement).checked)values.set('cinema','1');
    values.set("sort", sort.value);
    if(field('view').value==='timeline')values.set('view','timeline');
    const automaticallyExpanded = automaticExpansion.forSearch(values);
    if(resultOverride.forQuery(field('q').value)||automaticallyExpanded)values.set('unfiltered','true');
    return values;
  };
  if (payload.curation_expanded) automaticExpansion.activate(params());
  const categoryMode = (values: URLSearchParams) => !values.has('unfiltered') && isCategoryView(values);
  const requestPath = (values: URLSearchParams) => categoryMode(values)
    ? `browse/sections?${new URLSearchParams({media_type: values.get('media_type')!, region: values.get('region')!, min_score: values.get('min_score')!})}`
    : `browse?${browseRequestParams(values)}`;
  const initialValues = params();
  initialValues.set('page', '1');
  if (!initial.pending && error.hidden) responseCache.set(requestPath(initialValues), displayedCategories
    ? {sections: initial.sections, notice: payload.notice} : payload);
  if (!initial.pending) facetCache.set(`${field('type').value}:${field('region').value}:${field('genres').value}`, facets);
  const updateUrl = () => {
    const values = params();
    values.delete('unfiltered');
    values.delete("media_type");
    if (field("type").value === "series") values.set("type", "series");
    if (values.get("sort") === "all") values.delete("sort");
    if (values.get("min_score") === "0") values.delete("min_score");
    history.replaceState(
      history.state,
      "",
      `${location.pathname}${values.size ? `?${values}` : ""}`,
    );
    root.querySelectorAll<HTMLAnchorElement>('[data-browse-layout]').forEach(link=>{
      const target=new URLSearchParams(values);
      if(link.dataset.browseLayout==='timeline')target.set('view','timeline');else target.delete('view');
      link.href=`${location.pathname}${target.size?'?'+target:''}`;
      if(link.dataset.browseLayout===field('view').value)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    });
    root.querySelectorAll<HTMLAnchorElement>('[data-browse-type]').forEach(link=>{
      const target=new URLSearchParams(values);target.delete('genres');target.delete('status');target.delete('cinema');
      if(link.dataset.browseType==='series')target.set('type','series');else target.delete('type');
      link.href=`${location.pathname}${target.size?'?'+target:''}`;
    });
  };
  const json = async (path: string, options: RequestInit = {}) => {
    const response = await fetch(`/api/proxy/tracking/${path}`, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok)
      throw new Error(
        typeof body.detail === "string"
          ? body.detail
          : "Unable to load titles. Try again.",
      );
    return body;
  };
  const paintState = paintBrowseCardState;
  const initialItems = new Map<string, Item>(
    [
      ...payload.results,
      ...(initial.sections || []).flatMap(
        (section: Section) => section.results,
      ),
    ].map((item: Item) => [key(item), item]),
  );
  region.querySelectorAll<HTMLElement>("[data-browse-card]").forEach((card) => {
    const poster = card.querySelector<HTMLImageElement>("[data-poster]");
    if (poster) prepareBrowsePoster(poster, signal, readyPosters);
    const item = initialItems.get(card.dataset.key!);
    if (!item) return;
    items.set(card, item);
    if ('entry' in item) editorStore.seed(item as EditorTitle);
    if (grid.contains(card)) seen.add(key(item));
    paintState(card, item);
  });
  const syncItem = (updated: Item) => {
    const entryState = {id: updated.id, entry: updated.entry, list_status: updated.list_status,
      score: updated.score, rating_mode: updated.rating_mode, favorite: updated.favorite,
      editor_library: updated.editor_library};
    for (const cached of responseCache.values()) {
      const cachedItems = 'sections' in cached ? cached.sections.flatMap(section => section.results) : cached.results;
      for (const item of cachedItems) if (titleKey(item) === titleKey(updated)) Object.assign(item, entryState);
    }
    for (const [card, item] of items) {
      if (titleKey(item) !== titleKey(updated)) continue;
      Object.assign(item, entryState);
      card.querySelectorAll<HTMLAnchorElement>("a").forEach((link) => {
        if (item.id) link.href = `/title/${item.id}`;
      });
      paintState(card, item);
    }
  };
  const refreshViewerContext = async (response: Payload | SectionsPayload, current: AbortController) => {
    if (viewer === 'anonymous') return;
    const selection = 'sections' in response
      ? response.sections.flatMap(section => section.results) : response.results;
    const unique = new Map<string, Item>();
    for (const item of selection) if (item.id || item.tmdb_id) unique.set(titleKey(item), item);
    if (!unique.size) return;
    const revision = contextRevision;
    try {
      const fresh = await json('browse/context', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({items: [...unique.values()].slice(0, 80).map(({id, tmdb_id, type}) => ({
          id: tmdb_id ? null : id, tmdb_id, type,
        }))}),
        signal: current.signal,
      }) as {items: Item[]};
      if (current.signal.aborted || signal.aborted || request !== current || revision !== contextRevision) return;
      for (const row of fresh.items) {
        const displayed = unique.get(titleKey(row));
        if (!displayed) continue;
        const title = editorStore.seed({...displayed, ...row} as EditorTitle);
        syncItem({...displayed, ...row, ...title,
          list_status: title.entry?.status ?? null, score: title.entry?.score ?? null,
          rating_mode: title.entry?.rating_mode ?? 'manual'});
      }
    } catch { /* Cached public results remain usable if viewer-state refresh fails. */ }
  };
  const render = (results: Item[], destination = grid, deduplicate = true) => {
    const fragment = document.createDocumentFragment();
    const cards:HTMLElement[]=[];
    for (const item of results) {
      if (deduplicate && seen.has(key(item))) continue;
      if (deduplicate) seen.add(key(item));
      const card = template.content.firstElementChild!.cloneNode(
        true,
      ) as HTMLElement;
      card.dataset.key = key(item);
      const href = item.id
        ? `/title/${item.id}`
        : `/discover-title?type=${item.type}&id=${item.tmdb_id}`;
      card
        .querySelectorAll<HTMLAnchorElement>("a")
        .forEach((a) => (a.href = href));
      card.querySelector("h2")!.textContent = item.title;
      card
        .querySelector(".browse-poster-link")!
        .setAttribute("aria-label", item.title);
      const img = card.querySelector<HTMLImageElement>("[data-poster]")!;
      img.hidden = !item.poster;
      if (item.poster)
        applyResponsiveArtwork(img, item.poster, {
          size: "w342",
          sizes: "(max-width: 650px) 30vw, (max-width: 1000px) 22vw, 185px",
          route:'auto',
        });
      prepareBrowsePoster(img, signal, readyPosters);
      const placeholder = card.querySelector<HTMLElement>(
        ".browse-placeholder",
      )!;
      placeholder.hidden = !!item.poster;
      placeholder.textContent = item.title.slice(0, 1);
      card.querySelector("[data-year]")!.textContent = item.year || "";
      if (item.release_event) card.querySelector<HTMLElement>('[data-year]')!.title = item.release_country ? `Release date · ${item.release_country}` : 'Worldwide release date';
      card
        .querySelectorAll<HTMLButtonElement>("[data-browse-action]")
        .forEach((button) => {
          const label =
            button.dataset.browseAction === "edit"
              ? "Open list editor for"
              : button.dataset.browseAction === "plan"
                ? "Plan to watch"
                : button.dataset.browseAction === "watch" ? "Set to Watching" : "Rate and complete";
          button.setAttribute("aria-label", `${label} ${item.title}`);
        });
      paintState(card, item);
      items.set(card, item);
      cards.push(card);
      if ('entry' in item) editorStore.seed(item as EditorTitle);
      fragment.append(card);
    }
    if(destination===grid&&field('view').value==='timeline')appendBrowseTimeline(destination,cards,items,sort.value==='upcoming'||field('status').value==='upcoming');
    else destination.append(fragment);
    return cards;
  };
  const renderSections = (results: Section[]) => {
    for (const section of results) {
      const container = document.createElement("section");
      container.className = "browse-category";
      container.setAttribute("aria-label", section.title);
      const header = document.createElement("header"),
        heading = document.createElement("h2"),
        link = document.createElement("a"),
        cards = document.createElement("div");
      const headingLink = document.createElement("a");
      headingLink.textContent = section.title;
      link.textContent = "View all";
      link.dataset.viewAll = "";
      link.href = sectionHref(
        field("type").value,
        field("region").value,
        section.filters,
      );
      headingLink.href = link.href;
      headingLink.dataset.viewAll = "";
      heading.append(headingLink);
      header.append(heading, link);
      cards.className = "browse-grid";
      render(section.results, cards, false);
      container.append(header, cards);
      if (!section.results.length) {
        const message = document.createElement("p");
        message.className = "browse-category-empty";
        message.textContent = "No titles found.";
        container.append(message);
      }
      sections.append(container);
    }
  };
  const paintChips = () => {
    paintRanges();
    chips.replaceChildren();
    const country = new Intl.DisplayNames(['en'], {type:'region'}).of(field('region').value) || field('region').value;
    find<HTMLElement>('[data-browse-cinema]').dataset.cinemaLabel = `Theatrical · ${country}`;
    find<HTMLElement>('[data-browse-cinema]').title = `Theatrical · ${country}`;
    (field('cinema') as HTMLInputElement).setAttribute('aria-label', `Theatrical · ${country}`);
    const add = (label: string, onRemove: () => void) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.setAttribute("aria-label", `Remove ${label} filter`);
      button.append(
        find<HTMLTemplateElement>("[data-browse-close-icon]").content.cloneNode(
          true,
        ),
      );
      button.addEventListener("click", () => {
        onRemove();
        changed();
      });
      chips.append(button);
    };
    for (const genre of facets.genres)
      if (field("genres").value.split(",").includes(String(genre.id)))
        add(genre.name, () => {
          const input = form.querySelector<HTMLInputElement>(
            `[name=genre][value="${genre.id}"]`,
          );
          if (input) input.checked = false;
          syncGenres();
        });
    for (const name of ["start", "end", "status", "provider", "min_score"])
      if (field(name).value && (name !== "min_score" || field(name).value !== "0")) {
        const label =
          name === "start"
            ? `After ${formatDate(field(name).value)}`
            : name === "end"
              ? `Before ${formatDate(field(name).value)}`
              : name === "min_score" ? `${Number(field(name).value).toLocaleString()}+ TMDB score`
              : (field(name) as HTMLSelectElement).selectedOptions[0]
                  ?.textContent || field(name).value;
        add(label, () => {
          if (name === 'status' && sort.value === 'upcoming') sort.value = 'newest';
          field(name).value = name === "min_score" ? "0" : "";
          field(name).dispatchEvent(new Event("input", { bubbles: true }));
        });
      }
    if (field("q").value.trim())
      add(`“${field("q").value.trim()}”`, () => {
        field("q").value = "";
      });
    if((field('cinema') as HTMLInputElement).checked&&field('type').value==='movie')
      add(find<HTMLElement>('[data-browse-cinema]').dataset.cinemaLabel || 'Cinema release',()=>{(field('cinema') as HTMLInputElement).checked=false;});
    if (chips.childElementCount) {
      const reset = document.createElement("button");
      reset.type = "button";
      reset.dataset.reset = "";
      reset.textContent = "Clear all";
      reset.addEventListener("click", clear);
      chips.append(reset);
    }
    root.toggleAttribute('data-browse-chips', !!chips.children.length);
    syncSort(!!field("q").value.trim(), true, hasBrowseFilters(params()));
  };
  const syncGenres = () => {
    field("genres").value = [
      ...form.querySelectorAll<HTMLInputElement>("[name=genre]:checked"),
    ]
      .map((input) => input.value)
      .join(",");
    find("[id=browse-genres-value]").textContent = genreSelectionLabel(field("genres").value, facets.genres);
  };
  const updateFooter = () => {
    const manualOverride = resultOverride.forQuery(field('q').value);
    const overridden = manualOverride || automaticExpansion.forSearch(params());
    root.toggleAttribute('data-browse-override', overridden);
    showAll.hidden = overridden || displayedCategories || loading || suspended || payload.has_more || !error.hidden;
    curated.hidden = !manualOverride;
    curated.disabled = loading;
    find("[data-browse-sentinel]").hidden = displayedCategories;
    more.hidden = !payload.has_more;
    more.disabled = loading;
    end.hidden = payload.has_more || !grid.childElementCount;
    end.textContent = payload.partial ? 'End of curated results' : "You're all caught up";
    empty.hidden =
      displayedCategories ||
      loading ||
      payload.has_more ||
      !!grid.childElementCount ||
      !error.hidden;
  };
  const closeMenus = (except?: HTMLDetailsElement) =>
    form
      .querySelectorAll<HTMLDetailsElement>("[data-filter-menu]")
      .forEach((menu) => {
        if (menu === except || !menu.open) return;
        void closeFilter(menu);
      });
  async function closeFilter(menu: HTMLDetailsElement, restoreFocus = false) {
    const panel = menu.querySelector<HTMLElement>(".browse-options")!;
    await hideMenu(panel);
    if (panel.hidden) menu.open = false;
    if (restoreFocus) menu.querySelector<HTMLElement>("summary")?.focus();
  }
  async function closeAdvancedFilters(restoreFocus = false) {
    if (!advancedFilters.open || advancedPanel.inert) return;
    advancedPanel.inert = true;
    if (await dismiss(advancedPanel)) {
      advancedFilters.open = false;
      advancedPanel.inert = false;
      if (restoreFocus) advancedFilters.querySelector<HTMLElement>("summary")?.focus();
    }
  }
  async function load(append = false, debounce = 0) {
    if (
      signal.aborted ||
      (append &&
        (displayedCategories || loading || !payload.has_more || suspended))
    )
      return;
    if (!form.reportValidity()) {
      loadingView.finish();
      region.setAttribute("aria-busy", "false");
      return;
    }
    if(!append)prediction.cancel();
    const values = params();
    const categoryView = categoryMode(values);
    values.set("page", String(append ? payload.page + 1 : 1));
    if (append) {
      values.set("source", payload.source);
      if(payload.cursor)values.set('cursor',payload.cursor);
    }
    const path = requestPath(values);
    if (loading && currentPath === path) return;
    request?.abort();
    const current = new AbortController();
    request = current;
    currentPath = path;
    const epoch = ++generation;
    loading = true;
    paginationDemand.reset(scrollY);
    suspended = false;
    error.hidden = true;
    const cached = responseCache.get(path);
    let expanded = false;
    empty.hidden = true;
    region.setAttribute("aria-busy", "true");
    updateFooter();
    updateUrl();
    if (!append) {
      cancelUiMotion(region);
      grid.hidden = true;
      sections.hidden = true;
      // Hide the old DOM before changing its layout. A cached first page may
      // still need asynchronous viewport filling, so it also needs this handoff.
      const view = field('view').value;
      root.dataset.browseView = view;
      grid.className = view === 'timeline' ? 'browse-timeline' : 'browse-grid';
      notice.textContent = "";
      loadingView.show(false, categoryView, field('view').value==='timeline');
    } else if (!field('q').value.trim()) {
      loadingView.show(true, false, field('view').value==='timeline');
    }
    paintChips();
    try {
      let response = cached;
      if (!response) {
        await waitForBrowseRequest(debounce, current.signal);
        response = await prediction.consume(path,current.signal) ?? await json(path, {signal: current.signal}) as Payload | SectionsPayload;
        if (current.signal.aborted || signal.aborted || request !== current) return;
      }
      async function fillViewport(first: Payload, requestValues: URLSearchParams): Promise<Payload> {
        let response = first;
        const merged = new Map(response.results.map(item => [key(item), item]));
        const columns = Math.max(3, Math.floor(region.clientWidth / 160));
        const target = Math.max(24, columns * (Math.ceil(innerHeight / 260) + 1));
        for (let pages = 0; response.has_more && merged.size < target && pages < 5; pages++) {
          const next = new URLSearchParams(requestValues);
          next.set('page', String(response.page + 1)); next.set('source', response.source);
          if (response.cursor) next.set('cursor', response.cursor);
          const nextPath = requestPath(next);
          const cachedBatch = responseCache.get(nextPath) as Payload | undefined;
          const batch = cachedBatch ?? await json(nextPath, {signal: current.signal}) as Payload;
          if (current.signal.aborted || signal.aborted || request !== current) throw new DOMException('Search changed', 'AbortError');
          responseCache.set(nextPath, batch, !cachedBatch);
          batch.results.forEach(item => merged.set(key(item), item));
          response = {...batch, results: [...merged.values()]};
        }
        if (field('q').value.trim()) response.results.sort((a,b) => (b.search_rank || 0) - (a.search_rank || 0));
        return response;
      }
      if (!append && response && 'results' in response) {
        response = await fillViewport(response, values);
        responseCache.set(path, response, !cached);
        if (shouldBroadenSearch(values, response) && automaticExpansion.allowed(values)) {
          const broad = broadSearchParams(values);
          const broadPath = requestPath(broad);
          const cachedBroad = responseCache.get(broadPath) as Payload | undefined;
          const first = cachedBroad ?? await json(broadPath, {signal: current.signal}) as Payload;
          if (current.signal.aborted || signal.aborted || request !== current) return;
          response = await fillViewport(first, broad);
          responseCache.set(broadPath, response, !cachedBroad);
          expanded = true;
        }
      }
      if (!expanded) responseCache.set(path, response!, !cached);
      if (!cached) await loadingView.settle(current.signal);
      if (request !== current || epoch !== generation || signal.aborted) return;
      if (expanded) automaticExpansion.activate(values);
      if (!append) {
        cancelUiMotion(region);
        grid.replaceChildren();
        sections.replaceChildren();
        seen.clear();
        items.clear();
      }
      displayedCategories = categoryView;
      if ("sections" in response) {
        renderSections(response.sections);
        loadingView.handoff([...sections.querySelectorAll<HTMLElement>('.browse-card-body')]);
        payload = { results: [], has_more: false, page: 1, source: "" };
      } else {
        const cards=render(response.results);
        loadingView.handoff(cards.map(card=>card.querySelector<HTMLElement>('.browse-card-body')!));
        payload = response;
      }
      notice.textContent = response.notice || "";
      if (cached) void refreshViewerContext(response, current);
    } catch (cause) {
      await loadingView.settle(current.signal);
      if (current.signal.aborted || request !== current || signal.aborted)
        return;
      suspended = true;
      error.hidden = false;
      error.querySelector("span")!.textContent = (cause as Error).message;
      if (!append) {
        cancelUiMotion(region);
        grid.replaceChildren();
        sections.replaceChildren();
        displayedCategories = categoryView;
        items.clear();
        seen.clear();
        payload = { results: [], has_more: false, page: 0, source: "" };
      }
    } finally {
      if (request === current && epoch === generation && !signal.aborted) {
        currentPath = undefined;
        loading = false;
        paginationDemand.reset(scrollY);
        loadingView.finish();
        grid.hidden = displayedCategories;
        sections.hidden = !displayedCategories;
        region.setAttribute("aria-busy", "false");
        updateFooter();
      }
    }
  }
  function changed(event?: Event) {
    if (sort.value === 'upcoming' && field('status').value !== 'upcoming') {
      if (event?.target === field('status')) sort.value = 'newest';
      else {
        field('status').value = 'upcoming';
        refreshSelect(field('status') as HTMLSelectElement);
      }
    }
    searchClear.hidden = !field("q").value;
    // Update immediately; only network requests from typing/dragging are debounced.
    const debounce = event?.type === 'input' ? 300 : 0;
    void load(false, debounce);
  }
  function clear() {
    (field('cinema') as HTMLInputElement).checked=false;
    for (const name of [
      "q",
      "genres",
      "start",
      "end",
      "status",
      "provider",
    ])
      field(name).value = "";
    form
      .querySelectorAll<HTMLInputElement>("[name=genre]")
      .forEach((input) => (input.checked = false));
    ["status", "provider"].forEach((name) =>
      field(name).dispatchEvent(new Event("input", { bubbles: true })),
    );
    sort.value = "all";
    field("min_score").value = "0";
    syncGenres();
    changed();
  }
  async function loadFacets() {
    facetRequest?.abort();
    const current = new AbortController();
    facetRequest = current;
    const cacheKey = `${field('type').value}:${field('region').value}:${field('genres').value}`;
    try {
      const cachedFacets = facetCache.get(cacheKey);
      const result = cachedFacets ?? await json(
        `browse/facets?${new URLSearchParams({ media_type: field("type").value, region: field("region").value, genres: field("genres").value })}`,
        { signal: current.signal },
      );
      if (current !== facetRequest || current.signal.aborted || signal.aborted) return;
      facetCache.set(cacheKey, result, !cachedFacets);
      facets = result;
      find("[id=browse-genres-value]").textContent = genreSelectionLabel(field("genres").value, facets.genres);
      const options = find<HTMLElement>("[data-genre-options]");
      options.replaceChildren();
      for (const genre of facets.genres) {
        const label = document.createElement("label"),
          input = document.createElement("input"),
          span = document.createElement("span");
        input.type = "checkbox";
        input.name = "genre";
        input.value = String(genre.id);
        input.checked = field("genres")
          .value.split(",")
          .includes(String(genre.id));
        span.textContent = genre.name;
        label.append(input, span);
        options.append(label);
      }
      const provider = field("provider") as HTMLSelectElement,
        selected = provider.value;
      provider.replaceChildren(
        new Option("Any service", ""),
        ...facets.providers.map((p) => new Option(p.name, String(p.id))),
      );
      provider.value = selected;
      if (!provider.value) provider.value = "";
      refreshSelect(provider);
      if (selected !== provider.value) changed();
      notice.textContent = result.notice || "";
      paintChips();
    } catch (cause) {
      if (!current.signal.aborted && !signal.aborted) {
        notice.textContent = "Unable to load filter options. Try again.";
      }
    }
  }
  form.addEventListener(
    "submit",
    (event) => {
      event.preventDefault();
      void load();
    },
    { signal },
  );
  showAll.addEventListener('click', () => {
    resultOverride.set(true);
    void load();
  }, {signal});
  curated.addEventListener('click', () => {
    resultOverride.set(false);
    automaticExpansion.suppress(params());
    void load();
  }, {signal});
  form.addEventListener(
    "input",
    (event) => {
      const target = event.target as HTMLInputElement;
      if (target === field("q") || target.type === 'range' || ['start', 'end'].includes(target.name)) changed(event);
    },
    { signal },
  );
  searchClear.addEventListener("click", () => {
    field("q").value = "";
    field("q").focus();
    field("q").dispatchEvent(new Event("input", { bubbles: true }));
  }, { signal });
  form.addEventListener(
    "change",
    (event) => {
      const target = event.target as HTMLInputElement;
      if (refreshingSelect) return;
      if (target.name === "genre") {
        if (form.querySelectorAll("[name=genre]:checked").length > 12) {
          target.checked = false;
          notice.textContent = "Choose up to 12 genres.";
          return;
        }
        syncGenres();
      } else if (
        !["status", "provider", "region", "start", "end", "min_score", "cinema"].includes(target.name)
      )
        return;
      if (target.name === "region") {
        document.cookie = `${STREAMING_REGION_COOKIE}=manual.${target.value}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
        void loadFacets();
      }
      changed(event);
    },
    { signal },
  );
  bindBrowseCardTouch(region, signal);
  sort.addEventListener("change", changed, { signal });
  root.querySelectorAll<HTMLAnchorElement>('[data-browse-layout]').forEach(link=>link.addEventListener('click',event=>{
    if(event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    event.preventDefault();
    const view=link.dataset.browseLayout!;
    if(field('view').value===view)return;
    field('view').value=view;
    changed();
  },{signal}));
  root
    .querySelectorAll<HTMLAnchorElement>("[data-browse-type]")
    .forEach((link) =>
      link.addEventListener(
        "click",
        (event) => {
          event.preventDefault();
          if (field("type").value === link.dataset.browseType) return;
          field("type").value = link.dataset.browseType!;
          (field('cinema') as HTMLInputElement).checked=false;
          find<HTMLElement>('[data-browse-cinema]').hidden=field('type').value!=='movie';
          root
            .querySelectorAll("[data-browse-type]")
            .forEach((a) => a.removeAttribute("aria-current"));
          link.setAttribute("aria-current", "page");
          field("genres").value = "";
          syncGenresAfterType();
          field("status").value = "";
          const statuses =
            field("type").value === "movie"
              ? [
                  ["released", "Released"],
                  ["upcoming", "Upcoming"],
                ]
              : [
                  ["airing", "Airing"],
                  ["returning", "Returning Series"],
                  ["finished", "Finished"],
                  ["cancelled", "Cancelled"],
                  ["upcoming", "Upcoming"],
                ];
          (field("status") as HTMLSelectElement).replaceChildren(
            new Option("Any status", ""),
            ...statuses.map(([id, name]) => new Option(name, id)),
          );
          refreshSelect(field("status"));
          void loadFacets();
          changed();
        },
        { signal },
      ),
    );
  function syncGenresAfterType() {
    form
      .querySelectorAll<HTMLInputElement>("[name=genre]")
      .forEach((input) => (input.checked = false));
    syncGenres();
  }
  for (const menu of form.querySelectorAll<HTMLDetailsElement>(
    "[data-filter-menu]",
  )) {
    menu.querySelector("summary")!.addEventListener(
      "click",
      (event) => {
        event.preventDefault();
        const panel = menu.querySelector<HTMLElement>(".browse-options")!;
        if (menu.open && !panel.inert) void closeFilter(menu);
        else {
          closeMenus(menu);
          menu.open = true;
          showMenu(panel);
        }
      },
      { signal },
    );
  }
  document.addEventListener(
    "click",
    (event) => {
      if (!(event.target as Element).closest("[data-filter-menu]"))
        closeMenus();
      // Inline phone filters behave like the list-page section, not a popover.
      if (!form.hasAttribute('data-mobile-filters') && !advancedFilters.contains(event.target as Node) &&
          !(event.target as Element).closest(".browse-select-options"))
        void closeAdvancedFilters();
    },
    { signal },
  );
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !(event.target as Element).closest('[role="listbox"]'))
      void closeAdvancedFilters(true);
  }, { signal });
  form.addEventListener(
    "keydown",
    (event) => {
      if (event.key === "Escape") {
        const menu = (event.target as Element).closest<HTMLDetailsElement>(
          "[data-filter-menu]",
        );
        if (menu?.open) {
          event.stopPropagation();
          void closeFilter(menu, true);
        }
      }
    },
    { signal },
  );
  root
    .querySelectorAll("[data-browse-clear]")
    .forEach((button) => button.addEventListener("click", clear, { signal }));
  more.addEventListener(
    "click",
    () => {
      suspended = false;
      void load(true);
    },
    { signal },
  );
  find("[data-browse-retry]").addEventListener(
    "click",
    () => {
      suspended = false;
      void load(!displayedCategories && grid.childElementCount > 0);
    },
    { signal },
  );
  const sentinel = find<HTMLElement>("[data-browse-sentinel]");
  let sentinelNearEnd = false;
  let paginationFrame = 0;
  let predictionScrollY=scrollY;
  const canAutoLoad = () => !signal.aborted && !displayedCategories &&
    !loading && !suspended && payload.has_more;
  const autoLoadMore = () => {
    if (sentinelNearEnd && canAutoLoad() && paginationDemand.consume())
      void load(true);
  };
  const observer = new IntersectionObserver(
    (entries) => {
      sentinelNearEnd = entries.some((e) => e.isIntersecting);
      autoLoadMore();
    },
    { rootMargin: "500px 0px" },
  );
  observer.observe(sentinel);
  window.addEventListener("scroll", () => {
    const down=scrollY>predictionScrollY;predictionScrollY=scrollY;
    paginationDemand.advance(scrollY, canAutoLoad());
    if (!paginationFrame) paginationFrame = requestAnimationFrame(() => {
      paginationFrame = 0;
      if(down&&canAutoLoad()&&paginationPosition(region.getBoundingClientRect(),innerHeight).predict){
        const next=params();next.set('page',String(payload.page+1));next.set('source',payload.source);
        if(payload.cursor)next.set('cursor',payload.cursor);
        const path=requestPath(next);if(!responseCache.get(path))prediction.prefetch(path);
      }
      autoLoadMore();
    });
  }, { passive: true, signal });
  bindBrowseCardActions(region, items, signal, syncItem, message => { notice.textContent = message; notice.hidden = false; });
  paintChips();
  updateFooter();
  const savedRegion = streamingRegionPreference(document.cookie.split(';').map(part => part.trim()).find(part => part.startsWith(`${STREAMING_REGION_COOKIE}=`))?.slice(STREAMING_REGION_COOKIE.length + 1));
  if (!new URLSearchParams(location.search).has('region') && !savedRegion?.manual) {
    const detected = deviceStreamingCountry();
    if (detected) {
      document.cookie = `${STREAMING_REGION_COOKIE}=auto.${detected}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
      if (field('region').value !== detected) {
        const select = field('region') as HTMLSelectElement;
        if (![...select.options].some(option => option.value === detected)) {
          const name = new Intl.DisplayNames(['en'], {type:'region'}).of(detected) || detected;
          select.add(new Option(name, detected));
        }
        select.value = detected;
        refreshSelect(select);
        if (!initial.pending) { void loadFacets(); changed(); }
      }
    }
  }
  if (initial.pending) {
    void loadFacets();
    void load();
  } else if (!displayedCategories && payload.has_more && error.hidden && sentinel.getBoundingClientRect().top < innerHeight + 260) void load(false);
  if (error.hidden === false) suspended = true;
  if (
    field("start").value ||
    field("end").value
  )
    find<HTMLDetailsElement>(".browse-more").open = true;
  signal.addEventListener(
    "abort",
    () => {
      loadingView.finish();
      region.setAttribute("aria-busy", "false");
      request?.abort();
      facetRequest?.abort();
      observer.disconnect();
      cancelAnimationFrame(paginationFrame);
    },
    { once: true },
  );
}
