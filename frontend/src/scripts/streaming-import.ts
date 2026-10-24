import { showDialog, closeDialog } from '../lib/ui-motion';
import { showMessage } from '../lib/settings-feedback';
import { setupCalendarDateInputs } from '../lib/calendar-date-input';
import { createImportDraft } from '../lib/import-draft';
import { importDecisionStep } from '../lib/import-steps';
import { readRemapTitles } from '../lib/streaming-remap-search';
import { formatDate } from '../lib/date-time';
import { needsImportFinishDate, importFinishDateSuggestion } from '../lib/import-completion';

type MatchCandidate = {
  media_type: "movie" | "show";
  tmdb_id: number;
  title: string;
  year?: number | string | null;
  poster_path?: string | null;
};

type EpisodeResolution = "exact" | "guessed" | "covered" | "discarded";
type StreamingEpisode = {
  source_title?: string | null;
  source_episode_title?: string | null;
  title?: string | null;
  dates?: string[];
  season_number?: number | null;
  episode_number?: number | null;
  resolution?: EpisodeResolution;
};
type StreamingSeason = { season_number: number; represented: number; total_released: number | null };
type StreamingItem = {
  id: string | number;
  kind: "movie" | "show";
  is_anime?: boolean;
  requires_watch_confirmation?: boolean;
  progress_reviewed?: boolean;
  suggested_movie_status?: "completed" | "dropped";
  completion_evidence?: {finish_date: string | null; suggested_date: string | null};
  source_title: string;
  source_dates?: string[];
  source_rows?: number;
  match: {
    state: "matched" | "review" | "unmatched";
    confidence: "high" | "medium" | "low";
    reason?: string | null;
    candidate: MatchCandidate | null;
    candidates?: MatchCandidate[];
  };
  episodes?: StreamingEpisode[];
  seasons?: StreamingSeason[];
  existing?: { catalog?: boolean; tracked?: boolean; rated?: boolean; status?: string | null; progress?: number };
  decision: { action: "confirm" | "remap" | "skip" | null };
  outcome: {
    status: "completed" | "partial" | "skip" | "dropped";
    finish_date?: string | null;
    latest_season?: number | null;
    latest_episode?: number | null;
    tracking_status?: "watching" | "paused" | "dropped";
    manual_score?: number | null;
  };
};

type StreamingSession = {
  id: string;
  status: "preparing" | "review" | "ready" | "committed" | "cancelled" | "failed" | string;
  phase: string;
  progress: { current: number; total: number; message: string };
  revision: number;
  items: StreamingItem[];
  summary: Record<string, unknown> | null;
  errors?: (string | { message?: string; row?: number })[];
};

type SearchResult = MatchCandidate & { id?: number | null };

function mountStreamingImport(app: HTMLElement) {
  if (!app || app.dataset.streamingInitialized === "true") return;
  app.dataset.streamingInitialized = "true";
  const disclosureArrow = app.querySelector<HTMLTemplateElement>('[data-role="streaming-disclosure-arrow"]')!.innerHTML;
  const checkIcon = app.querySelector<HTMLTemplateElement>('[data-role="streaming-check"]')!.innerHTML;
  const upload = app.querySelector<HTMLElement>('[data-role="streaming-import-upload"]') as HTMLDivElement;
  const uploadControls = app.querySelector<HTMLElement>('[data-role="streaming-import-upload-controls"]') as HTMLElement;
  const fileInput = app.querySelector<HTMLElement>('[data-role="streaming-import-file"]') as HTMLInputElement;
  const languageInput = app.querySelector<HTMLElement>('[data-role="streaming-import-language"]') as HTMLSelectElement;
  const skipAnimeInput = app.querySelector<HTMLElement>('[data-role="streaming-skip-anime"]') as HTMLInputElement;
  const workflow = app.querySelector<HTMLElement>('[data-role="streaming-import-workflow"]') as HTMLElement;
  const remapDialog = app.querySelector<HTMLElement>('[data-role="streaming-remap-dialog"]') as HTMLDialogElement;
  const remapInput = app.querySelector<HTMLElement>('[data-role="streaming-remap-input"]') as HTMLInputElement;
  const remapState = app.querySelector<HTMLElement>('[data-role="streaming-remap-state"]') as HTMLElement;
  const remapResults = app.querySelector<HTMLElement>('[data-role="streaming-remap-results"]') as HTMLElement;
  const remapList = app.querySelector<HTMLElement>('[data-role="streaming-remap-list"]') as HTMLElement;
  const remapProgress = app.querySelector<HTMLElement>('[data-role="streaming-remap-progress"]') as HTMLElement;
  const service = app.dataset.service!;
  const serviceName = app.dataset.serviceName!;
  const extension = app.dataset.extension!;
  const token = document.getElementById("app-data")?.getAttribute("data-token") ?? "";
  const hasTmdbKey = app.getAttribute("data-has-tmdb-key") === "true";

  let session: StreamingSession | null = null;
  let step = 1;
  let pollTimer: number | undefined;
  let saving = false;
  let showingCompleted = false;
  const openRatingGroups = new Set<string>();
  let remappingItemId: string | number | null = null;
  let searchController: AbortController | null = null;
  let searchTimer = 0;
  const draft = createImportDraft<StreamingSession>({
    getSession: () => session,
    setSession: updated => { session = updated; },
    apply: (current, {itemId, changes}) => {
      const target = current.items.find(value => String(value.id) === String(itemId));
      if (!target) return;
      if (changes.action != null) {
        target.decision.action = changes.action as StreamingItem['decision']['action'];
        if (changes.action === 'confirm' && target.kind === 'movie') target.outcome.status = target.suggested_movie_status || 'completed';
      }
      const {action, media_type, tmdb_id, ...outcome} = changes;
      Object.assign(target.outcome, outcome);
      if (target.outcome.status === 'dropped') target.outcome.tracking_status = 'dropped';
      if (changes.status != null) target.progress_reviewed = true;
    },
    save: (current, {itemId, changes}) => request<StreamingSession>(`/imports/${encodeURIComponent(current.id)}/items/${encodeURIComponent(String(itemId))}`, {
      method: 'PATCH', body: JSON.stringify({revision: current.revision, ...changes}),
    }),
  });
  let idempotencyKey = "";
  let busy = false;
  let uploadController: AbortController | null = null;
  const esc = (value: unknown) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]!);

  function posterSrc(path?: string | null): string {
    if (!path) return "";
    if(path.startsWith('/api/proxy/media/rating-poster/'))return path;
    const legacy=/^\/api\/proxy\/media\/image\/(w\d+|original)(\/[^?#]+)$/.exec(path);
    if(legacy)return `https://image.tmdb.org/t/p/${legacy[1]}${legacy[2]}`;
    if(path.startsWith('/api/proxy/media/image/tvdb/'))return `https://artworks.thetvdb.com/${path.slice('/api/proxy/media/image/tvdb/'.length)}`;
    if (path.startsWith("/")) return `https://image.tmdb.org/t/p/w342/${path.replace(/^\/+/, "")}`;
    try {
      const url = new URL(path);
      if (url.protocol === "https:" && (url.hostname === "image.tmdb.org" || url.hostname === "artworks.thetvdb.com")) return path;
    } catch { /* Ignore invalid poster values. */ }
    return "";
  }

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await fetch(`/api/proxy${path}`, { ...init, headers });
    if (!response.ok) {
      let detail = `Request failed (${response.status})`;
      try {
        const payload = await response.json();
        detail = payload.detail || payload.message || detail;
      } catch { /* Keep the HTTP status message. */ }
      throw new Error(detail);
    }
    return response.status === 204 ? undefined as T : response.json();
  }

  function showError(message: string) {
    if (remapDialog.open) {
      remapState.textContent = message;
      remapState.hidden = false;
      return;
    }
    showMessage(message, 'error');
  }

  function clearError() {
    remapState.textContent = "";
    remapState.hidden = true;
  }

  function summaryNumber(...keys: string[]): number {
    const summary = session?.summary ?? {};
    for (const key of keys) {
      const value = summary[key];
      if (typeof value === "number") return value;
    }
    return 0;
  }

  function fileHistory(item: StreamingItem): string {
    const episodes = item.episodes ?? [];
    if (!episodes.length) return '';
    return `<details data-motion-details class="streaming-disclosure streaming-history"><summary class="streaming-disclosure-trigger">View file history${disclosureArrow}</summary><ul>${episodes.map(episode => {
      const position = episode.resolution === 'discarded' ? 'Not included'
        : episode.resolution === 'covered' ? 'Included in your progress'
        : episode.season_number != null && episode.episode_number != null
        ? `Season ${episode.season_number} · Episode ${episode.episode_number}` : 'Episode not identified';
      const dates = [...new Set(episode.dates ?? [])].sort().map(date => formatDate(date));
      return `<li><span>${esc(episode.source_episode_title || episode.source_title || episode.title || position)}</span><span>${esc([position, ...dates].join(' · '))}</span></li>`;
    }).join('')}</ul></details>`;
  }

  function heading(title: string, description = '', remaining?: number): string {
    return `<header class="streaming-heading"><div><div class="streaming-heading-top"><p class="streaming-step">Step ${step} of 5</p>${step > 1 ? '<button type="button" data-streaming-action="cancel" class="streaming-text-action streaming-muted streaming-mobile-cancel">Cancel</button>' : ''}</div><h2>${title}</h2>${description ? `<p class="streaming-description">${description}</p>` : ''}</div><div class="streaming-step-progress">${remaining != null ? `<span role="status">${remaining ? `${remaining} title${remaining === 1 ? '' : 's'} left` : 'All matches checked'}</span>` : ''}<div class="streaming-step-bars" role="img" aria-label="Step ${step} of 5">${Array.from({length: 5}, (_, index) => `<span class="${index < step ? 'is-reached' : ''}"></span>`).join('')}</div></div></header>`;
  }

  function artwork(path?: string | null): string {
    const poster = posterSrc(path);
    return poster ? `<img class="streaming-artwork" loading="lazy" src="${esc(poster)}" alt="">`
      : `<span class="streaming-artwork streaming-artwork-empty" aria-hidden="true">${app.querySelector<HTMLTemplateElement>('[data-role="streaming-poster-placeholder"]')!.innerHTML}</span>`;
  }

  function isIncomplete(item: StreamingItem): boolean {
    if (item.kind === "movie") return true;
    if (item.requires_watch_confirmation && !item.progress_reviewed) return true;
    const seasons = (item.seasons ?? []).filter((season) => season.season_number > 0 && (season.total_released ?? 0) > 0);
    const unknownSeasons = (item.seasons ?? []).filter((season) => season.season_number > 0 && season.total_released == null);
    if (unknownSeasons.length) return true;
    if (seasons.length) return seasons.some((season) => season.represented < (season.total_released ?? 0));
    return true;
  }

  function visibleItems(): StreamingItem[] {
    return (session?.items ?? []).filter((item) => !item.is_anime || skipAnimeInput?.checked === false);
  }

  function reviewItems(): StreamingItem[] {
    return visibleItems().filter((item) => {
      if (item.decision?.action === "skip" || item.decision?.action === "confirm" || item.decision?.action === "remap") return false;
      return item.match?.state !== "matched" || item.match?.confidence !== "high" || !item.match?.candidate;
    });
  }

  function titleReviewComplete(): boolean {
    return reviewItems().length === 0;
  }

  function autoMatchedItems(): StreamingItem[] {
    return visibleItems().filter((item) =>
      !item.is_anime && item.match?.state === "matched" && item.match?.confidence === "high" && !!item.match?.candidate && item.decision?.action === "confirm",
    );
  }

  function reviewQueueItems(): StreamingItem[] {
    const automatic = new Set(autoMatchedItems());
    return visibleItems().filter((item) =>
      item.decision?.action !== "skip" && !automatic.has(item),
    );
  }

  function itemCard(item: StreamingItem): string {
    const candidate = item.match?.candidate;
    const accepted = item.decision.action === 'confirm' || item.decision.action === 'remap';
    const skipped = item.decision.action === 'skip';
    const arrow = app.querySelector<HTMLTemplateElement>('[data-role="streaming-match-arrow"]')!.innerHTML;
    return `<article class="streaming-match-row ${skipped ? 'is-skipped' : ''}" data-streaming-item="${esc(item.id)}" aria-label="Match for ${esc(item.source_title)}">
      <div class="streaming-source"><span class="streaming-mobile-label">In your file</span><p>${esc(item.source_title)}</p></div>
      <span class="streaming-match-arrow" aria-hidden="true">${arrow}</span>
      <div class="streaming-found"><span class="streaming-mobile-label">We found</span><div class="streaming-title">${artwork(candidate?.poster_path)}<div>${candidate ? `<h3>${esc(candidate.title)}</h3><p>${[candidate.year, candidate.media_type === 'show' ? 'Series' : 'Movie'].filter(Boolean).map(esc).join(' · ')}</p>` : '<p>No match found</p>'}</div></div></div>
      <div class="streaming-match-actions">
        ${candidate && !item.is_anime ? `<button type="button" data-streaming-action="confirm" data-item-id="${esc(item.id)}" class="streaming-button streaming-primary ${accepted ? 'is-confirmed' : ''}" ${accepted ? 'disabled' : ''}>${accepted ? 'Confirmed' : 'Confirm'}</button>` : `<button type="button" data-streaming-action="open-search" data-item-id="${esc(item.id)}" class="streaming-button streaming-primary">Find title</button>`}
        ${candidate ? `<button type="button" data-streaming-action="open-search" data-item-id="${esc(item.id)}" class="streaming-text-action">Choose another</button>` : ''}
        <button type="button" data-streaming-action="skip-match" data-item-id="${esc(item.id)}" class="streaming-text-action streaming-muted" ${skipped ? 'disabled' : ''}>${skipped ? 'Skipped' : 'Skip'}</button>
      </div>
    </article>`;
  }

  function outcomeButtons(item: StreamingItem): string {
    return (item.kind === "movie" ? [["completed", "Completed"], ["dropped", "Dropped"], ["skip", "Skip"]] : [["completed", "Completed"], ["partial", "Partly watched"], ["skip", "Skip"]]).map(([value, label]) => `<button type="button" data-streaming-action="outcome" data-item-id="${esc(item.id)}" data-status="${value}" aria-pressed="${item.outcome.status === value}" class="streaming-outcome ${item.outcome.status === value ? "is-selected" : ""}"><span class="streaming-choice-check" aria-hidden="true">${checkIcon}</span><span>${label}</span></button>`).join("");
  }

  function partialControls(item: StreamingItem): string {
    if (item.outcome.status !== "partial") return "";
    const seasons = (item.seasons ?? []).filter((season) => season.season_number > 0 && (season.total_released ?? 0) > 0).sort((a, b) => a.season_number - b.season_number);
    if (!seasons.length) return `<p class="mt-3 text-xs text-amber-200">Episode details are unavailable. Skip this title or try again later.</p>`;
    const currentSeason = seasons.find((season) => season.season_number === item.outcome.latest_season) ?? seasons[0];
    const season = currentSeason.season_number;
    const total = currentSeason.total_released ?? 0;
    const episode = Math.max(1, Math.min(total, item.outcome.latest_episode ?? 1));
    const seasonOptions = seasons.map((meta) => `<option value="${meta.season_number}" ${meta.season_number === season ? "selected" : ""}>Season ${meta.season_number}</option>`).join("");
    const episodeOptions = Array.from({ length: Math.min(total, 100) }, (_, i) => i + 1).map((n) => `<option value="${n}" ${n === episode ? "selected" : ""}>Episode ${n}</option>`).join("");
    const tracking = item.outcome.tracking_status ?? "watching";
    return `<div class="streaming-progress-fields"><p class="streaming-field-heading">Last episode watched</p>
      <label class="min-w-0 text-xs font-medium text-zinc-400"><span class="flex min-h-5 items-end">Season</span><select data-streaming-change="latest-season" data-item-id="${esc(item.id)}" class="mt-1 w-full min-w-0 rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-2 text-sm text-zinc-100">${seasonOptions}</select></label>
      <label class="min-w-0 text-xs font-medium text-zinc-400"><span class="flex min-h-5 items-end">Episode</span><select data-streaming-change="latest-episode" data-item-id="${esc(item.id)}" class="mt-1 w-full min-w-0 rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-2 text-sm text-zinc-100">${episodeOptions}</select></label>
      <label class="min-w-0 text-xs font-medium text-zinc-400 sm:col-span-2"><span class="flex min-h-5 items-end">Current status</span><select data-streaming-change="tracking-status" data-item-id="${esc(item.id)}" class="mt-1 w-full min-w-0 rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-2 text-sm text-zinc-100"><option value="watching" ${tracking === "watching" ? "selected" : ""}>Watching</option><option value="paused" ${tracking === "paused" ? "selected" : ""}>Paused</option><option value="dropped" ${tracking === "dropped" ? "selected" : ""}>Dropped</option></select></label>
    </div>`;
  }

  function showCard(item: StreamingItem): string {
    const candidate = item.match.candidate;
    const title = candidate?.title ?? item.source_title;
    return `<article class="streaming-progress-card" data-streaming-item="${esc(item.id)}">
      <div class="streaming-title">${artwork(candidate?.poster_path)}<div><h3>${esc(title)}</h3><p>${[candidate?.year, item.kind === 'show' ? 'Series' : 'Movie'].filter(Boolean).map(esc).join(' · ')}</p></div></div>
      <div class="streaming-outcomes" role="group" aria-label="Watched progress for ${esc(title)}">${outcomeButtons(item)}</div>
      ${partialControls(item)}
      ${item.kind === 'show' ? fileHistory(item) : ''}
    </article>`;
  }

  function progressView(): string {
    const showItems = visibleItems().filter(item => item.decision?.action !== 'skip');
    const incomplete = showItems.filter(isIncomplete);
    const visible = (showingCompleted ? showItems : incomplete).slice().sort((a, b) =>
      (a.match.candidate?.title ?? a.source_title).localeCompare(b.match.candidate?.title ?? b.source_title));
    const completedCount = showItems.length - incomplete.length;
    return `${heading('How much did you watch?')}<div class="streaming-progress-toolbar">${completedCount ? `<label><input type="checkbox" data-streaming-change="show-completed" ${showingCompleted ? 'checked' : ''}>Show completed titles (${completedCount})</label>` : ''}</div>
      ${visible.length ? `<div class="streaming-progress-grid">${visible.map(showCard).join('')}</div>` : '<p class="streaming-empty">All watched progress is checked.</p>'}`;
  }

  function summaryView(): string {
    const rows = [['Movies', summaryNumber('movies')], ['Series', summaryNumber('shows')], ['Episodes', summaryNumber('episodes')]] as const;
    const summaryErrors = session?.summary?.errors;
    const errors = typeof summaryErrors === 'number' ? summaryErrors : Array.isArray(summaryErrors) ? summaryErrors.length : session?.errors?.length ?? 0;
    return `${heading('Ready to import')}<div class="streaming-summary">${rows.map(([label, value]) => `<div><strong>${value.toLocaleString()}</strong><span>${label}</span></div>`).join('')}</div>${errors ? `<p class="streaming-warning">${errors} file row${errors === 1 ? '' : 's'} could not be read.</p>` : ''}`;
  }

  function ratingView(): string {
    const groups = ["Watching", "Completed", "Paused", "Dropped"] as const;
    const eligible = ratingItems();
    const groupFor = (item: StreamingItem): typeof groups[number] => {
      if (item.outcome.status === "completed") return "Completed";
      const status = item.outcome.tracking_status ?? "watching";
      return status === "paused" ? "Paused" : status === "dropped" ? "Dropped" : "Watching";
    };
    const displayProgress = (item: StreamingItem) => {
      if (item.kind !== "show" || !["Watching", "Paused", "Dropped"].includes(groupFor(item))) return "";
      const season = item.outcome.latest_season;
      const episode = item.outcome.latest_episode;
      return season != null && episode != null ? `<p class="mt-1 text-xs text-zinc-500">Season ${season} · Episode ${episode}</p>` : "";
    };
    const card = (item: StreamingItem) => {
      const candidate = item.match.candidate!;
      const score = item.outcome.manual_score;
      return `<article class="streaming-rating-row">
        ${artwork(candidate.poster_path)}
        <div class="min-w-0 flex-1"><h3 class="streaming-rating-title">${esc(candidate.title)}</h3><p class="streaming-media-kind">${item.kind === "show" ? "Series" : "Movie"}</p>${displayProgress(item)}</div>
        <button type="button" data-streaming-action="rate" data-item-id="${esc(item.id)}" aria-label="${score != null ? `Change rating for ${esc(candidate.title)}: ${score.toFixed(1)} out of 10` : `Rate ${esc(candidate.title)}`}" class="streaming-button streaming-rate">${score != null ? `<span aria-hidden="true">★</span> ${score.toFixed(1)} / 10` : "Rate"}</button>
      </article>`;
    };
    return `<div class="streaming-ratings">${heading('Add your ratings', 'Optional — you can do this later.')}
      ${groups.filter(group => eligible.some(item => groupFor(item) === group)).map((group) => {
        const items = eligible.filter((item) => groupFor(item) === group).sort((a, b) => (a.match.candidate?.title ?? a.source_title).localeCompare(b.match.candidate?.title ?? b.source_title));
        return `<details data-motion-details data-rating-group="${group}" ${openRatingGroups.has(group) ? "open" : ""} class="streaming-disclosure rounded-xl border border-zinc-800 bg-zinc-900/30"><summary class="streaming-disclosure-trigger cursor-pointer px-4 py-3 text-sm font-semibold text-zinc-200">${group} <span class="ml-1 text-zinc-500">${items.length}</span>${disclosureArrow}</summary><div class="grid gap-2 border-t border-zinc-800 p-3 sm:grid-cols-2">${items.length ? items.map(card).join("") : `<p class="text-sm text-zinc-500">No titles in this group.</p>`}</div></details>`;
      }).join("")}
    </div>`;
  }



  function ratingItems(): StreamingItem[] {
    return visibleItems().filter(item => item.decision?.action !== 'skip' && item.outcome.status !== 'skip'
      && !!item.match?.candidate && !item.existing?.rated);
  }

  function decisionStep(start: number, direction: 1 | -1): number | null {
    return importDecisionStep(start, direction, {
      matches: reviewQueueItems().length > 0,
      progress: visibleItems().some(item => item.decision?.action !== 'skip' && (isIncomplete(item)
        || (item.outcome.status === 'completed' && item.kind === 'show' && item.completion_evidence
          && !item.completion_evidence.finish_date && !('finish_date' in item.outcome)))),
      ratings: ratingItems().length > 0,
    });
  }

  function updateImportActivity() {
    const active = busy || !!session && !['committed', 'cancelled', 'failed'].includes(session.status);
    app.dataset.importActive = String(active);
    if (active) document.querySelectorAll<HTMLElement>('[data-mobile-navigation] [popover]:popover-open')
      .forEach(panel => panel.hidePopover());
  }

  function render() {
    updateImportActivity();
    if (!session) {
      workflow.classList.add("hidden");
      uploadControls.classList.remove("hidden");
      return;
    }
    workflow.classList.remove("hidden");
    uploadControls.classList.add("hidden");
    const isPreparing = session.status === "preparing";
    const isCommitted = session.status === "committed";
    const isFailed = session.status === "failed";
    let content = "";
    if (isCommitted) {
      step = 5;
      content = `<div class="streaming-complete"><span aria-hidden="true">✓</span><h2>History imported</h2><p>Your movies and series are in your list.</p></div>`;
    } else if (isFailed) {
      content = `<div class="streaming-failure"><h2>We couldn't read this file</h2><p>${esc(session.errors?.map(error => typeof error === 'string' ? error : error.message).filter(Boolean).join(' · ') || session.progress?.message || `Download a new copy of your ${serviceName} viewing history and try again.`)}</p><button type="button" data-streaming-action="restart" class="streaming-button streaming-primary">Choose another file</button></div>`;
    } else if (isPreparing) {
      const total = Math.max(0, session.progress?.total ?? 0);
      const current = Math.max(0, session.progress?.current ?? 0);
      const pct = total > 0 ? Math.max(0, Math.min(100, Math.round(current * 100 / total))) : 0;
      content = `${heading('Preparing your history')}<div class="streaming-preparing"><div class="streaming-loading-track" role="progressbar" aria-label="Preparing history" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><span style="width:${pct}%"></span></div><p>${total ? `${current.toLocaleString()} of ${total.toLocaleString()}` : 'Reading your file…'}</p></div>`;
    } else if (step === 2) {
      const needing = reviewItems();
      const reviewQueue = reviewQueueItems();
      const auto = autoMatchedItems();
      const skipped = visibleItems().filter((item) => item.decision?.action === "skip");
      content = `${heading('Check your matches', 'Confirm the title, choose another, or skip it.', needing.length)}
        <div class="streaming-match-table"><div class="streaming-match-columns" aria-hidden="true"><span>In your file</span><span></span><span>We found</span><span></span></div>${reviewQueue.length ? reviewQueue.map(item => itemCard(item)).join('') : '<p class="streaming-empty">All matches checked.</p>'}</div>
        ${auto.length ? `<details data-motion-details class="streaming-disclosure streaming-review-group"><summary class="streaming-disclosure-trigger">Already matched <span>${auto.length}</span>${disclosureArrow}</summary><div>${auto.map(item => itemCard(item)).join('')}</div></details>` : ''}
        ${skipped.length ? `<details data-motion-details class="streaming-disclosure streaming-review-group"><summary class="streaming-disclosure-trigger">Skipped <span>${skipped.length}</span>${disclosureArrow}</summary><div>${skipped.map(item => itemCard(item)).join('')}</div></details>` : ''}`;
    } else if (step === 3) {
      content = progressView();
    } else if (step === 4) {
      content = ratingView();
    } else {
      content = summaryView();
    }

    const nav = isPreparing || isFailed
      ? `<button type="button" data-streaming-action="cancel" class="streaming-text-action streaming-muted">Cancel</button>`
      : isCommitted ? `<button type="button" data-streaming-action="restart" class="streaming-button streaming-primary">Import another file</button>`
      : `<button type="button" data-streaming-action="cancel" class="streaming-text-action streaming-muted streaming-cancel">Cancel import</button><div class="streaming-nav-actions">${decisionStep(step - 1, -1) !== null ? '<button type="button" data-streaming-action="back" class="streaming-button streaming-secondary">Back</button>' : ''}${step < 5 ? `<button type="button" data-streaming-action="next" ${step === 2 && !titleReviewComplete() ? 'disabled' : ''} class="streaming-button streaming-primary">Continue</button>` : `<button type="button" data-streaming-action="commit" ${busy || saving ? 'disabled' : ''} class="streaming-button streaming-primary">${busy ? 'Importing…' : 'Import history'}</button>`}</div>`;
    workflow.innerHTML = `<div class="streaming-flow">${content}<footer class="streaming-footer">${nav}</footer></div>`;
    if (saving) workflow.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLSelectElement>('button, input, select').forEach(control => { control.disabled = true; });
  }

  function byId(id: string | number): StreamingItem | undefined {
    return session?.items.find((item) => String(item.id) === String(id));
  }

  function saveItem(item: StreamingItem, changes: Record<string, unknown>): Promise<void> {
    if (!session || saving) return Promise.resolve();
    draft.stage({itemId: item.id, changes});
    render();
    return Promise.resolve();
  }

  async function flushDraft(): Promise<boolean> {
    if (!session || saving) return false;
    const id = session.id;
    saving = true;
    render();
    try {
      await draft.flush();
      return session?.id === id;
    } catch (error) {
      if (session?.id === id) {
        try { draft.rebase(await request<StreamingSession>(`/imports/${encodeURIComponent(id)}`)); }
        catch { /* Retain local choices for retry even if the server is unavailable. */ }
        showError(error instanceof Error ? error.message : 'Could not save your choices. Please try Continue again.');
      }
      return false;
    } finally {
      saving = false;
      render();
    }
  }

  async function poll() {
    if (!session || session.status !== "preparing") return;
    try {
      const updated = await request<StreamingSession>(`/imports/${encodeURIComponent(session.id)}`);
      session = updated;
      if (session.status === "preparing") {
        render();
        pollTimer = window.setTimeout(poll, 1000);
        return;
      }
      if (session.status === "review" || session.status === "ready") {
        step = decisionStep(2, 1)!;
        render();
        return;
      }
      if (session.status === "failed") showError("This file could not be prepared. Review the message below or try another export.");
      render();
    } catch (error) {
      showError(error instanceof Error ? error.message : "Could not check import progress.");
      pollTimer = window.setTimeout(poll, 2500);
    }
  }

  async function uploadFile(file?: File) {
    if (!file || !hasTmdbKey || busy) return;
    if (!file.name.toLowerCase().endsWith(extension)) {
      showError(`Choose the ${extension} file downloaded from ${serviceName}.`);
      return;
    }
    clearError();
    openRatingGroups.clear();
    busy = true;
    updateImportActivity();
    uploadControls.classList.add("hidden");
    workflow.classList.remove("hidden");
    step = 1;
    workflow.innerHTML = `<div class="streaming-uploading"><h2>Uploading your history…</h2><p>${esc(file.name)}</p><div class="streaming-loading-track"><span class="animate-pulse" style="width:35%"></span></div><button type="button" data-streaming-action="cancel-upload" class="streaming-text-action streaming-muted">Cancel</button></div>`;
    const form = new FormData();
    form.append("file", file);
    if (languageInput.value !== "auto") form.append("language", languageInput.value);
    uploadController = new AbortController();
    try {
      session = await request<StreamingSession>(`/imports/${service}`, { method: "POST", body: form, signal: uploadController.signal });
      step = 1;
      render();
      if (session.status === "preparing") pollTimer = window.setTimeout(poll, 250);
      else if (session.status === "review" || session.status === "ready") { step = decisionStep(2, 1)!; render(); }
      else render();
    } catch (error) {
      uploadControls.classList.remove("hidden");
      workflow.classList.add("hidden");
      if (!(error instanceof DOMException && error.name === "AbortError")) showError(error instanceof Error ? error.message : `Could not upload the ${serviceName} export.`);
    } finally {
      uploadController = null;
      busy = false;
      updateImportActivity();
      fileInput.value = "";
    }
  }

  function cancelUpload() {
    if (!uploadController) return;
    uploadController.abort();
    uploadController = null;
    busy = false;
    updateImportActivity();
    workflow.classList.add("hidden");
    workflow.innerHTML = "";
    uploadControls.classList.remove("hidden");
    fileInput.value = "";
  }

  function closeRemap() {
    if (remapDialog.open) void closeDialog(remapDialog);
    searchController?.abort();
    window.clearTimeout(searchTimer);
    remappingItemId = null;
  }

  function canRemapShowToMovie(item: StreamingItem): boolean {
    return item.kind === "show" && item.episodes?.length === 1 && item.episodes[0].resolution !== "exact";
  }

  function canRemap(item: StreamingItem, result: SearchResult): boolean {
    if (item.kind === result.media_type) return true;
    if (result.media_type === "movie") return canRemapShowToMovie(item);
    return item.kind === "movie" && result.media_type === "show";
  }

  function renderRemapResults(results: SearchResult[]) {
    const item = remappingItemId == null ? undefined : byId(remappingItemId);
    const available = item ? results.filter((result) => canRemap(item, result)) : [];
    remapResults.hidden = !available.length;
    remapList.innerHTML = available.map((result) => {
      const poster = posterSrc(result.poster_path);
      return `<button type="button" class="quick-search-result" data-streaming-remap-result data-tmdb-id="${esc(result.tmdb_id)}" data-media-type="${esc(result.media_type)}">
        ${poster ? `<img loading="lazy" src="${esc(poster)}" alt="">` : `<span class="quick-search-poster">${esc(result.title.slice(0, 1))}</span>`}
        <span class="min-w-0"><strong class="block truncate">${esc(result.title)}</strong><small>${esc(result.year ?? "")} · ${result.media_type === "movie" ? "Movie" : "Series"}</small></span>
      </button>`;
    }).join("");
    remapState.hidden = !!available.length;
    remapState.textContent = available.length ? "" : item?.kind === "show" && !canRemapShowToMovie(item) ? "No matching series." : "No matching movies or series.";
  }

  async function searchTitles() {
    const query = remapInput.value.trim();
    searchController?.abort();
    const item = remappingItemId == null ? undefined : byId(remappingItemId);
    if (!item) return;
    if (query.length < 2) {
      remapList.innerHTML = "";
      remapResults.hidden = true;
      remapState.textContent = query ? "Type one more character." : "";
      remapState.hidden = !query;
      return;
    }
    const controller = new AbortController();
    searchController = controller;
    remapProgress.classList.add("visible");
    remapState.hidden = true;
    try {
      const seriesOnly = item.kind === "show" && !canRemapShowToMovie(item);
      const responses = await readRemapTitles<{tmdb_id?: number; title: string; poster?: string | null; year?: string | null}>(
        query, seriesOnly, controller.signal, (path, signal) => request(path, {signal}),
      );
      if (controller.signal.aborted || searchController !== controller) return;
      const mediaTypes = seriesOnly ? ["series"] as const : ["movie", "series"] as const;
      const buckets = mediaTypes.map(type => responses[type]);
      const combined: SearchResult[] = [];
      const seen = new Set<string>();
      for (let index = 0; index < Math.max(...buckets.map((bucket) => bucket.length)); index++) {
        for (const [bucketIndex, bucket] of buckets.entries()) {
          const row = bucket[index];
          if (!row?.tmdb_id) continue;
          const media_type = mediaTypes[bucketIndex] === "movie" ? "movie" : "show";
          const key = `${media_type}:${row.tmdb_id}`;
          if (seen.has(key)) continue;
          seen.add(key);
          combined.push({ tmdb_id: row.tmdb_id, media_type, title: row.title, year: row.year, poster_path: row.poster });
        }
        if (combined.length >= 16) break;
      }
      renderRemapResults(combined);
    } catch (error) {
      if (controller.signal.aborted) return;
      remapResults.hidden = true;
      remapState.textContent = error instanceof Error ? error.message : "Search is unavailable. Try again.";
      remapState.hidden = false;
    } finally {
      if (searchController === controller) remapProgress.classList.remove("visible");
    }
  }

  function openRemap(item: StreamingItem) {
    remappingItemId = item.id;
    clearError();
    const searchLabel = item.kind === "show" && !canRemapShowToMovie(item) ? "Search series" : "Search movies and series";
    const dialogTitle = remapDialog.querySelector('[data-role="streaming-remap-title"]');
    const inputLabel = remapDialog.querySelector('label');
    if (dialogTitle) dialogTitle.textContent = searchLabel;
    if (inputLabel) inputLabel.textContent = searchLabel;
    remapInput.placeholder = `${searchLabel}…`;
    remapInput.value = item.kind === "show" && item.episodes?.length === 1
      ? item.episodes[0].source_title || item.source_title : item.source_title;
    remapList.innerHTML = "";
    remapResults.hidden = true;
    showDialog(remapDialog);
    remapInput.focus();
    void searchTitles();
  }

  const finishDialog = app.querySelector<HTMLDialogElement>('[data-role="streaming-finish-dialog"]')!;
  const dateSignal = new AbortController();
  document.addEventListener('astro:before-swap', () => dateSignal.abort(), {once: true});
  const refreshFinishDate = setupCalendarDateInputs(finishDialog, dateSignal.signal, {returnFocus: 'trigger'});
  async function confirmFinishDate(item: StreamingItem, reselectingCompleted = false) {
    if (!needsImportFinishDate(item, reselectingCompleted)) return;
    const input = finishDialog.querySelector<HTMLInputElement>('[data-date-iso]')!;
    input.value = importFinishDateSuggestion(item);
    refreshFinishDate();
    finishDialog.returnValue = '';
    showDialog(finishDialog);
    finishDialog.querySelector<HTMLElement>('h2')!.focus({preventScroll: true});
    await new Promise<void>(resolve => finishDialog.addEventListener('close', () => resolve(), {once: true}));
    await saveItem(item, {finish_date: finishDialog.returnValue === 'confirm' && input.value ? input.value : null});
  }

  async function cancelSession() {
    if (!session) return;
    if (pollTimer) window.clearTimeout(pollTimer);
    try { await request(`/imports/${encodeURIComponent(session.id)}`, { method: "DELETE" }); }
    catch (error) { showError(error instanceof Error ? error.message : "Could not cancel the import."); return; }
    session = null;
    step = 1;
    openRatingGroups.clear();
    showingCompleted = false;
    closeRemap();
    clearError();
    render();
  }

  async function commit() {
    if (!session || busy) return;
    if (!await flushDraft()) return;
    busy = true;
    clearError();
    render();
    idempotencyKey ||= crypto.randomUUID();
    try {
      session = await request<StreamingSession>(`/imports/${encodeURIComponent(session.id)}/commit`, {
        method: "POST", body: JSON.stringify({ revision: session.revision, idempotency_key: idempotencyKey }),
      });
      if (session.status === "committing") {
        workflow.innerHTML = `<div class="streaming-uploading"><h2>Importing your history…</h2><p>Keep this page open while we save your history.</p><div class="streaming-loading-track"><span class="animate-pulse" style="width:65%"></span></div><button type="button" data-streaming-action="cancel" class="streaming-text-action streaming-muted">Cancel import</button></div>`;
        const waitForCommit = async () => {
          if (!session) return;
          try {
            session = await request<StreamingSession>(`/imports/${encodeURIComponent(session.id)}`);
            if (session.status !== "committing") {
              busy = false;
              if (session.status === "review") {
                step = 5;
                showError(session.errors?.map((error) => typeof error === "string" ? error : error.message).filter(Boolean).join(" · ") || "Review your choices and try again.");
              } else if (session.status === "cancelled") {
                session = null;
                step = 1;
              }
              render();
              return;
            }
          } catch (error) { showError(error instanceof Error ? error.message : "Could not check the import result."); }
          pollTimer = window.setTimeout(waitForCommit, 1000);
        };
        await waitForCommit();
      } else {
        busy = false;
        render();
      }
    } catch (error) {
      busy = false;
      showError(error instanceof Error ? error.message : "Import could not be completed.");
      render();
    }
  }

  workflow.addEventListener("click", async (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-streaming-action]");
    if (!button) return;
    const action = button.dataset.streamingAction;
    const id = button.dataset.itemId;
    const item = id ? byId(id) : undefined;
    clearError();
    if (action === "cancel-upload") { cancelUpload(); return; }
    if (action === "cancel") { await cancelSession(); return; }
    if (action === "restart") { session = null; step = 1; openRatingGroups.clear(); clearError(); render(); return; }
    if (saving) return;
    if (action === "back") { step = decisionStep(step - 1, -1) ?? step; render(); workflow.scrollIntoView({ block: "start" }); return; }
    if (action === "next") {
      if (step === 2 && !titleReviewComplete()) {
        showError("Confirm, choose another title, or skip each remaining match."); return;
      }
      if (step === 3) for (const item of visibleItems()) {
        if (item.decision.action !== 'skip' && item.outcome.status === 'completed') await confirmFinishDate(item);
      }
      if (!await flushDraft()) return;
      step = decisionStep(step + 1, 1)!;
      render();
      workflow.scrollIntoView({ block: "start" });
      return;
    }
    if (action === "commit") { await commit(); return; }
    if (!item) return;
    if (action === "rate") {
      const quickRate = (window as any).anyListQuickRate as ((options: { title: string; poster?: string; score?: number | null; onScore: (score: number | null) => Promise<void> }) => void) | undefined;
      if (!quickRate) { showError("Rating controls are unavailable. Please refresh and try again."); return; }
      const candidate = item.match.candidate;
      if (!candidate) return;
      quickRate({ title: candidate.title, poster: posterSrc(candidate.poster_path), score: item.outcome.manual_score,
        onScore: async (score) => { await saveItem(item, { manual_score: score }); },
      });
      return;
    }
    if (action === "confirm") { await saveItem(item, { action: "confirm" }); return; }
    if (action === "skip-match") { await saveItem(item, { action: "skip" }); return; }
    if (action === "open-search") { openRemap(item); return; }
    if (action === "outcome") {
      const status = button.dataset.status as StreamingItem["outcome"]["status"];
      if (status === "completed") await confirmFinishDate(item, item.outcome.status !== 'completed');
      if (status === 'partial' && (!item.outcome.latest_season || !item.outcome.latest_episode)) {
        const firstSeason = (item.seasons ?? []).filter(season => season.season_number > 0 && (season.total_released ?? 0) > 0)
          .sort((a, b) => a.season_number - b.season_number)[0];
        if (firstSeason) {
          await saveItem(item, {status, latest_season: firstSeason.season_number, latest_episode: 1});
          return;
        }
      }
      await saveItem(item, { status });
    }
  });

  workflow.addEventListener("toggle", (event) => {
    const details = event.target as HTMLDetailsElement;
    if (step !== 4 || !details.matches("details[data-rating-group]")) return;
    const group = details.dataset.ratingGroup;
    if (!group) return;
    if (details.open) openRatingGroups.add(group);
    else openRatingGroups.delete(group);
  }, true);

  workflow.addEventListener("change", (event) => {
    const input = event.target as HTMLInputElement | HTMLSelectElement;
    const action = input.dataset.streamingChange;
    const id = input.dataset.itemId;
    if (action === "show-completed") { showingCompleted = (input as HTMLInputElement).checked; render(); return; }
    const item = id ? byId(id) : undefined;
    if (!item || saving) return;
    if (action === "latest-season") {
      const latestSeason = Number(input.value);
      const selectedSeason = (item.seasons ?? []).find((season) => season.season_number === latestSeason);
      const latestEpisode = Math.min(item.outcome.latest_episode ?? selectedSeason?.total_released ?? 1, selectedSeason?.total_released ?? 1);
      void saveItem(item, { latest_season: latestSeason, latest_episode: latestEpisode });
    } else if (action === "latest-episode") {
      void saveItem(item, { latest_episode: Number(input.value) });
    } else if (action === "tracking-status") {
      void saveItem(item, { tracking_status: input.value });
    }
  });

  remapDialog.querySelector('[data-role="streaming-remap-close"]')?.addEventListener("click", closeRemap);
  remapDialog.addEventListener("close", closeRemap);
  remapDialog.addEventListener("click", (event) => {
    if (event.target === remapDialog) closeRemap();
  });
  remapInput.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchController?.abort();
    searchTimer = window.setTimeout(() => { void searchTitles(); }, 180);
  });
  remapInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      window.clearTimeout(searchTimer);
      void searchTitles();
    }
  });
  remapDialog.addEventListener("click", async (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-streaming-remap-result]");
    if (!button || button.disabled || remappingItemId == null || saving) return;
    const item = byId(remappingItemId);
    const tmdbId = Number(button.dataset.tmdbId);
    const mediaType = button.dataset.mediaType === "movie" ? "movie" : "show";
    if (!item || !tmdbId || !canRemap(item, { tmdb_id: tmdbId, media_type: mediaType, title: "" })) return;
    button.disabled = true;
    if (!await flushDraft()) { button.disabled = false; return; }
    if (!session) return;
    saving = true;
    render();
    try {
      session = await request<StreamingSession>(`/imports/${encodeURIComponent(session.id)}/items/${encodeURIComponent(String(item.id))}`, {
        method: 'PATCH', body: JSON.stringify({revision: session.revision, action: 'remap', media_type: mediaType, tmdb_id: tmdbId}),
      });
      closeRemap();
    } catch (error) {
      button.disabled = false;
      showError(error instanceof Error ? error.message : 'Could not remap this title. Please try again.');
    } finally { saving = false; render(); }
  });

  upload.addEventListener("click", (event) => {
    if ((event.target as HTMLElement).closest("a")) return;
    if (hasTmdbKey) fileInput.click();
  });
  upload.addEventListener("keydown", (event) => {
    if ((event.target as HTMLElement).closest("a")) return;
    if (hasTmdbKey && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); fileInput.click(); }
  });
  fileInput.addEventListener("change", () => { void uploadFile(fileInput.files?.[0]); });
  upload.addEventListener("dragover", (event) => { event.preventDefault(); upload.classList.add("border-blue-500", "bg-blue-500/5"); });
  upload.addEventListener("dragleave", () => upload.classList.remove("border-blue-500", "bg-blue-500/5"));
  upload.addEventListener("drop", (event) => {
    event.preventDefault();
    upload.classList.remove("border-blue-500", "bg-blue-500/5");
    void uploadFile(event.dataTransfer?.files?.[0]);
  });

}

function mountStreamingImports() { document.querySelectorAll<HTMLElement>("[data-streaming-import]").forEach(mountStreamingImport); }
document.addEventListener("astro:page-load", mountStreamingImports);
mountStreamingImports();
