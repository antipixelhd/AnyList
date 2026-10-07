import {
  isCategoryView,
  browseRequestParams,
  sectionHref,
} from "./browse-view";
import { applyResponsiveArtwork } from "./responsive-artwork";
import { initializeScrollMotion } from "./scroll-motion";
import { cancelUiMotion, reveal, showMenu, hideMenu } from "./ui-motion";

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
  const find = <T extends HTMLElement>(selector: string) =>
    root.querySelector<T>(selector)!;
  const form = find<HTMLFormElement>("[data-browse-form]");
  const field = (name: string) =>
    form.elements.namedItem(name) as HTMLInputElement | HTMLSelectElement;
  const grid = find<HTMLElement>("[data-browse-results]");
  const sections = find<HTMLElement>("[data-browse-sections]");
  const filterToggle = find<HTMLButtonElement>("[data-filter-toggle]");
  const skeleton = find<HTMLElement>("[data-browse-skeleton]");
  const region = find<HTMLElement>("[data-browse-region]");
  const empty = find<HTMLElement>("[data-browse-empty]");
  const notice = find<HTMLElement>("[data-browse-notice]");
  const error = find<HTMLElement>("[data-browse-error]");
  const more = find<HTMLButtonElement>("[data-browse-more]");
  const end = find<HTMLElement>("[data-browse-end]");
  const sort = find<HTMLSelectElement>("[data-browse-sort]");
  const sortNames = new Map(
    [...sort.options].map((option) => [option.value, option.textContent]),
  );
  const chips = find<HTMLElement>("[data-active-filters]");
  const template = find<HTMLTemplateElement>("[data-browse-template]");
  const tagSearch = find<HTMLInputElement>("[data-tag-search]");
  const tagResults = find<HTMLElement>("[data-tag-results]");
  const tagStatus = find<HTMLElement>("[data-tag-status]");
  const initial = JSON.parse(
    find<HTMLElement>("#browse-data").textContent || "{}",
  );
  let payload: Payload = initial.payload;
  let displayedCategories = !sections.hidden;
  let facets: { genres: Facet[]; providers: Facet[] } = initial.facets;
  const items = new Map<HTMLElement, Item>();
  const seen = new Set<string>();
  const tags = new Map<number, string>(
    (field("tags").value || "")
      .split(",")
      .filter(Boolean)
      .map((id) => [Number(id), `Tag ${id}`]),
  );
  let request: AbortController | undefined,
    facetRequest: AbortController | undefined,
    tagRequest: AbortController | undefined;
  let timer: number | undefined, tagTimer: number | undefined;
  let loading = false,
    suspended = false;
  let generation = 0;
  const key = (item: Item) => `${item.type}:${item.tmdb_id || item.id}`;
  const params = () => {
    const values = new URLSearchParams();
    for (const name of [
      "q",
      "genres",
      "tags",
      "start",
      "end",
      "status",
      "provider",
      "region",
    ]) {
      const value = field(name).value.trim();
      if (value) values.set(name, value);
    }
    values.set("media_type", field("type").value);
    values.set("sort", sort.value);
    return values;
  };
  const updateUrl = () => {
    const values = params();
    values.delete("media_type");
    if (field("type").value === "series") values.set("type", "series");
    if (values.get("region") === "US") values.delete("region");
    if (values.get("sort") === "all") values.delete("sort");
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
    const item = initialItems.get(card.dataset.key!);
    if (!item) return;
    items.set(card, item);
    if (grid.contains(card)) seen.add(key(item));
    paintState(card, item);
  });
  const syncItem = (updated: Item) => {
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
                : "Rate and complete";
          button.setAttribute("aria-label", `${label} ${item.title}`);
        });
      paintState(card, item);
      items.set(card, item);
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
      heading.textContent = section.title;
      link.textContent = "View all";
      link.dataset.viewAll = "";
      link.href = sectionHref(
        field("type").value,
        field("region").value,
        section.filters,
      );
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
    for (const [id, name] of tags)
      add(name, () => {
        tags.delete(id);
        syncTags();
        renderTags();
      });
    for (const name of ["start", "end", "status", "provider"])
      if (field(name).value) {
        const label =
          name === "start"
            ? `After ${field(name).value}`
            : name === "end"
              ? `Before ${field(name).value}`
              : (field(name) as HTMLSelectElement).selectedOptions[0]
                  ?.textContent || field(name).value;
        add(label, () => {
          field(name).value = "";
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
    sort.disabled = !!field("q").value.trim();
    for (const option of sort.options)
      option.textContent =
        sort.disabled && option.selected
          ? "Relevance"
          : sortNames.get(option.value) || option.value;
    sort.dispatchEvent(new Event("input", { bubbles: true }));
  };
  const syncGenres = () => {
    field("genres").value = [
      ...form.querySelectorAll<HTMLInputElement>("[name=genre]:checked"),
    ]
      .map((input) => input.value)
      .join(",");
    find("[id=browse-genres-value]").textContent = field("genres").value
      ? `${field("genres").value.split(",").length} selected`
      : "All genres";
  };
  const syncTags = () => {
    field("tags").value = [...tags.keys()].join(",");
    find("[id=browse-tags-value]").textContent = tags.size
      ? `${tags.size} selected`
      : "Any tag";
  };
  let availableTags: Facet[] = [];
  const renderTags = () => {
    tagResults.replaceChildren();
    const options = new Map<number, string>([
      ...tags,
      ...availableTags.map((tag) => [tag.id, tag.name] as [number, string]),
    ]);
    for (const [id, name] of options) {
      const label = document.createElement("label"),
        input = document.createElement("input"),
        text = document.createElement("span");
      input.type = "checkbox";
      input.checked = tags.has(id);
      input.value = String(id);
      text.textContent = name;
      input.addEventListener("change", () => {
        if (input.checked && tags.size >= 12) {
          input.checked = false;
          tagStatus.textContent = "Choose up to 12 tags.";
          return;
        }
        if (input.checked) tags.set(id, name);
        else tags.delete(id);
        syncTags();
        changed();
      });
      label.append(input, text);
      tagResults.append(label);
    }
    if (!options.size) {
      const p = document.createElement("p");
      p.textContent =
        tagSearch.value.trim().length >= 2
          ? "No matching tags"
          : "Type to find tags";
      tagResults.append(p);
    }
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
  async function load(append = false) {
    if (
      signal.aborted ||
      (append &&
        (displayedCategories || loading || !payload.has_more || suspended))
    )
      return;
    if (!form.reportValidity()) return;
    request?.abort();
    const current = new AbortController();
    request = current;
    const epoch = ++generation;
    loading = true;
    suspended = false;
    error.hidden = true;
    const values = params();
    const categoryView = isCategoryView(values);
    values.set("page", String(append ? payload.page + 1 : 1));
    if (append) values.set("source", payload.source);
    if (!append) {
      grid.style.opacity = ".45";
      sections.style.opacity = ".45";
    }
    empty.hidden = true;
    region.setAttribute("aria-busy", "true");
    updateFooter();
    updateUrl();
    paintChips();
    const loadingTimer = window.setTimeout(() => {
      if (request === current) {
        skeleton.hidden = false;
        if (!append) {
          cancelUiMotion(region);
          grid.hidden = true;
          sections.hidden = true;
          skeleton.style.marginTop = "0";
        }
      }
    }, 120);
    try {
      const path = categoryView
        ? `browse/sections?${new URLSearchParams({ media_type: field("type").value, region: field("region").value })}`
        : `browse?${browseRequestParams(values)}`;
      const response: Payload | SectionsPayload = await json(path, {
        signal: current.signal,
      });
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
        payload = { results: [], has_more: false, page: 1, source: "" };
      } else {
        render(response.results);
        payload = response;
      }
      notice.textContent = response.notice || "";
      // Filtered search can have empty intermediate pages. Continue through them.
      observer.unobserve(sentinel);
      requestAnimationFrame(() => {
        if (!signal.aborted) observer.observe(sentinel);
      });
    } catch (cause) {
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
      window.clearTimeout(loadingTimer);
      if (request === current) {
        loading = false;
        skeleton.hidden = true;
        skeleton.style.marginTop = "";
        grid.hidden = displayedCategories;
        sections.hidden = !displayedCategories;
        grid.style.opacity = "";
        sections.style.opacity = "";
        region.setAttribute("aria-busy", "false");
        updateFooter();
      }
    }
  }
  function changed() {
    window.clearTimeout(timer);
    request?.abort();
    generation++;
    loading = false;
    suspended = true;
    updateUrl();
    paintChips();
    // Invalidate at the input event, before the debounce, so an older response cannot repaint.
    timer = window.setTimeout(
      () => void load(),
      field("q") === document.activeElement ? 220 : 80,
    );
  }
  function clear() {
    for (const name of [
      "q",
      "genres",
      "tags",
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
    tags.clear();
    syncGenres();
    syncTags();
    renderTags();
    changed();
  }
  async function loadFacets() {
    facetRequest?.abort();
    const current = new AbortController();
    facetRequest = current;
    try {
      const result = await json(
        `browse/facets?${new URLSearchParams({ media_type: field("type").value, region: field("region").value })}`,
        { signal: current.signal },
      );
      if (current !== facetRequest || signal.aborted) return;
      facets = result;
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
      provider.dispatchEvent(new Event("change", { bubbles: true }));
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
      window.clearTimeout(timer);
      void load();
    },
    { signal },
  );
  form.addEventListener(
    "input",
    (event) => {
      if (event.target === field("q")) changed();
    },
    { signal },
  );
  form.addEventListener(
    "change",
    (event) => {
      const target = event.target as HTMLInputElement;
      if (target.name === "genre") {
        if (form.querySelectorAll("[name=genre]:checked").length > 12) {
          target.checked = false;
          notice.textContent = "Choose up to 12 genres.";
          return;
        }
        syncGenres();
      } else if (
        !["status", "provider", "region", "start", "end"].includes(target.name)
      )
        return;
      if (target.name === "region") void loadFacets();
      changed();
    },
    { signal },
  );
  filterToggle.addEventListener(
    "click",
    () => {
      const open = filterToggle.getAttribute("aria-expanded") !== "true";
      filterToggle.setAttribute("aria-expanded", String(open));
      root.classList.toggle("filters-open", open);
      if (!open) closeMenus();
    },
    { signal },
  );
  sections.addEventListener(
    "click",
    (event) => {
      const link = (event.target as Element).closest<HTMLAnchorElement>(
        "[data-view-all]",
      );
      if (
        !link ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      event.preventDefault();
      const values = new URL(link.href).searchParams;
      for (const name of [
        "q",
        "genres",
        "tags",
        "start",
        "end",
        "status",
        "provider",
      ])
        field(name).value = values.get(name) || "";
      sort.value = values.get("sort") || "popular";
      tags.clear();
      syncGenresAfterType();
      syncTags();
      renderTags();
      for (const name of ["sort", "status", "provider"])
        field(name).dispatchEvent(new Event("input", { bubbles: true }));
      changed();
      window.scrollTo({
        top: root.offsetTop,
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "instant"
          : "smooth",
      });
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
          field("status").dispatchEvent(new Event("change", { bubbles: true }));
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
    },
    { signal },
  );
  form.addEventListener(
    "keydown",
    (event) => {
      if (event.key === "Escape") {
        const menu = (event.target as Element).closest<HTMLDetailsElement>(
          "[data-filter-menu]",
        );
        if (menu?.open) {
          void closeFilter(menu, true);
        }
      }
    },
    { signal },
  );
  tagSearch.addEventListener(
    "input",
    () => {
      window.clearTimeout(tagTimer);
      tagRequest?.abort();
      tagStatus.textContent = "";
      tagTimer = window.setTimeout(async () => {
        const term = tagSearch.value.trim();
        if (term.length < 2) {
          availableTags = [];
          renderTags();
          return;
        }
        const current = new AbortController();
        tagRequest = current;
        tagStatus.textContent = "Loading tags…";
        try {
          const result = await json(
            `browse/tags?q=${encodeURIComponent(term)}`,
            { signal: current.signal },
          );
          if (current !== tagRequest || signal.aborted) return;
          availableTags = result.results;
          renderTags();
          tagStatus.textContent = "";
        } catch (cause) {
          if (!current.signal.aborted && !signal.aborted)
            tagStatus.textContent = (cause as Error).message;
        }
      }, 200);
    },
    { signal },
  );
  if (tags.size)
    void json(`browse/tags?ids=${[...tags.keys()].join(",")}`, { signal })
      .then((result) => {
        for (const tag of result.results)
          if (tags.has(tag.id)) tags.set(tag.id, tag.name);
        paintChips();
        renderTags();
      })
      .catch(() => {});
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
  const observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) void load(true);
    },
    { rootMargin: "500px 0px" },
  );
  observer.observe(sentinel);
  initializeScrollMotion(
    region,
    signal,
    ".browse-card",
    "browse-scroll-reveal",
  );
  // Imports only happen on an explicit poster action; browsing never writes media.
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
      const buttons = [...card.querySelectorAll<HTMLButtonElement>("button")];
      buttons.forEach((b) => (b.disabled = true));
      try {
        if (!item.id) {
          const imported = await json(`catalog/${item.type}/${item.tmdb_id}`, {
            method: "POST",
          });
          item.id = imported.id;
          syncItem(item);
          card
            .querySelectorAll<HTMLAnchorElement>("a")
            .forEach((a) => (a.href = `/title/${item.id}`));
        }
        if (signal.aborted) return;
        if (button.dataset.browseAction === "edit")
          document.dispatchEvent(
            new CustomEvent("anylist:open-editor", {
              detail: { mediaId: item.id, opener: button },
            }),
          );
        else if (button.dataset.browseAction === "plan") {
          const saved = await json(`entry/${item.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: "planning" }),
          });
          item.list_status = saved.status;
          item.score = saved.score;
          syncItem(item);
          notice.textContent = `${item.title} added to Plan to Watch.`;
          reveal(card.querySelector<HTMLElement>("[data-list-state]")!);
        } else {
          // The existing picker handles season-average override confirmation.
          const title = await json(`title/${item.id}`);
          const entry = title.entry;
          (window as any).anyListQuickRate?.({
            title: item.title,
            poster: item.poster,
            help: "Choose a score to complete this title and add it to your list.",
            score: entry?.score,
            ratingMode: entry?.rating_mode || "manual",
            onScore: async (score: number | null) => {
              const saved = await json(`entry/${item.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  manual_score: score ?? 0,
                  ...(score != null
                    ? { status: "completed", rating_mode: "manual" }
                    : {}),
                }),
              });
              item.list_status = saved.status;
              item.score = saved.score;
              item.rating_mode = saved.rating_mode;
              syncItem(item);
              notice.textContent =
                score != null
                  ? `${item.title} rated and completed.`
                  : `Rating removed for ${item.title}.`;
            },
          });
        }
      } catch (cause) {
        notice.textContent = (cause as Error).message;
      } finally {
        buttons.forEach((b) => (b.disabled = false));
      }
    },
    { signal },
  );
  document.addEventListener(
    "anylist:entry-saved",
    (event) => {
      const saved = (event as CustomEvent).detail;
      for (const [card, item] of items)
        if (item.id === saved.id) {
          item.list_status = saved.status;
          item.score = saved.score;
          item.rating_mode = saved.rating_mode;
          paintState(card, item);
        }
    },
    { signal },
  );
  paintChips();
  renderTags();
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
      request?.abort();
      facetRequest?.abort();
      tagRequest?.abort();
      observer.disconnect();
      window.clearTimeout(timer);
      window.clearTimeout(tagTimer);
    },
    { once: true },
  );
}
