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
import { createBrowseCache, waitForBrowseRequest } from "./browse-cache";
import { initializeBrowseSort } from "./browse-sort";
import { initializeScrollMotion } from "./scroll-motion";
import { editorStore, type EditorTitle } from "./editor-store";
import { cancelUiMotion, showMenu, hideMenu, dismiss } from "./ui-motion";

type Item = {
  id: number | null;
  tmdb_id: number | null;
  type: string;
  title: string;
  poster?: string;
  year?: string;
  list_status?: string;
  score?: number | null;
  rating_mode?: string;
  backdrop?: string;
  entry?: Record<string, any> | null;
  editor_library?: EditorTitle['editor_library'];
};
type Payload = {
  results: Item[];
  has_more: boolean;
  page: number;
  source: string;
  notice?: string;
};
type Section = {
  title: string;
  filters: Record<string, string>;
  results: Item[];
};
type SectionsPayload = { sections: Section[]; notice?: string };
type Facet = { id: number; name: string };
const statusLabels: Record<string, string> = {
  watching: "Watching",
  completed: "Completed",
  planning: "Plan to Watch",
  paused: "Paused",
  dropped: "Dropped",
};

export function initializeBrowse(root: HTMLElement, signal: AbortSignal) {
  root.dataset.enhanced = "";
  root.dataset.browseScripted = "";
  // Start slots only after the native controls have finished their layout handoff.
  // A repeated page-load event must not restart an already running entrance.
  const animateInitial = !root.hasAttribute("data-browse-arrivals-ready");
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (signal.aborted || !root.isConnected) return;
    root.setAttribute("data-browse-arrivals-ready", "");
    root.removeAttribute("data-browse-arrivals-pending");
    initializeScrollMotion(root, signal, '.browse-card-body, .browse-skeleton-body', 'browse-scroll-reveal', {
      animateInitial, downwardOnly: true, scaleEntrance: true, once: true,
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
  const end = find<HTMLElement>("[data-browse-end]");
  const sort = find<HTMLSelectElement>("[data-browse-sort]");
  const syncSort = initializeBrowseSort(root, signal, changed);
  const chips = find<HTMLElement>("[data-active-filters]");
  const template = find<HTMLTemplateElement>("[data-browse-template]");
  const initial = JSON.parse(
    find<HTMLElement>("#browse-data").textContent || "{}",
  );
  let payload: Payload = initial.payload;
  let displayedCategories = !sections.hidden;
  let facets: { genres: Facet[]; providers: Facet[] } = initial.facets;
  const responseCache = createBrowseCache<Payload | SectionsPayload>();
  const facetCache = createBrowseCache<typeof facets>();
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
  const key = (item: Item) => `${item.type}:${item.tmdb_id || item.id}`;
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
      "min_votes",
    ]) {
      const value = field(name).value.trim();
      if (value) values.set(name, value);
    }
    values.set("media_type", field("type").value);
    values.set("sort", sort.value);
    return values;
  };
  const requestPath = (values: URLSearchParams) => isCategoryView(values)
    ? `browse/sections?${new URLSearchParams({media_type: values.get('media_type')!, region: values.get('region')!, min_votes: values.get('min_votes')!})}`
    : `browse?${browseRequestParams(values)}`;
  const initialValues = params();
  initialValues.set('page', '1');
  if (error.hidden) responseCache.set(requestPath(initialValues), displayedCategories
    ? {sections: initial.sections, notice: payload.notice} : payload);
  facetCache.set(`${field('type').value}:${field('region').value}`, facets);
  const updateUrl = () => {
    const values = params();
    values.delete("media_type");
    if (field("type").value === "series") values.set("type", "series");
    if (values.get("region") === "US") values.delete("region");
    if (values.get("sort") === "all") values.delete("sort");
    if (values.get("min_votes") === "250") values.delete("min_votes");
    history.replaceState(
      history.state,
      "",
      `${location.pathname}${values.size ? `?${values}` : ""}`,
    );
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
  const paintState = (card: HTMLElement, item: Item) => {
    card.querySelectorAll<HTMLElement>('[data-editor-icon]').forEach(icon => {
      icon.hidden = icon.dataset.editorIcon === 'edit' ? !item.list_status : !!item.list_status;
    });
    const watch = card.querySelector<HTMLButtonElement>('[data-browse-action=watch]');
    if (watch) watch.hidden = item.list_status === 'watching';
    card.dataset.state = item.list_status || "";
    const rating = card.querySelector<HTMLElement>("[data-user-score]")!;
    rating.hidden = !(item.score != null && item.score > 0);
    const score = Number(item.score || 0).toFixed(1);
    rating.setAttribute("aria-label", `Your rating: ${score} out of 10`);
    rating.querySelector("[data-score]")!.textContent = score;
    const state = card.querySelector<HTMLElement>("[data-list-state]")!;
    state.hidden = !item.list_status;
    state.title = statusLabels[item.list_status || ""] || "";
    state.setAttribute("aria-label", state.title);
    state
      .querySelectorAll<HTMLElement>("[data-state-icon]")
      .forEach(
        (icon) => (icon.hidden = icon.dataset.stateIcon !== item.list_status),
      );
    const plan = card.querySelector<HTMLButtonElement>(
      "[data-browse-action=plan]",
    );
    const rate = card.querySelector<HTMLButtonElement>(
      "[data-browse-action=rate]",
    );
    if (plan) plan.hidden = !!item.list_status;
    if (rate) rate.hidden = item.score != null && item.score > 0;
  };
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
      score: updated.score, rating_mode: updated.rating_mode};
    for (const cached of responseCache.values()) {
      const cachedItems = 'sections' in cached ? cached.sections.flatMap(section => section.results) : cached.results;
      for (const item of cachedItems) if (key(item) === key(updated)) Object.assign(item, entryState);
    }
    for (const [card, item] of items) {
      if (key(item) !== key(updated)) continue;
      Object.assign(item, updated);
      card.querySelectorAll<HTMLAnchorElement>("a").forEach((link) => {
        if (item.id) link.href = `/title/${item.id}`;
      });
      paintState(card, item);
    }
  };
  const render = (results: Item[], destination = grid, deduplicate = true) => {
    const fragment = document.createDocumentFragment();
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
          route: "direct",
        });
      prepareBrowsePoster(img, signal, readyPosters);
      const placeholder = card.querySelector<HTMLElement>(
        ".browse-placeholder",
      )!;
      placeholder.hidden = !!item.poster;
      placeholder.textContent = item.title.slice(0, 1);
      card.querySelector("[data-year]")!.textContent = item.year || "";
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
      if ('entry' in item) editorStore.seed(item as EditorTitle);
      fragment.append(card);
    }
    destination.append(fragment);
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
    for (const name of ["start", "end", "status", "provider", "min_votes"])
      if (field(name).value && (name !== "min_votes" || field(name).value !== "250")) {
        const label =
          name === "start"
            ? `After ${field(name).value}`
            : name === "end"
              ? `Before ${field(name).value}`
              : name === "min_votes" ? `${Number(field(name).value).toLocaleString()}+ votes`
              : (field(name) as HTMLSelectElement).selectedOptions[0]
                  ?.textContent || field(name).value;
        add(label, () => {
          field(name).value = name === "min_votes" ? "250" : "";
          field(name).dispatchEvent(new Event("input", { bubbles: true }));
        });
      }
    if (field("q").value.trim())
      add(`“${field("q").value.trim()}”`, () => {
        field("q").value = "";
      });
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
    find("[data-browse-sentinel]").hidden = displayedCategories;
    more.hidden = !payload.has_more;
    more.disabled = loading;
    end.hidden = payload.has_more || !grid.childElementCount;
    empty.hidden =
      displayedCategories ||
      loading ||
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
    const values = params();
    const categoryView = isCategoryView(values);
    values.set("page", String(append ? payload.page + 1 : 1));
    if (append) values.set("source", payload.source);
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
    empty.hidden = true;
    region.setAttribute("aria-busy", "true");
    updateFooter();
    updateUrl();
    paintChips();
    if (cached) loadingView.finish();
    else if (!append) {
      cancelUiMotion(region);
      grid.hidden = true;
      sections.hidden = true;
      notice.textContent = "";
      loadingView.show(false, categoryView);
    } else {
      loadingView.show(true);
    }
    try {
      let response = cached;
      if (!response) {
        await waitForBrowseRequest(debounce, current.signal);
        response = await json(path, {signal: current.signal}) as Payload | SectionsPayload;
        if (current.signal.aborted || signal.aborted || request !== current) return;
        responseCache.set(path, response);
        await loadingView.settle(current.signal);
      }
      if (request !== current || epoch !== generation || signal.aborted) return;
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
        const existing = grid.querySelectorAll('.browse-card-body').length;
        render(response.results);
        loadingView.handoff([...grid.querySelectorAll<HTMLElement>('.browse-card-body')].slice(existing));
        payload = response;
      }
      notice.textContent = response.notice || "";
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
    searchClear.hidden = !field("q").value;
    // Update immediately; only network requests from typing/dragging are debounced.
    const debounce = event?.type === 'input' ? 220 : 0;
    void load(false, debounce);
  }
  function clear() {
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
    field("min_votes").value = "250";
    syncGenres();
    changed();
  }
  async function loadFacets() {
    facetRequest?.abort();
    const current = new AbortController();
    facetRequest = current;
    const cacheKey = `${field('type').value}:${field('region').value}`;
    try {
      const result = facetCache.get(cacheKey) ?? await json(
        `browse/facets?${new URLSearchParams({ media_type: field("type").value, region: field("region").value })}`,
        { signal: current.signal },
      );
      if (current !== facetRequest || current.signal.aborted || signal.aborted) return;
      facetCache.set(cacheKey, result);
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
        !["status", "provider", "region", "start", "end", "min_votes"].includes(target.name)
      )
        return;
      if (target.name === "region") void loadFacets();
      changed(event);
    },
    { signal },
  );
  let touchPoster = false;
  region.addEventListener(
    "pointerdown",
    (event) => {
      touchPoster = event.pointerType === "touch";
    },
    { signal },
  );
  region.addEventListener(
    "click",
    (event) => {
      if (!touchPoster) return;
      const poster = (event.target as Element).closest<HTMLAnchorElement>(
        ".browse-poster-link",
      );
      const card = poster?.closest<HTMLElement>("[data-browse-card]");
      if (
        !card?.querySelector(".browse-actions") ||
        card.classList.contains("is-touch-active")
      )
        return;
      event.preventDefault();
      root
        .querySelectorAll(".is-touch-active")
        .forEach((other) => other.classList.remove("is-touch-active"));
      card.classList.add("is-touch-active");
    },
    { signal },
  );
  document.addEventListener(
    "pointerdown",
    (event) => {
      for (const card of root.querySelectorAll<HTMLElement>(
        ".is-touch-active",
      )) {
        if (card.contains(event.target as Node)) continue;
        card.classList.remove("is-touch-active");
        if (card.contains(document.activeElement))
          (document.activeElement as HTMLElement)?.blur();
      }
    },
    { signal },
  );
  sort.addEventListener("change", changed, { signal });
  root
    .querySelectorAll<HTMLAnchorElement>("[data-browse-type]")
    .forEach((link) =>
      link.addEventListener(
        "click",
        (event) => {
          event.preventDefault();
          if (field("type").value === link.dataset.browseType) return;
          field("type").value = link.dataset.browseType!;
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
      if (!advancedFilters.contains(event.target as Node) &&
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
    paginationDemand.advance(scrollY, canAutoLoad());
    if (!paginationFrame) paginationFrame = requestAnimationFrame(() => {
      paginationFrame = 0;
      autoLoadMore();
    });
  }, { passive: true, signal });
  const editorTitle = (item: Item) => editorStore.get({...item, entry:item.entry ?? null});
  const warmEditor = (event: Event) => {
    const card = (event.target as Element).closest<HTMLElement>('[data-browse-card]');
    const item = card && items.get(card);
    if (!item || !card?.querySelector('[data-browse-action]')) return;
    const image = card.querySelector<HTMLImageElement>('[data-poster]');
    editorStore.warmArtwork(editorTitle(item), image?.currentSrc || image?.src);
  };
  region.addEventListener('pointerover', warmEditor, {signal});
  region.addEventListener('focusin', warmEditor, {signal});
  document.addEventListener('anylist:editor-state', event => {
    const title = (event as CustomEvent<EditorTitle>).detail;
    let updated: Item = {...title, tmdb_id:title.tmdb_id ?? null,
      poster:title.poster ?? undefined, backdrop:title.backdrop ?? undefined, list_status:title.entry?.status,
      score:title.entry?.score, rating_mode:title.entry?.rating_mode};
    for (const item of items.values()) {
      if (!(title.id && item.id === title.id) && !(title.tmdb_id && item.tmdb_id === title.tmdb_id && item.type === title.type)) continue;
      updated = {...item, id:title.id, entry:title.entry, list_status:title.entry?.status,
        score:title.entry?.score, rating_mode:title.entry?.rating_mode};
      break;
    }
    syncItem(updated);
  }, {signal});
  // Read snapshots immediately. Import and save only after an explicit write.
  region.addEventListener(
    "click",
    async (event) => {
      const button = (event.target as Element).closest<HTMLButtonElement>(
        "[data-browse-action]",
      );
      const card = button?.closest<HTMLElement>("[data-browse-card]");
      if (!button || !card) return;
      const item = items.get(card);
      if (!item || button.disabled) return;
      const title = editorTitle(item);
      try {
        if (button.dataset.browseAction === "edit")
          document.dispatchEvent(
            new CustomEvent("anylist:open-editor", {
              detail: { mediaId: item.id, opener: button, title },
            }),
          );
        else if (button.dataset.browseAction === "plan" || button.dataset.browseAction === "watch") {
          const saved = await editorStore.write(title, {status:button.dataset.browseAction === 'watch' ? 'watching' : 'planning'});
          if (signal.aborted) return;
          document.dispatchEvent(new CustomEvent('anylist:entry-saved', {detail:saved}));
        } else {
          // The existing picker handles season-average override confirmation.
          const entry = title.entry;
          (window as any).anyListQuickRate?.({
            title: item.title,
            poster: item.poster,
            posterSrc: card.querySelector<HTMLImageElement>('[data-poster]')?.currentSrc,
            score: entry?.score,
            ratingMode: entry?.rating_mode || "manual",
            onScore: async (score: number | null) => {
              const saved = await editorStore.write(title, {
                  manual_score: score ?? 0,
                  ...(score != null
                    ? { status: "completed", rating_mode: "manual" }
                    : {}),
              });
              if (signal.aborted) return;
              document.dispatchEvent(new CustomEvent('anylist:entry-saved', {detail:saved}));
            },
          });
        }
      } catch (cause) {
        notice.textContent = (cause as Error).message;
      }
    },
    { signal },
  );
  paintChips();
  updateFooter();
  if (error.hidden === false) suspended = true;
  if (
    field("start").value ||
    field("end").value ||
    field("region").value !== "US"
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
