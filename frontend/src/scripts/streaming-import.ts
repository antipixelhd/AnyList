import { canStartImport } from "../lib/import-admission";
import { showDialog, closeDialog, reveal, cancelUiMotion } from '../lib/ui-motion';
import { showMessage } from '../lib/settings-feedback';
import { setupCalendarDateInputs } from '../lib/calendar-date-input';
import { confirmImportCancellation } from '../lib/import-cancel';
import { importRecovery } from '../lib/import-recovery';
import { createImportDraft } from '../lib/import-draft';
import { importDecisionStep } from '../lib/import-steps';
import { readRemapTitles } from '../lib/streaming-remap-search';
import { formatDate } from '../lib/date-time';
import { needsImportFinishDate, importFinishDateSuggestion, needsImportProgressReview as isIncomplete, hasConfirmedMovieEvidence } from '../lib/import-completion';
import { createImportSequence } from '../lib/import-sequence';

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
  source: string;
  id: string;
  expires_at?: string;
  connection_id?: number | null;
  idempotency_key?: string | null;
  status: "preparing" | "review" | "ready" | "committed" | "cancelled" | "failed" | string;
  phase: string;
  progress: { current: number; total: number; message: string };
  revision: number;
  items: StreamingItem[];
  summary: Record<string, unknown> | null;
  errors?: (string | { message?: string; row?: number })[];
};

type SearchResult = MatchCandidate & { id?: number | null };

let activeConnectionImport: HTMLElement | null = null;

function mountStreamingImport(app: HTMLElement) {
  if (!app || app.dataset.streamingInitialized === "true") return;
  app.dataset.streamingInitialized = "true";
  const disclosureArrow = app.querySelector<HTMLTemplateElement>('[data-role="streaming-disclosure-arrow"]')!.innerHTML;
  const starIcon = app.querySelector<HTMLTemplateElement>('[data-role="streaming-star"]')!.innerHTML;
  const mobileReview = matchMedia('(max-width: 650px)');
  const matchSequence = createImportSequence();
  const progressSequence = createImportSequence();
  let sequenceSessionId: string | undefined;
  let ratingsInitialized = false;
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
  const connectionId = app.dataset.connectionImport;
  const sourceLabel = connectionId ? `From ${serviceName}` : "In your file";
  const token = document.getElementById("app-data")?.getAttribute("data-token") ?? "";
  const hasTmdbKey = app.getAttribute("data-has-tmdb-key") === "true";

  let session: StreamingSession | null = null;
  let admissionState = "";
  let step = 1;
  let pollTimer: number | undefined;
  let saving = false;
  let cancelling = false;
  let requestEpoch = 0;
  let saveController = new AbortController();
  // Storage can be unavailable in private browsing; keep the review functional.
  let recovery: ReturnType<typeof importRecovery> | undefined;
  try { recovery = importRecovery(localStorage, document.body.dataset.cacheUser || 'anonymous'); } catch { /* Optional recovery. */ }
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
    saveBatch: (current, patches) => request<StreamingSession>(`/imports/${encodeURIComponent(current.id)}/decisions`, {
      method: 'PATCH', body: JSON.stringify({revision: current.revision, patches}), signal: saveController.signal,
    }),
    save: (current, {itemId, changes}) => request<StreamingSession>(`/imports/${encodeURIComponent(current.id)}/items/${encodeURIComponent(String(itemId))}`, {
      method: 'PATCH', body: JSON.stringify({revision: current.revision, ...changes}),
    }),
  });
  let idempotencyKey = "";
  let busy = false;
  let loadingConnection = false;
  let checkingConnection = !!connectionId && app.dataset.connectionPending === "true";
  let connectionError = "";
  let positionedWorkflow = false;
  const launchParams = new URL(location.href).searchParams;
  let restoringSession = !!app.dataset.resumeImport || !!launchParams.get('import') && (
    connectionId ? launchParams.get('connection') === connectionId : launchParams.get('source') === service && !launchParams.has('connection'));
  let restoreController: AbortController | null = null;
  let connectionProgress = {current:0, total:0, message:'Waiting to start…'};
  let currentViewKey = '';
  let currentViewHtml = '';
  let renderedView: HTMLElement | null = null;

  function setWorkflow(html: string, key: string) {
    if (currentViewHtml === html) return;
    const changed = currentViewKey !== key;
    if (renderedView) cancelUiMotion(renderedView);
    workflow.innerHTML = html;
    renderedView = workflow.firstElementChild as HTMLElement | null;
    currentViewHtml = html;
    currentViewKey = key;
    if (changed && renderedView) {
      renderedView.setAttribute('data-motion-fade', '');
      reveal(renderedView);
    }
  }
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
    const timeout = AbortSignal.timeout(init.body instanceof FormData ? 120000 : 30000);
    const signal = AbortSignal.any([dateSignal.signal, timeout, ...(init.signal ? [init.signal] : [])]);
    const response = await fetch(`/api/proxy${path}`, { ...init, headers, signal });
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
    return `<details data-motion-details class="streaming-disclosure streaming-history"><summary class="streaming-disclosure-trigger">View episodes${disclosureArrow}</summary><ul>${episodes.map(episode => {
      const position = episode.resolution === 'discarded' ? 'Not included'
        : episode.resolution === 'covered' ? 'Included in your progress'
        : episode.season_number != null && episode.episode_number != null
        ? `Season ${episode.season_number} · Episode ${episode.episode_number}` : 'Episode not identified';
      const dates = [...new Set(episode.dates ?? [])].sort().map(date => formatDate(date));
      return `<li><span>${esc(episode.source_episode_title || episode.source_title || episode.title || position)}</span><span>${esc([position, ...dates].join(' · '))}</span></li>`;
    }).join('')}</ul></details>`;
  }

  function heading(title: string, description = '', remaining?: number, position = ''): string {
    const total = connectionId ? 3 : 5;
    const displayStep = connectionId ? Math.max(0,step - 2) : step;
    const label = displayStep ? `Step ${displayStep} of ${total}` : '';
    const prefix = connectionId ? esc(serviceName) + (label ? ' · ' : '') : '';
    const bars = label ? `<div class="streaming-step-bars" role="img" aria-label="${label}">${Array.from({length:total}, (_,index) => `<span class="${index < displayStep ? 'is-reached' : ''}"></span>`).join('')}</div>` : '';
    return `<header class="streaming-heading"><div><div class="streaming-heading-top"><p class="streaming-step">${prefix}${label}</p>${step > 1 ? '<button type="button" data-streaming-action="cancel" class="streaming-text-action streaming-muted streaming-mobile-cancel">Cancel</button>' : ''}</div><h2>${title}</h2>${description ? `<p class="streaming-description">${description}</p>` : ''}${position ? `<p class="streaming-position">${esc(position)}</p>` : ''}</div><div class="streaming-step-progress">${remaining != null ? `<span role="status">${remaining ? `${remaining} title${remaining === 1 ? '' : 's'} left` : 'All matches checked'}</span>` : ''}${bars}</div></header>`;
  }

  function artwork(path?: string | null): string {
    const poster = posterSrc(path);
    return poster ? `<img class="streaming-artwork" loading="lazy" src="${esc(poster)}" alt="">`
      : `<span class="streaming-artwork streaming-artwork-empty" aria-hidden="true">${app.querySelector<HTMLTemplateElement>('[data-role="streaming-poster-placeholder"]')!.innerHTML}</span>`;
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
    return reviewItems();
  }

  function syncReviewSequences() {
    if (sequenceSessionId !== session?.id) {
      sequenceSessionId = session?.id;
      matchSequence.reset(); progressSequence.reset();
      ratingsInitialized = false;
    }
    if (session && ['review', 'ready'].includes(session.status)) matchSequence.sync(reviewItems().map(item => item.id));
  }

  function reviewLabel(item: StreamingItem, phase: 'matches' | 'progress'): string {
    if (phase === 'matches') return item.decision.action === 'skip' ? 'Skipped'
      : item.decision.action === 'confirm' || item.decision.action === 'remap' ? 'Confirmed' : 'To review';
    if (item.outcome.status === 'skip') return 'Skipped';
    if (item.outcome.status === 'completed') return 'Completed';
    if (item.kind === 'movie') return 'Dropped';
    return `Season ${item.outcome.latest_season ?? 1} · Episode ${item.outcome.latest_episode ?? 1}`;
  }

  function reviewLinks(items: StreamingItem[], phase: 'matches' | 'progress'): string {
    return `<div class="streaming-history-links">${items.map(item => `<button type="button" data-streaming-action="review-item" data-item-id="${esc(item.id)}"><span>${esc(item.match.candidate?.title ?? item.source_title)}</span><small>${esc(reviewLabel(item, phase))}</small></button>`).join('')}</div>`;
  }

  function reviewHistory(items: StreamingItem[], phase: 'matches' | 'progress'): string {
    return items.length ? `<details data-motion-details class="streaming-disclosure streaming-review-history"><summary class="streaming-disclosure-trigger">Reviewed titles <span>${items.length}</span>${disclosureArrow}</summary>${reviewLinks(items, phase)}</details>` : '';
  }

  function reviewOverview(items: StreamingItem[], phase: 'matches' | 'progress'): string {
    const labels = phase === 'matches' ? ['To review', 'Confirmed', 'Skipped'] : ['Completed', 'Partly watched', 'Dropped', 'Skipped'];
    const group = (item: StreamingItem) => phase === 'matches' ? reviewLabel(item, phase)
      : item.outcome.status === 'partial' ? 'Partly watched' : reviewLabel(item, phase);
    return `<div class="streaming-review-overview">${labels.map(label => {
      const rows = items.filter(item => group(item) === label);
      return rows.length ? `<section><h3>${label} <span>${rows.length}</span></h3>${reviewLinks(rows, phase)}</section>` : '';
    }).join('')}</div>`;
  }

  function matchCurrent(): StreamingItem | undefined {
    if (matchSequence.selected && !byId(matchSequence.selected)) matchSequence.resume();
    const id = matchSequence.current(reviewItems().map(item => String(item.id)));
    return id ? byId(id) : undefined;
  }

  function progressPending(): string[] {
    return progressSequence.pending().filter(id => {
      const item = byId(id);
      return item && item.decision.action !== 'skip' && !hasConfirmedMovieEvidence(item);
    });
  }

  function progressCurrent(): StreamingItem | undefined {
    const selected = progressSequence.selected;
    if (selected && (!byId(selected) || byId(selected)?.decision.action === 'skip')) progressSequence.resume();
    const id = progressSequence.current(progressPending());
    return id ? byId(id) : undefined;
  }

  function itemCard(item: StreamingItem, editing = false, automatic = false): string {
    const candidate = item.match?.candidate;
    const accepted = !editing && (item.decision.action === 'confirm' || item.decision.action === 'remap');
    const skipped = item.decision.action === 'skip';
    const arrow = app.querySelector<HTMLTemplateElement>('[data-role="streaming-match-arrow"]')!.innerHTML;
    return `<article class="streaming-match-row ${skipped ? 'is-skipped' : ''}" data-streaming-item="${esc(item.id)}" aria-label="Match for ${esc(item.source_title)}">
      <div class="streaming-source"><span class="streaming-mobile-label">${esc(sourceLabel)}</span><p>${esc(item.source_title)}</p></div>
      <span class="streaming-match-arrow" aria-hidden="true">${arrow}</span>
      <div class="streaming-found"><span class="streaming-mobile-label">We found</span><div class="streaming-title">${artwork(candidate?.poster_path)}<div>${candidate ? `<h3>${esc(candidate.title)}</h3><p>${[candidate.year, candidate.media_type === 'show' ? 'Series' : 'Movie'].filter(Boolean).map(esc).join(' · ')}</p>` : '<p>No match found</p>'}</div></div></div>
      <div class="streaming-match-actions">
        ${automatic ? '' : candidate && !item.is_anime ? `<button type="button" data-streaming-action="confirm" data-item-id="${esc(item.id)}" class="streaming-button streaming-primary ${accepted ? 'is-confirmed' : ''}" ${accepted ? 'disabled' : ''}>${accepted ? 'Confirmed' : 'Confirm'}</button>` : `<button type="button" data-streaming-action="open-search" data-item-id="${esc(item.id)}" class="streaming-button streaming-primary">Find title</button>`}
        ${candidate ? `<button type="button" data-streaming-action="open-search" data-item-id="${esc(item.id)}" class="streaming-button ${automatic ? 'streaming-primary' : 'streaming-secondary'}">Choose another</button>` : ''}
        ${automatic ? '' : `<button type="button" data-streaming-action="skip-match" data-item-id="${esc(item.id)}" class="streaming-button streaming-secondary" ${skipped ? 'disabled' : ''}>${skipped ? 'Skipped' : 'Skip'}</button>`}
      </div>
    </article>`;
  }

  function outcomeButtons(item: StreamingItem): string {
    return (item.kind === "movie" ? [["completed", "Completed"], ["dropped", "Dropped"], ["skip", "Skip"]] : [["completed", "Completed"], ["partial", "Partly watched"], ["skip", "Skip"]]).map(([value, label]) => `<button type="button" data-streaming-action="outcome" data-item-id="${esc(item.id)}" data-status="${value}" aria-pressed="${item.outcome.status === value}" class="streaming-outcome ${item.outcome.status === value ? "is-selected" : ""}"><span>${label}</span></button>`).join("");
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
      <label class="min-w-0 text-xs font-medium text-zinc-400"><span class="flex min-h-5 items-end">Current status</span><select data-streaming-change="tracking-status" data-item-id="${esc(item.id)}" class="mt-1 w-full min-w-0 rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-2 text-sm text-zinc-100"><option value="watching" ${tracking === "watching" ? "selected" : ""}>Watching</option><option value="paused" ${tracking === "paused" ? "selected" : ""}>Paused</option><option value="dropped" ${tracking === "dropped" ? "selected" : ""}>Dropped</option></select></label>
    </div>`;
  }

  function showCard(item: StreamingItem): string {
    const candidate = item.match.candidate;
    const title = candidate?.title ?? item.source_title;
    return `<article class="streaming-progress-card" data-streaming-item="${esc(item.id)}">
      <div class="streaming-title">${artwork(candidate?.poster_path)}<div><h3>${esc(title)}</h3><p>${[candidate?.year, item.kind === 'show' ? 'Series' : 'Movie'].filter(Boolean).map(esc).join(' · ')}</p></div></div>
      <div class="streaming-progress-choice"><p class="streaming-choice-label">How much have you watched?</p><div class="streaming-outcomes" role="group" aria-label="Watched progress for ${esc(title)}">${outcomeButtons(item)}</div>
      ${partialControls(item)}
      ${item.kind === 'show' ? fileHistory(item) : ''}</div>
    </article>`;
  }

  function matchesView(): string {
    const pending = reviewQueueItems();
    const automatic = autoMatchedItems();
    const automaticSet = new Set(automatic);
    const confirmed = visibleItems().filter(item => !automaticSet.has(item) && ['confirm', 'remap'].includes(item.decision.action ?? ''));
    const skipped = visibleItems().filter(item => item.decision.action === 'skip');
    if (mobileReview.matches) {
      const current = matchCurrent();
      const index = current ? matchSequence.ids.indexOf(String(current.id)) : -1;
      const position = current ? index >= 0 ? `Title ${index + 1} of ${matchSequence.ids.length}` : 'Reviewing an earlier choice' : '';
      const body = current ? `<div class="streaming-mobile-focus">${itemCard(current, true, automaticSet.has(current))}</div>`
        : matchSequence.overview ? reviewOverview(visibleItems(), 'matches')
        : '<div class="streaming-review-done"><h3>All matches checked</h3></div>';
      return `${heading('Check your matches', '', undefined, position)}${body}${!matchSequence.overview || current ? reviewHistory([...confirmed, ...skipped, ...automatic], 'matches') : ''}`;
    }
    const group = (label: string, items: StreamingItem[]) => items.length ? `<details data-motion-details ${matchSequence.overview ? 'open' : ''} class="streaming-disclosure streaming-review-group"><summary class="streaming-disclosure-trigger">${label} <span>${items.length}</span>${disclosureArrow}</summary><div>${items.map(item => itemCard(item, true, automaticSet.has(item))).join('')}</div></details>` : '';
    return `${heading('Check your matches', 'Confirm the title, choose another, or skip it.', pending.length)}
      <div class="streaming-match-table"><div class="streaming-match-columns" aria-hidden="true"><span>${esc(sourceLabel)}</span><span></span><span>We found</span><span></span></div>${pending.length ? pending.map(item => itemCard(item)).join('') : '<p class="streaming-empty">All matches checked.</p>'}</div>
      ${group('Confirmed', confirmed)}${group('Skipped', skipped)}${group('Already matched', automatic)}`;
  }

  function progressView(): string {
    const showItems = visibleItems().filter(item => item.decision?.action !== 'skip');
    const incomplete = showItems.filter(item => isIncomplete(item) || item.outcome.status === 'completed' && !!item.completion_evidence && needsImportFinishDate(item));
    progressSequence.sync(incomplete.map(item => item.id));
    const visible = (showingCompleted ? showItems : incomplete).slice().sort((a, b) =>
      (a.match.candidate?.title ?? a.source_title).localeCompare(b.match.candidate?.title ?? b.source_title));
    const completedCount = showItems.length - incomplete.length;
    if (mobileReview.matches) {
      const current = progressCurrent();
      const index = current ? progressSequence.ids.indexOf(String(current.id)) : -1;
      const position = current ? index >= 0 ? `Title ${index + 1} of ${progressSequence.ids.length}` : 'Reviewing an earlier choice' : '';
      const pending = new Set(progressSequence.pending());
      const reviewed = progressSequence.ids.filter(id => !pending.has(id)).map(id => byId(id)).filter((item): item is StreamingItem => !!item && item.decision.action !== 'skip');
      return `${heading('Set your watch progress', '', undefined, position)}${current ? `<div class="streaming-mobile-focus">${showCard(current)}</div>` : reviewOverview(showItems, 'progress')}${!progressSequence.overview || current ? reviewHistory(reviewed, 'progress') : ''}`;
    }
    return `${heading('Set your watch progress')}<div class="streaming-progress-toolbar">${completedCount ? `<label><input type="checkbox" data-streaming-change="show-completed" ${showingCompleted ? 'checked' : ''}>Show completed titles (${completedCount})</label>` : ''}</div>
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
    if (!ratingsInitialized) {
      for (const group of groups) openRatingGroups.add(group);
      ratingsInitialized = true;
    }
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
        <button type="button" data-streaming-action="rate" data-item-id="${esc(item.id)}" aria-label="${score != null ? `Change rating for ${esc(candidate.title)}: ${score.toFixed(1)} out of 10` : `Rate ${esc(candidate.title)}`}" class="streaming-button streaming-rate ${score != null ? 'is-rated' : ''}">${score != null ? `<span aria-hidden="true">${starIcon}</span> ${score.toFixed(1)} <small>/ 10</small>` : "Rate"}</button>
      </article>`;
    };
    return `<div class="streaming-ratings">${heading('Add your ratings', 'Optional — you can do this later.')}
      ${groups.filter(group => eligible.some(item => groupFor(item) === group)).map((group) => {
        const items = eligible.filter((item) => groupFor(item) === group).sort((a, b) => (a.match.candidate?.title ?? a.source_title).localeCompare(b.match.candidate?.title ?? b.source_title));
        return `<details data-motion-details data-rating-group="${group}" ${openRatingGroups.has(group) ? "open" : ""} class="streaming-disclosure rounded-xl border border-zinc-800 bg-zinc-900/30"><summary class="streaming-disclosure-trigger cursor-pointer px-4 py-3 text-sm font-semibold text-zinc-200">${group} <span class="ml-1 text-zinc-500">${items.length}</span>${disclosureArrow}</summary><div class="streaming-rating-list">${items.length ? items.map(card).join("") : `<p class="text-sm text-zinc-500">No titles in this group.</p>`}</div></details>`;
      }).join("")}
    </div>`;
  }



  function ratingItems(): StreamingItem[] {
    return visibleItems().filter(item => item.decision?.action !== 'skip' && item.outcome.status !== 'skip'
      && !!item.match?.candidate && !item.existing?.rated);
  }

  function decisionStep(start: number, direction: 1 | -1): number | null {
    syncReviewSequences();
    return importDecisionStep(start, direction, {
      matches: matchSequence.ids.length > 0,
      progress: progressSequence.ids.some(id => {
        const item = byId(id);
        return item && item.decision.action !== 'skip' && !hasConfirmedMovieEvidence(item);
      }) || visibleItems().some(item => item.decision?.action !== 'skip' && (isIncomplete(item)
        || (item.outcome.status === 'completed' && item.kind === 'show' && item.completion_evidence
          && !item.completion_evidence.finish_date && !('finish_date' in item.outcome)))),
      ratings: ratingItems().length > 0,
    });
  }

  function reviewNavigation(): string | null {
    if (!mobileReview.matches || ![2, 3].includes(step)) return null;
    const sequence = step === 2 ? matchSequence : progressSequence;
    const current = step === 2 ? matchCurrent() : progressCurrent();
    const left = decisionStep(step - 1, -1) !== null
      ? '<button type="button" data-streaming-action="back" class="streaming-button streaming-secondary">Back</button>' : '';
    const label = step === 3 && current && sequence.selected ? 'Save changes' : 'Continue';
    const returnToPending = step === 2 && !!sequence.selected && !titleReviewComplete();
    return `<div class="streaming-nav-actions">${left}<button type="button" data-streaming-action="${returnToPending ? 'return-review' : 'next'}" ${step === 2 && !titleReviewComplete() && !returnToPending ? 'disabled' : ''} class="streaming-button streaming-primary">${label}</button></div>`;
  }

  function updateImportActivity() {
    const active = restoringSession || checkingConnection || busy || !!session && !['committed', 'cancelled', 'failed'].includes(session.status);
    app.dataset.importActive = String(active);
    if (active) document.querySelectorAll<HTMLElement>('[data-mobile-navigation] [popover]:popover-open')
      .forEach(panel => panel.hidePopover());
  }

  function checkpoint() {
    if (!session || !session.expires_at) return;
    if (['cancelled', 'committed'].includes(session.status)) { recovery?.remove(session.id); return; }
    recovery?.save(session.id, {revision: session.revision, expiresAt: session.expires_at, step,
      patches: draft.snapshot(), matches: matchSequence.snapshot(), progress: progressSequence.snapshot()});
    const url = new URL(location.href);
    url.searchParams.set('import', session.id);
    url.searchParams.set('source', service);
    if (connectionId) url.searchParams.set('connection', connectionId);
    else { url.searchParams.delete('connection'); url.searchParams.set('section', 'imports'); }
    history.replaceState(history.state, '', url);
  }

  function restoreSession(updated: StreamingSession) {
    session = updated;
    idempotencyKey = updated.idempotency_key || '';
    const saved = recovery?.read(updated.id);
    syncReviewSequences();
    if (saved && updated.status === 'review') {
      if (saved.revision === updated.revision) draft.restore(saved.patches);
      matchSequence.restore(saved.matches); progressSequence.restore(saved.progress);
      step = saved.step >= 2 ? saved.step : decisionStep(2, 1)!;
    } else step = updated.status === 'preparing' ? 1 : decisionStep(2, 1)!;
  }

  function render() {
    uploadControls.hidden = !!connectionId;
    workflow.setAttribute('aria-busy', String(restoringSession || checkingConnection || saving || cancelling || busy || session?.status === 'preparing'));
    syncReviewSequences();
    checkpoint();
    const nextAdmissionState = session ? `${session.id}:${session.status}` : checkingConnection ? `connection:${connectionId}:checking` : "";
    if (nextAdmissionState !== admissionState) {
      admissionState = nextAdmissionState;
      document.dispatchEvent(new CustomEvent('streaming:session-changed'));
    }
    app.dataset.reviewLayout = mobileReview.matches ? 'single' : 'list';
    app.dataset.importStep = String(step);
    if (session && !['cancelled', 'committed'].includes(session.status)) app.dataset.importSession = session.id;
    else if (restoringSession && (app.dataset.resumeImport || launchParams.get('import'))) app.dataset.importSession = app.dataset.resumeImport || launchParams.get('import')!;
    else delete app.dataset.importSession;
    updateImportActivity();
    document.dispatchEvent(new CustomEvent('streaming:activity-changed'));
    if (!session) {
      if (restoringSession || connectionId && checkingConnection) {
        step = 1; app.dataset.importStep = '1'; app.hidden = false;
        workflow.classList.remove('hidden'); uploadControls.classList.add('hidden');
        const title = restoringSession ? 'Loading your import' : 'Checking your history';
        const total = Math.max(0,connectionProgress.total);
        const current = Math.max(0,connectionProgress.current);
        const percent = total ? Math.min(100,Math.round(current / total * 100)) : 0;
        const status = restoringSession ? 'Restoring your review…' : connectionProgress.message;
        const track = '<div class="streaming-loading-track ' + (total ? '' : 'is-indeterminate') + '" role="progressbar" aria-label="' + title + '"' + (total ? ' aria-valuenow="' + percent + '" aria-valuemin="0" aria-valuemax="100"' : '') + '><span' + (total ? ' style="width:' + percent + '%"' : '') + '></span></div>';
        const content = connectionError ? '<p class="streaming-check-error" role="alert">' + esc(connectionError) + '</p>'
          : '<div class="streaming-preparing">' + track + '<p role="status">' + esc(status) + (total ? ' · ' + current.toLocaleString() + ' / ' + total.toLocaleString() : '') + '</p></div>';
        const action = connectionId ? 'cancel' : 'close-loading';
        const label = connectionId ? 'Cancel import' : 'Close';
        setWorkflow('<div class="streaming-flow">' + heading(title) + content + '<footer class="streaming-footer"><button type="button" data-streaming-action="' + action + '" class="streaming-button streaming-secondary" ' + (cancelling ? 'disabled' : '') + '>' + label + '</button>' + (connectionError && connectionId ? '<button type="button" data-streaming-action="retry-connection" class="streaming-button streaming-primary">Try again</button>' : '') + '</footer></div>',
          JSON.stringify(['loading',restoringSession,!!connectionError,cancelling]));
        return;
      }
      if (connectionId) {
        app.hidden = true;
        if (activeConnectionImport === app) {
          activeConnectionImport = null;
          document.dispatchEvent(new CustomEvent('streaming:connection-finished'));
        }
      }
      workflow.classList.add("hidden");
      uploadControls.classList.remove("hidden");
      return;
    }
    app.hidden = false;
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
      content = `<div class="streaming-failure"><h2>${connectionId ? "Import could not be completed" : "We couldn't read this file"}</h2><p>${esc(session.errors?.map(error => typeof error === 'string' ? error : error.message).filter(Boolean).join(' · ') || session.progress?.message || `Download a new copy of your ${serviceName} viewing history and try again.`)}</p><button type="button" data-streaming-action="${connectionId ? "cancel" : "restart"}" class="streaming-button streaming-primary">${connectionId ? "Cancel import" : "Choose another file"}</button></div>`;
    } else if (isPreparing) {
      const total = Math.max(0, session.progress?.total ?? 0);
      const current = Math.max(0, session.progress?.current ?? 0);
      const pct = total > 0 ? Math.max(0, Math.min(100, Math.round(current * 100 / total))) : 0;
      content = `${heading('Preparing your history')}<div class="streaming-preparing"><div class="streaming-loading-track" role="progressbar" aria-label="Preparing history" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><span style="width:${pct}%"></span></div><p>${total ? `${current.toLocaleString()} of ${total.toLocaleString()}` : 'Reading your file…'}</p></div>`;
    } else if (step === 2) {
      content = matchesView();
    } else if (step === 3) {
      content = progressView();
    } else if (step === 4) {
      content = ratingView();
    } else {
      content = summaryView();
    }

    const transitioning = saving || cancelling || (busy && !isPreparing);
    if (transitioning) {
      content = `<div class="streaming-uploading" role="status" aria-live="polite"><h2>${cancelling ? 'Cancelling import…' : busy ? 'Importing your history…' : 'Saving your choices…'}</h2><div class="streaming-loading-track"><span class="animate-pulse" style="width:50%"></span></div></div>`;
    }
    const nav = transitioning
      ? `<button type="button" data-streaming-action="cancel" class="streaming-button streaming-secondary" ${cancelling ? 'disabled' : ''}>Cancel import</button>`
      : isPreparing || isFailed
      ? `<button type="button" data-streaming-action="cancel" class="streaming-text-action streaming-muted">Cancel</button>`
      : isCommitted ? `<button type="button" data-streaming-action="restart" class="streaming-button streaming-primary">${connectionId ? 'Done' : 'Import another file'}</button>`
      : reviewNavigation() ?? `<button type="button" data-streaming-action="cancel" class="streaming-text-action streaming-muted streaming-cancel">Cancel import</button><div class="streaming-nav-actions">${decisionStep(step - 1, -1) !== null ? '<button type="button" data-streaming-action="back" class="streaming-button streaming-secondary">Back</button>' : ''}${step < 5 ? `<button type="button" data-streaming-action="next" ${step === 2 && !titleReviewComplete() ? 'disabled' : ''} class="streaming-button streaming-primary">Continue</button>` : `<button type="button" data-streaming-action="commit" ${busy || saving ? 'disabled' : ''} class="streaming-button streaming-primary">${busy ? 'Importing…' : 'Import history'}</button>`}</div>`;
    const card = mobileReview.matches ? step === 2 ? matchCurrent()?.id : step === 3 ? progressCurrent()?.id : null : null;
    setWorkflow(`<div class="streaming-flow">${content}<footer class="streaming-footer">${nav}</footer></div>`,
      JSON.stringify([session.id, step, session.status, transitioning ? cancelling ? 'cancel' : busy ? 'commit' : 'save' : 'review', card]));

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
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
      await draft.flush();
      return session?.id === id && !cancelling && !dateSignal.signal.aborted;
    } catch (error) {
      if (session?.id === id && !cancelling && !dateSignal.signal.aborted) {
        try { draft.rebase(await request<StreamingSession>(`/imports/${encodeURIComponent(id)}`, {signal: AbortSignal.timeout(5000)})); }
        catch { /* Retain local choices for retry even if the server is unavailable. */ }
        showError(error instanceof Error ? error.message : 'Could not save your choices. Please try Continue again.');
      }
      return false;
    } finally {
      saving = false;
      render();
    }
  }

  async function advanceAfterLastMatch() {
    if (step !== 2 || !titleReviewComplete() || !await flushDraft()) return;
    step = decisionStep(3, 1)!;
    render();
    workflow.scrollIntoView({block: 'start'});
  }

  async function poll() {
    if (!session || session.status !== "preparing") return;
    const epoch = requestEpoch;
    const id = session.id;
    try {
      const updated = await request<StreamingSession>(`/imports/${encodeURIComponent(id)}`);
      if (epoch !== requestEpoch || session?.id !== id || dateSignal.signal.aborted) return;
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
      if (epoch !== requestEpoch || dateSignal.signal.aborted) return;
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
    if (!await canStartImport()) return;
    clearError();
    openRatingGroups.clear();
    busy = true;
    updateImportActivity();
    uploadControls.classList.add("hidden");
    workflow.classList.remove("hidden");
    step = 1;
    workflow.setAttribute('aria-busy', 'true');
    setWorkflow(`<div class="streaming-uploading"><h2>Uploading your history…</h2><p>${esc(file.name)}</p><div class="streaming-loading-track"><span class="animate-pulse" style="width:35%"></span></div><button type="button" data-streaming-action="cancel-upload" class="streaming-text-action streaming-muted">Cancel</button></div>`, 'upload');
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
    setWorkflow("", "idle");
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
    // Opening a dialog can select its prefilled search text. Keep the caret at the end.
    remapInput.setSelectionRange(remapInput.value.length, remapInput.value.length);
    void searchTitles();
  }

  const finishDialog = app.querySelector<HTMLDialogElement>('[data-role="streaming-finish-dialog"]')!;
  const dateSignal = new AbortController();
  document.addEventListener('astro:before-swap', () => {
    checkpoint();
    dateSignal.abort();
    if (activeConnectionImport === app) activeConnectionImport = null;
    window.clearTimeout(pollTimer);
    window.clearTimeout(searchTimer);
    searchController?.abort();
    uploadController?.abort();
    restoreController?.abort();
    if (renderedView) cancelUiMotion(renderedView);
  }, {once: true});
  window.addEventListener('pagehide', checkpoint, {signal: dateSignal.signal});
  mobileReview.addEventListener('change', () => render(), {signal: dateSignal.signal});
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
    if (cancelling) return;
    if (!session && connectionId && (checkingConnection || restoringSession)) {
      if (!await confirmImportCancellation(true, dateSignal.signal)) return;
      cancelling = true;
      window.clearTimeout(pollTimer);
      render();
      try {
        await request('/auth/connections/' + encodeURIComponent(connectionId), {method:'DELETE'});
        const url = new URL(location.href);
        for (const key of ['import', 'source', 'connection']) url.searchParams.delete(key);
        history.replaceState(history.state, '', url);
        location.reload();
      } catch (error) {
        cancelling = false;
        connectionError = error instanceof Error ? error.message : 'Could not cancel the import.';
        render();
      }
      return;
    }
    if (!session) return;
    if (connectionId && !await confirmImportCancellation(true, dateSignal.signal)) return;
    const cancelledId = session.id;
    if (pollTimer) window.clearTimeout(pollTimer);
    cancelling = true; requestEpoch++; saveController.abort(); saveController = new AbortController(); render();
    try { await request(`/imports/${encodeURIComponent(cancelledId)}`, { method: "DELETE" }); }
    catch (error) {
      cancelling = false; busy = false; render();
      showError(error instanceof Error ? error.message : "Could not cancel the import.");
      if (session?.status === 'preparing') void poll();
      else if (session?.status === 'committing') void commit();
      return;
    }
    recovery?.remove(cancelledId);
    document.dispatchEvent(new CustomEvent('media-tracker:notifications-changed'));
    session = null; cancelling = false; busy = false;
    const url = new URL(location.href); url.searchParams.delete('import'); url.searchParams.delete('source'); url.searchParams.delete('connection');
    history.replaceState(history.state, '', url);
    if (connectionId) { location.reload(); return; }
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
    const epoch = requestEpoch;
    const committingId = session.id;
    clearError();
    render();
    idempotencyKey ||= crypto.randomUUID();
    try {
      const updated = await request<StreamingSession>(`/imports/${encodeURIComponent(committingId)}/commit`, {
        method: "POST", body: JSON.stringify({ revision: session.revision, idempotency_key: idempotencyKey }),
      });
      if (epoch !== requestEpoch || session?.id !== committingId || dateSignal.signal.aborted) return;
      session = updated;
      if (session.status === "committing") {
        setWorkflow(`<div class="streaming-uploading"><h2>Importing your history…</h2><p>Keep this page open while we save your history.</p><div class="streaming-loading-track"><span class="animate-pulse" style="width:65%"></span></div><button type="button" data-streaming-action="cancel" class="streaming-text-action streaming-muted">Cancel import</button></div>`, 'committing');
        const waitForCommit = async () => {
          if (!session || epoch !== requestEpoch || dateSignal.signal.aborted) return;
          try {
            const updated = await request<StreamingSession>(`/imports/${encodeURIComponent(committingId)}`);
            if (epoch !== requestEpoch || session?.id !== committingId || dateSignal.signal.aborted) return;
            session = updated;
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
          } catch (error) {
            if (epoch !== requestEpoch || dateSignal.signal.aborted) return;
            showError(error instanceof Error ? error.message : "Could not check the import result.");
          }
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
    if (action === 'retry-connection' && connectionId) {
      connectionError = ''; render();
      try { await request('/sync/connection/' + encodeURIComponent(connectionId), {method:'POST'}); void loadConnectionImport(); }
      catch (error) { connectionError = error instanceof Error ? error.message : 'Could not check your history.'; render(); }
      return;
    }
    if (action === 'close-loading') {
      restoreController?.abort(); restoreController = null;
      restoringSession = false;
      const url = new URL(location.href);
      for (const key of ['import', 'source']) url.searchParams.delete(key);
      history.replaceState(history.state, '', url);
      render(); return;
    }
    if (action === "cancel-upload") { cancelUpload(); return; }
    if (action === "cancel") { await cancelSession(); return; }
    if (action === "restart") {
      if (session?.status === 'failed') { await cancelSession(); return; }
      session = null; step = 1; openRatingGroups.clear(); clearError();
      const url = new URL(location.href);
      for (const key of ['import', 'source', 'connection']) url.searchParams.delete(key);
      history.replaceState(history.state, '', url);
      if (app.dataset.connectionCreated === 'true') { location.reload(); return; }
      render(); return;
    }
    if (saving) return;
    if (action === "back") {
      const previousStep = decisionStep(step - 1, -1);
      if (previousStep === null) return;
      step = previousStep;
      if (step === 2) matchSequence.showOverview();
      if (step === 3) { progressSequence.showOverview(); showingCompleted = true; }
      render(); workflow.scrollIntoView({block: 'start'}); return;
    }
    if (action === 'review-item') {
      (step === 2 ? matchSequence : progressSequence).visit(id!);
      render(); workflow.scrollIntoView({block: 'start'}); return;
    }
    if (action === 'return-review') {
      (step === 2 ? matchSequence : progressSequence).resume(); render(); return;
    }
    if (action === "next") {
      if (step === 2 && !titleReviewComplete()) {
        showError("Confirm, choose another title, or skip each remaining match."); return;
      }
      let finishedProgressId: string | undefined;
      if (step === 3 && mobileReview.matches) {
        const current = progressCurrent();
        if (current) {
          if (current.outcome.status === 'completed') await confirmFinishDate(current);
          await saveItem(current, {status: current.outcome.status});
          if (progressSequence.selected || progressPending().length > 1) {
            progressSequence.complete(current.id); render(); workflow.scrollIntoView({block: 'start'}); return;
          }
          finishedProgressId = String(current.id);
        }
      }
      if (step === 3) for (const item of visibleItems()) {
        if (item.decision.action !== 'skip' && item.outcome.status === 'completed') await confirmFinishDate(item);
      }
      if (!await flushDraft()) return;
      if (finishedProgressId) progressSequence.complete(finishedProgressId);
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
    if (action === "confirm" || action === 'skip-match') {
      const wasPending = reviewItems().some(pending => pending.id === item.id);
      matchSequence.complete(item.id);
      progressSequence.invalidate(item.id);
      await saveItem(item, {action: action === 'confirm' ? 'confirm' : 'skip'});
      if (wasPending) await advanceAfterLastMatch();
      if (mobileReview.matches) workflow.scrollIntoView({block: 'start'});
      return;
    }
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
    const wasPending = reviewItems().some(pending => pending.id === item.id);
    let remapped = false;
    try {
      session = await request<StreamingSession>(`/imports/${encodeURIComponent(session.id)}/items/${encodeURIComponent(String(item.id))}`, {
        method: 'PATCH', body: JSON.stringify({revision: session.revision, action: 'remap', media_type: mediaType, tmdb_id: tmdbId}),
      });
      matchSequence.complete(item.id);
      progressSequence.invalidate(item.id);
      closeRemap();
      remapped = true;
    } catch (error) {
      button.disabled = false;
      showError(error instanceof Error ? error.message : 'Could not remap this title. Please try again.');
    } finally { saving = false; render(); }
    if (remapped && wasPending) await advanceAfterLastMatch();
  });

  function positionImport() {
    if (positionedWorkflow || app.hidden) return;
    positionedWorkflow = true;
    const visit = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;
    if (app.dataset.connectionCreated !== 'true' && (visit?.type === 'reload' || visit?.type === 'back_forward')) return;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (!app.isConnected || dateSignal.signal.aborted || app.hidden) return;
      workflow.scrollIntoView({block:'start'});
      const title = workflow.querySelector('h2');
      if (title) { title.tabIndex = -1; title.focus({preventScroll:true}); }
    }));
  }

  async function loadConnectionImport() {
    if (!connectionId || !app.isConnected || dateSignal.signal.aborted || session || loadingConnection || cancelling) return;
    if (app.parentElement?.closest('[hidden]')) return;
    if (activeConnectionImport?.isConnected && activeConnectionImport !== app) return;
    const requestedConnection = new URL(location.href).searchParams.get('connection');
    if (requestedConnection && requestedConnection !== connectionId) return;
    loadingConnection = true;
    if (checkingConnection || restoringSession) { activeConnectionImport = app; render(); positionImport(); }
    try {
      const result = await request<{session: StreamingSession | null; checking: boolean; error?: string; progress?: {current:number; total:number; message:string}}>(
        '/imports/connections/' + encodeURIComponent(connectionId), {signal: dateSignal.signal});
      if (dateSignal.signal.aborted || cancelling) return;
      if (result.session) {
        if (activeConnectionImport?.isConnected && activeConnectionImport !== app) return;
        if (result.session.connection_id !== Number(connectionId) || result.session.source !== service)
          throw new Error('This import belongs to another connection. Refresh to check this account again.');
        activeConnectionImport = app;
        restoringSession = false; checkingConnection = false; connectionError = '';
        restoreSession(result.session);
        render(); positionImport();
        if (result.session.status === 'committing') void commit();
      } else if (result.checking || result.error) {
        restoringSession = false; checkingConnection = true; connectionError = result.error || '';
        if (result.progress) connectionProgress = result.progress;
        activeConnectionImport = app;
        render(); positionImport();
        if (!connectionError) pollTimer = window.setTimeout(loadConnectionImport, 1500);
      } else {
        restoringSession = false; checkingConnection = false;
        render();
        if (app.dataset.connectionCreated === 'true') {
          const url = new URL(location.href);
          for (const key of ['import', 'source', 'connection']) url.searchParams.delete(key);
          history.replaceState(history.state, '', url);
          location.reload();
        }
      }
    } catch (error) {
      if (!dateSignal.signal.aborted && !cancelling) {
        restoringSession = false; checkingConnection = true;
        connectionError = error instanceof Error ? error.message : 'Could not load connection history.';
        render(); positionImport();
      }
    } finally { loadingConnection = false; }
  }
  document.addEventListener('connections:sync-updated', () => { void loadConnectionImport(); }, {signal: dateSignal.signal});
  document.addEventListener('streaming:connection-finished', () => { void loadConnectionImport(); }, {signal: dateSignal.signal});
  if (connectionId) void loadConnectionImport();
  else {
    const params = new URL(location.href).searchParams;
    const id = params.get('import');
    if (id && params.get('source') === service && !params.has('connection')) {
      for (let parent = app.parentElement; parent; parent = parent.parentElement) {
        if (parent instanceof HTMLDetailsElement) parent.open = true;
      }
      restoringSession = true; render(); positionImport();
      restoreController = new AbortController();
      const restoring = restoreController;
      void request<StreamingSession>(`/imports/${encodeURIComponent(id)}`, {signal:restoring.signal}).then(updated => {
        if (dateSignal.signal.aborted || restoring.signal.aborted) return;
        restoringSession = false;
        restoreSession(updated); render();
        if (session?.status === 'preparing') void poll();
        if (session?.status === 'committing') void commit();
      }).catch(error => {
        if (dateSignal.signal.aborted || restoring.signal.aborted) return;
        restoringSession = false; render(); showError(error.message);
      });
    }
  }

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

/** Start onboarding in the same page using the canonical importer markup. */
export function launchConnectionImport(connection: {id:number; type:string; name:string; sync_watched?:boolean}) {
  if (!connection || !Number.isInteger(connection.id) || connection.id <= 0 || !['stremio', 'nuvio'].includes(connection.type) || connection.sync_watched === false) return false;
  const host = document.querySelector<HTMLElement>('[data-connection-imports]');
  const template = document.querySelector<HTMLTemplateElement>('[data-connection-import-template="' + connection.type + '"]');
  if (!host || !template) return false;
  const app = template.content.firstElementChild!.cloneNode(true) as HTMLElement;
  const prefix = 'connection-' + connection.id;
  for (const element of [app, ...app.querySelectorAll<HTMLElement>('*')]) {
    for (const name of ['id','for','aria-controls','aria-labelledby']) {
      const value = element.getAttribute(name);
      if (value) element.setAttribute(name, value.replaceAll('connection--1', prefix));
    }
  }
  app.dataset.connectionImport = String(connection.id);
  app.dataset.serviceName = connection.name;
  app.dataset.connectionPending = 'true';
  app.dataset.connectionCreated = 'true';
  const url = new URL(location.href);
  url.searchParams.delete('import'); url.searchParams.delete('section');
  url.searchParams.set('connection', String(connection.id)); url.searchParams.set('source', connection.type);
  history.replaceState(history.state, '', url);
  host.append(app);
  const form = document.querySelector<HTMLElement>('[data-import-start-connection]');
  if (form) form.hidden = true;
  mountStreamingImport(app);
  return true;
}

function mountStreamingImports() { document.querySelectorAll<HTMLElement>("[data-streaming-import]").forEach(mountStreamingImport); }
document.addEventListener("astro:page-load", mountStreamingImports);
mountStreamingImports();
