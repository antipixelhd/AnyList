import { showDialog, closeDialog } from '../lib/ui-motion';
import { showMessage } from '../lib/settings-feedback';
import { setupCalendarDateInputs } from '../lib/calendar-date-input';
import { createImportDraft } from '../lib/import-draft';
import { importDecisionStep } from '../lib/import-steps';

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

  function seasonSummary(item: StreamingItem): string {
    const seasons = (item.seasons ?? []).filter((season) => season.season_number > 0);
    if (!seasons.length) return "Episode positions unavailable";
    return seasons.map((season) => `S${season.season_number} ${season.represented}/${season.total_released ?? "?"}`).join(" · ");
  }

  function episodeResolutionDetails(item: StreamingItem): string {
    if (item.kind !== "show") return "";
    const episodes = (item.episodes ?? []).filter((episode) =>
      episode.resolution === "exact" || episode.resolution === "guessed" || episode.resolution === "covered" || episode.resolution === "discarded",
    );
    if (!episodes.length) return "";

    const labels: Record<EpisodeResolution, string> = {
      exact: "Exact", guessed: "Guessed", covered: "Covered", discarded: "Discarded",
    };
    const styles: Record<EpisodeResolution, string> = {
      exact: "bg-emerald-500/10 text-emerald-300",
      guessed: "bg-blue-500/10 text-blue-300",
      covered: "bg-zinc-800 text-zinc-400",
      discarded: "bg-amber-500/10 text-amber-200",
    };
    const counts = (Object.keys(labels) as EpisodeResolution[])
      .map((resolution) => ({ resolution, count: episodes.filter((episode) => episode.resolution === resolution).length }))
      .filter(({ count }) => count > 0)
      .map(({ resolution, count }) => `${labels[resolution]} ${count}`)
      .join(" · ");
    const rows = episodes.map((episode) => {
      const resolution = episode.resolution!;
      const title = episode.source_episode_title || episode.source_title || episode.title || `${serviceName} episode`;
      const dates = [...new Set(episode.dates ?? [])].sort();
      const dateLabel = dates.length ? dates.join(", ") : "Date unavailable";
      const hasPosition = episode.season_number != null && episode.episode_number != null;
      const assignment = (resolution === "exact" || resolution === "guessed") && hasPosition
        ? `<span class="text-zinc-300">→ S${episode.season_number}E${episode.episode_number}</span>`
        : resolution === "covered"
        ? `<span class="text-zinc-400">Covered by confirmed progress</span>`
        : resolution === "discarded"
        ? `<span class="text-amber-200">No released catalogue position</span>`
        : "";
      return `<li class="flex flex-col gap-1 rounded-lg bg-zinc-900/60 px-2.5 py-2 sm:flex-row sm:items-center sm:justify-between"><div class="min-w-0"><p class="break-words text-xs font-medium text-zinc-200">${esc(title)}</p><p class="text-[11px] text-zinc-500">${esc(dateLabel)}</p></div><div class="flex shrink-0 items-center gap-2 text-[11px]"><span class="rounded-full px-2 py-0.5 font-semibold ${styles[resolution]}">${labels[resolution]}</span>${assignment}</div></li>`;
    }).join("");

    return `<details data-motion-details class="streaming-disclosure mt-3 rounded-lg border border-zinc-800 bg-zinc-950/40"><summary class="streaming-disclosure-trigger cursor-pointer px-3 py-2 text-xs font-semibold text-zinc-300">Episode resolution · ${esc(counts)}${disclosureArrow}</summary><ul class="max-h-64 space-y-1.5 overflow-auto border-t border-zinc-800 p-2">${rows}</ul></details>`;
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

  function itemCard(item: StreamingItem, needsReview: boolean): string {
    const candidate = item.match?.candidate;
    const poster = posterSrc(candidate?.poster_path);
    const candidateTitle = candidate ? `${candidate.title}${candidate.year ? ` (${candidate.year})` : ""}` : "No confident match found";
    const badge = item.is_anime ? "Anime skipped" : item.decision?.action === "skip" ? "Skipped" : item.decision?.action === "remap" ? "Remapped" : item.decision?.action === "confirm" ? "Confirmed" : needsReview ? "Review needed" : "High confidence";
    const badgeStyle = item.decision?.action === "skip" ? "border-zinc-700 bg-zinc-800 text-zinc-300" : needsReview ? "border-amber-400/30 bg-amber-400/10 text-amber-200" : "border-emerald-400/20 bg-emerald-400/5 text-emerald-300";
    return `<article class="rounded-xl border ${needsReview ? "border-amber-500/25 bg-amber-500/[0.035]" : "border-zinc-800 bg-zinc-900/35"} p-3 sm:p-4" data-streaming-item="${esc(item.id)}">
      <div class="flex flex-col gap-4 sm:flex-row">
        <div class="flex min-w-0 flex-1 gap-3">
          ${poster ? `<img loading="lazy" src="${esc(poster)}" alt="Poster for ${esc(candidateTitle)}" class="h-20 w-14 shrink-0 rounded-lg bg-zinc-800 object-cover">` : `<div class="flex h-20 w-14 shrink-0 items-center justify-center rounded-lg bg-zinc-800 text-lg font-bold text-zinc-500">${esc((candidate?.title || item.source_title).slice(0, 1).toUpperCase())}</div>`}
          <div class="min-w-0">
            <p class="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">${esc(serviceName)} title</p>
            <p class="break-words font-semibold text-zinc-100">${esc(item.source_title)}</p>
            <p class="mt-1 text-xs text-zinc-500">${item.kind === "show" ? "Series" : "Movie"}${item.source_dates?.length ? ` · ${esc(item.source_dates[0])}` : ""}</p>
            <p class="mt-2 text-xs text-zinc-400">${item.decision?.action === "remap" ? "Selected" : "Suggested"} catalogue match: <span class="font-semibold text-zinc-200">${esc(candidateTitle)}</span></p>
            <div class="mt-2 flex flex-wrap items-center gap-2">
              <span class="rounded-full border px-2 py-0.5 text-xs ${badgeStyle}">${badge}</span>
            </div>
            ${item.kind === "show" ? `<p class="mt-2 text-xs text-zinc-400">${esc(seasonSummary(item))}</p>` : ""}
          </div>
        </div>
        <div class="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
          ${candidate && !item.is_anime ? `<button type="button" data-streaming-action="confirm" data-item-id="${esc(item.id)}" ${item.decision?.action === "confirm" || item.decision?.action === "remap" ? "disabled" : ""} class="rounded-lg bg-emerald-600 px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-emerald-500 disabled:cursor-default disabled:opacity-60">${item.decision?.action === "confirm" ? "Confirmed" : item.decision?.action === "remap" ? "Remapped" : `Confirm ${item.kind === "show" ? "show" : "movie"}`}</button>` : ""}
          <button type="button" data-streaming-action="open-search" data-item-id="${esc(item.id)}" class="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm font-semibold text-zinc-200 transition-colors hover:bg-zinc-700">Remap</button>
          ${item.decision?.action !== "skip" ? `<button type="button" data-streaming-action="skip-match" data-item-id="${esc(item.id)}" class="rounded-lg px-3 py-2 text-sm font-semibold text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200">Skip</button>` : ""}
        </div>
      </div>
    </article>`;
  }

  function outcomeButtons(item: StreamingItem): string {
    return (item.kind === "movie" ? [["completed", "Completed"], ["dropped", "Dropped"], ["skip", "Skip"]] : [["completed", "Completed"], ["partial", "Partial"], ["skip", "Skip"]]).map(([value, label]) => `<button type="button" data-streaming-action="outcome" data-item-id="${esc(item.id)}" data-status="${value}" aria-pressed="${item.outcome.status === value}" class="flex-1 rounded-lg px-2 py-2 text-xs font-semibold transition-colors sm:text-sm ${item.outcome.status === value ? "bg-blue-600 text-white" : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"}">${label}</button>`).join("");
  }

  function partialControls(item: StreamingItem): string {
    if (item.outcome.status !== "partial") return "";
    const seasons = (item.seasons ?? []).filter((season) => season.season_number > 0 && (season.total_released ?? 0) > 0).sort((a, b) => a.season_number - b.season_number);
    if (!seasons.length) return `<p class="mt-3 text-xs text-amber-200">Episode details are not available for this show yet. Choose Skip or wait for the catalogue to finish loading.</p>`;
    const currentSeason = seasons.find((season) => season.season_number === item.outcome.latest_season) ?? seasons[0];
    const season = currentSeason.season_number;
    const total = currentSeason.total_released ?? 0;
    const episode = Math.max(1, Math.min(total, item.outcome.latest_episode ?? 1));
    const seasonOptions = seasons.map((meta) => `<option value="${meta.season_number}" ${meta.season_number === season ? "selected" : ""}>Season ${meta.season_number}</option>`).join("");
    const episodeOptions = Array.from({ length: Math.min(total, 100) }, (_, i) => i + 1).map((n) => `<option value="${n}" ${n === episode ? "selected" : ""}>Episode ${n}</option>`).join("");
    const tracking = item.outcome.tracking_status ?? "watching";
    return `<div class="grid gap-3 sm:grid-cols-2">
      <label class="min-w-0 text-xs font-medium text-zinc-400"><span class="flex min-h-5 items-end">Season</span><select data-streaming-change="latest-season" data-item-id="${esc(item.id)}" class="mt-1 w-full min-w-0 rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-2 text-sm text-zinc-100">${seasonOptions}</select></label>
      <label class="min-w-0 text-xs font-medium text-zinc-400"><span class="flex min-h-5 items-end">Episode</span><select data-streaming-change="latest-episode" data-item-id="${esc(item.id)}" class="mt-1 w-full min-w-0 rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-2 text-sm text-zinc-100">${episodeOptions}</select></label>
      <label class="min-w-0 text-xs font-medium text-zinc-400 sm:col-span-2"><span class="flex min-h-5 items-end">Status</span><select data-streaming-change="tracking-status" data-item-id="${esc(item.id)}" class="mt-1 w-full min-w-0 rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-2 text-sm text-zinc-100"><option value="watching" ${tracking === "watching" ? "selected" : ""}>Watching</option><option value="paused" ${tracking === "paused" ? "selected" : ""}>Paused</option><option value="dropped" ${tracking === "dropped" ? "selected" : ""}>Dropped</option></select></label>
    </div>`;
  }

  function showCard(item: StreamingItem): string {
    const candidate = item.match.candidate;
    const poster = posterSrc(candidate?.poster_path);
    const seasons = (item.seasons ?? []).filter((season) => season.season_number > 0);
    return `<article class="flex h-full min-w-0 flex-col gap-3 rounded-2xl border border-zinc-800 bg-zinc-900/50 p-4 lg:row-span-3 lg:grid lg:[grid-template-rows:subgrid]" data-streaming-item="${esc(item.id)}">
      <div class="flex gap-3">
        ${poster ? `<img loading="lazy" src="${esc(poster)}" alt="Poster for ${esc(candidate?.title ?? item.source_title)}" class="h-24 w-16 shrink-0 rounded-lg bg-zinc-800 object-cover">` : `<div class="h-24 w-16 shrink-0 rounded-lg bg-zinc-800"></div>`}
        <div class="min-w-0 flex-1">
          <h3 class="break-words font-semibold text-zinc-100">${esc(candidate?.title ?? item.source_title)}</h3>
          ${seasons.length ? `<div class="mt-2 flex flex-wrap gap-1.5">${seasons.map((season) => `<span class="rounded-md bg-zinc-800 px-2 py-1 text-[11px] text-zinc-400">S${season.season_number}: ${season.represented}/${season.total_released ?? "?"}</span>`).join("")}</div>` : ""}
          ${episodeResolutionDetails(item)}
        </div>
      </div>
      <div class="flex rounded-xl border border-zinc-800 bg-zinc-950 p-1" role="group" aria-label="Import progress for ${esc(candidate?.title ?? item.source_title)}">${outcomeButtons(item)}</div>
      <div>${item.outcome.status === "skip" ? `<p class="text-xs text-zinc-500">Skip leaves this title and any existing AnyList progress untouched.</p>` : partialControls(item)}</div>
    </article>`;
  }

  function progressView(): string {
    const showItems = visibleItems().filter((item) => item.decision?.action !== "skip");
    const incomplete = showItems.filter(isIncomplete);
    const visible = (showingCompleted ? showItems : incomplete).slice().sort((a, b) =>
      (a.match.candidate?.title ?? a.source_title).localeCompare(b.match.candidate?.title ?? b.source_title),
    );
    const completedCount = showItems.length - incomplete.length;
    return `<div class="space-y-4">
      <div><p class="text-xs font-bold uppercase tracking-[0.16em] text-blue-300">Step 3 of 5 · Review watched progress</p><h2 class="mt-1 text-xl font-bold text-zinc-100">Choose how each title should appear in AnyList</h2><p class="mt-1 text-sm text-zinc-400">For Partial Progress select the latest watched episode and its current status.</p></div>
      <label class="flex cursor-pointer items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-3 text-sm text-zinc-300"><input type="checkbox" data-streaming-change="show-completed" ${showingCompleted ? "checked" : ""} class="h-4 w-4 rounded border-zinc-600 bg-zinc-950 text-blue-600 focus:ring-blue-500"><span>Show fully represented shows <span class="text-zinc-500">(${completedCount})</span></span></label>
      ${visible.length ? `<div class="grid gap-3 lg:grid-cols-2">${visible.map(showCard).join("")}</div>` : `<div class="rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-8 text-center text-sm text-zinc-400">${showItems.length ? "All shows are represented through their latest released episode. Use the control above to inspect them." : "No matched shows in this export."}</div>`}
    </div>`;
  }

  function summaryView(): string {
    const movies = summaryNumber("movies");
    const shows = summaryNumber("shows");
    const episodes = summaryNumber("episodes");
    const rows = [
      ["Movies", movies],
      ["Shows", shows],
      ["Episodes", episodes],
    ] as const;
    const summaryErrors = session?.summary?.errors;
    const errors = typeof summaryErrors === "number" ? summaryErrors : Array.isArray(summaryErrors) ? summaryErrors.length : session?.errors?.length ?? 0;
    return `<div class="space-y-5">
      <div><p class="text-xs font-bold uppercase tracking-[0.16em] text-blue-300">Step 5 of 5 · Final review</p><h2 class="mt-1 text-xl font-bold text-zinc-100">Ready to import</h2><p class="mt-1 text-sm text-zinc-400">Review what will change in your AnyList account. Nothing is applied until you select Import.</p></div>
      <div class="space-y-3">
        ${rows.map(([label, value]) => `<div class="flex items-center justify-between gap-6 py-3"><span class="text-base text-zinc-300">${label}</span><strong class="text-2xl tabular-nums text-zinc-100">${value.toLocaleString()}</strong></div>`).join("")}
      </div>
      <p class="text-xs text-zinc-500">Episode total includes the positions filled by your final Partial and Completed choices.</p>
      ${errors ? `<p class="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-sm text-amber-200">${errors} CSV row${errors === 1 ? "" : "s"} could not be read.</p>` : ""}
    </div>`;
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
      return season != null && episode != null ? `<p class="mt-1 text-xs text-zinc-500">Progress · S${season}E${episode}</p>` : "";
    };
    const card = (item: StreamingItem) => {
      const candidate = item.match.candidate!;
      const poster = posterSrc(candidate.poster_path);
      const score = item.outcome.manual_score;
      return `<article class="flex min-w-0 items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-900/40 p-3">
        ${poster ? `<img loading="lazy" src="${esc(poster)}" alt="Poster for ${esc(candidate.title)}" class="h-16 w-11 shrink-0 rounded-md bg-zinc-800 object-cover">` : `<div class="flex h-16 w-11 shrink-0 items-center justify-center rounded-md bg-zinc-800 text-sm font-bold text-zinc-500">${esc(candidate.title.slice(0, 1).toUpperCase())}</div>`}
        <div class="min-w-0 flex-1"><h3 class="truncate text-sm font-semibold text-zinc-100">${esc(candidate.title)}</h3><span class="mt-1 inline-flex rounded-full bg-zinc-800 px-2 py-0.5 text-[10px] font-semibold text-zinc-300">${item.kind === "show" ? "Series" : "Movie"}</span>${displayProgress(item)}</div>
        <button type="button" data-streaming-action="rate" data-item-id="${esc(item.id)}" aria-label="${score != null ? `Change rating for ${esc(candidate.title)}: ${score.toFixed(1)} out of 10` : `Rate ${esc(candidate.title)}`}" class="shrink-0 rounded-lg bg-yellow-400 px-3 py-2 text-xs font-bold text-white transition-colors hover:bg-yellow-300">${score != null ? `<span aria-hidden="true">★</span> ${score.toFixed(1)} / 10` : "Rate now"}</button>
      </article>`;
    };
    return `<div class="space-y-4"><div><p class="text-xs font-bold uppercase tracking-[0.16em] text-blue-300">Step 4 of 5 · Optional ratings</p><h2 class="mt-1 text-xl font-bold text-zinc-100">Rate shows and movies</h2><p class="mt-1 text-sm text-zinc-400">This is an optional step. You can still rate anything later in your own list.</p></div>
      ${groups.map((group) => {
        const items = eligible.filter((item) => groupFor(item) === group).sort((a, b) => (a.match.candidate?.title ?? a.source_title).localeCompare(b.match.candidate?.title ?? b.source_title));
        return `<details data-motion-details data-rating-group="${group}" ${openRatingGroups.has(group) ? "open" : ""} class="streaming-disclosure rounded-xl border border-zinc-800 bg-zinc-900/30"><summary class="streaming-disclosure-trigger cursor-pointer px-4 py-3 text-sm font-semibold text-zinc-200">${group} <span class="ml-1 text-zinc-500">${items.length}</span>${disclosureArrow}</summary><div class="grid gap-2 border-t border-zinc-800 p-3 sm:grid-cols-2">${items.length ? items.map(card).join("") : `<p class="text-sm text-zinc-500">No titles in this group.</p>`}</div></details>`;
      }).join("")}
    </div>`;
  }

  function stepHeader(): string {
    const labels = ["Upload", "Review matches", "Watch progress", "Ratings", "Import"];
    return `<div class="mb-5 flex flex-wrap items-center gap-2">${labels.map((label, index) => `<div class="flex items-center gap-2"><span class="flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${step === index + 1 ? "bg-blue-600 text-white" : step > index + 1 ? "bg-emerald-500/20 text-emerald-300" : "bg-zinc-800 text-zinc-500"}">${step > index + 1 ? "✓" : index + 1}</span><span class="text-xs ${step === index + 1 ? "font-semibold text-zinc-200" : "text-zinc-500"}">${label}</span></div>${index < labels.length - 1 ? `<span class="h-px min-w-4 flex-1 bg-zinc-800"></span>` : ""}`).join("")}</div>`;
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

  function render() {
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
      content = `<div class="rounded-2xl border border-emerald-500/25 bg-emerald-500/5 p-5"><p class="text-xs font-bold uppercase tracking-[0.16em] text-emerald-300">Import complete</p><h2 class="mt-1 text-xl font-bold text-zinc-100">${esc(serviceName)} history added</h2><p class="mt-2 text-sm text-zinc-300">Your reviewed movies and show progress have been added to AnyList.</p></div>`;
    } else if (isFailed) {
      content = `<div class="rounded-xl border border-red-500/25 bg-red-500/5 p-4"><h2 class="font-semibold text-red-200">We couldn't prepare this export</h2><p class="mt-1 text-sm text-zinc-400">${esc(session.errors?.map((error) => typeof error === "string" ? error : error.message).filter(Boolean).join(" · ") || session.progress?.message || `Try downloading a new copy of your ${serviceName} viewing history.`)}</p><button type="button" data-streaming-action="restart" class="mt-3 rounded-lg border border-zinc-700 px-3 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800">Choose another file</button></div>`;
    } else if (isPreparing) {
      const total = Math.max(0, session.progress?.total ?? 0);
      const current = Math.max(0, session.progress?.current ?? 0);
      const pct = total > 0 ? Math.max(0, Math.min(100, Math.round(current * 100 / total))) : 0;
      content = `<div class="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-4 sm:p-6"><p class="text-xs font-bold uppercase tracking-[0.16em] text-blue-300">Step 1 of 5 · Preparing import</p><h2 class="mt-1 text-lg font-bold text-zinc-100">${esc(session.progress?.message || (session.phase === "parse" ? "Reading your CSV…" : "Matching titles and resolving episode positions…"))}</h2><div class="mt-4 h-2 overflow-hidden rounded-full bg-zinc-800"><div class="h-full rounded-full bg-blue-500 transition-all" style="width:${pct}%"></div></div><div class="mt-2 flex justify-between gap-3 text-xs text-zinc-500"><span>${total ? `${current.toLocaleString()} of ${total.toLocaleString()} items` : "Getting started"}</span><span>${total ? `${pct}%` : esc(session.phase)}</span></div><p class="mt-3 text-xs text-zinc-500">Duplicate rows are grouped before matching so the same title is only looked up once.</p></div>`;
    } else if (step === 2) {
      const needing = reviewItems();
      const reviewQueue = reviewQueueItems();
      const auto = autoMatchedItems();
      const skipped = visibleItems().filter((item) => item.decision?.action === "skip");
      content = `<div class="space-y-4"><div><p class="text-xs font-bold uppercase tracking-[0.16em] text-blue-300">Step 2 of 5 · Review title matches</p><h2 class="mt-1 text-xl font-bold text-zinc-100">Confirm anything uncertain</h2><p class="mt-1 text-sm text-zinc-400">Confirm watched movies and show matches. Remap incorrect titles or skip them.</p></div>
        ${reviewQueue.length ? `<div class="space-y-3">${reviewQueue.map((item) => itemCard(item, needing.includes(item))).join("")}</div>` : `<div class="rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-4 py-3 text-sm text-emerald-200">All show and movie titles are resolved. Episode positions will be assigned automatically where the catalogue permits.</div>`}
        ${auto.length ? `<details data-motion-details class="streaming-disclosure rounded-xl border border-zinc-800 bg-zinc-900/30"><summary class="streaming-disclosure-trigger cursor-pointer px-4 py-3 text-sm font-semibold text-zinc-300">${auto.length} high confidence match${auto.length === 1 ? "" : "es"} accepted automatically${disclosureArrow}</summary><div class="space-y-3 border-t border-zinc-800 p-3">${auto.map((item) => itemCard(item, false)).join("")}</div></details>` : ""}
        ${skipped.length ? `<details data-motion-details class="streaming-disclosure rounded-xl border border-zinc-800 bg-zinc-900/30"><summary class="streaming-disclosure-trigger cursor-pointer px-4 py-3 text-sm font-semibold text-zinc-400">${skipped.length} title${skipped.length === 1 ? "" : "s"} skipped · reopen to change${disclosureArrow}</summary><div class="space-y-3 border-t border-zinc-800 p-3">${skipped.map((item) => itemCard(item, false)).join("")}</div></details>` : ""}
      </div>`;
    } else if (step === 3) {
      content = progressView();
    } else if (step === 4) {
      content = ratingView();
    } else {
      content = summaryView();
    }

    const nav = isPreparing
      ? `<button type="button" data-streaming-action="cancel" class="rounded-lg px-3 py-2 text-sm font-semibold text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">Cancel</button>`
      : isFailed
      ? `<button type="button" data-streaming-action="cancel" class="rounded-lg px-3 py-2 text-sm font-semibold text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">Cancel</button>`
      : `<div class="flex w-full flex-col-reverse gap-2 sm:w-auto sm:flex-row"><button type="button" data-streaming-action="cancel" class="rounded-lg px-3 py-2 text-sm font-semibold text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">Cancel import</button>${decisionStep(step - 1, -1) !== null ? `<button type="button" data-streaming-action="back" class="rounded-lg border border-zinc-700 px-4 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800">Back</button>` : ""}${step < 5 ? `<button type="button" data-streaming-action="next" ${step === 2 && !titleReviewComplete() ? "disabled" : ""} class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-40">Continue</button>` : `<button type="button" data-streaming-action="commit" ${busy || saving ? "disabled" : ""} class="rounded-lg bg-emerald-600 px-5 py-2 text-sm font-bold text-white transition-colors hover:bg-emerald-500 disabled:cursor-wait disabled:opacity-50">${busy ? "Importing…" : "Import"}</button>`}</div>`;
    workflow.innerHTML = `${!isCommitted ? stepHeader() : ""}<div class="space-y-5">${content}<div class="flex flex-col-reverse items-stretch justify-between gap-3 border-t border-zinc-800 pt-4 sm:flex-row sm:items-center">${isPreparing ? `<span class="text-xs text-zinc-500">Matching in progress</span>` : ""}${!isCommitted ? nav : `<button type="button" data-streaming-action="restart" class="self-start rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white hover:bg-blue-500 sm:self-auto">Import another file</button>`}</div></div>`;
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
      if (session.status === "failed") showError("This CSV could not be prepared. Review the message below or try another export.");
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
    uploadControls.classList.add("hidden");
    workflow.classList.remove("hidden");
    step = 1;
    workflow.innerHTML = `<div class="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5"><div class="flex items-center justify-between gap-3"><p class="text-sm font-semibold text-zinc-200">Uploading ${esc(file.name)}…</p><button type="button" data-streaming-action="cancel-upload" class="rounded-lg px-3 py-2 text-sm font-semibold text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">Cancel</button></div><div class="mt-3 h-2 overflow-hidden rounded-full bg-zinc-800"><div class="h-full w-1/3 animate-pulse rounded-full bg-blue-500"></div></div></div>`;
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
      fileInput.value = "";
    }
  }

  function cancelUpload() {
    if (!uploadController) return;
    uploadController.abort();
    uploadController = null;
    busy = false;
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
      const search = (media_type: "movie" | "series") => request<{ results: Array<{ id?: number | null; tmdb_id?: number; type: string; title: string; poster?: string | null; year?: string | null }> }>(
        `/tracking/catalog?${new URLSearchParams({ media_type, q: query })}`, { signal: controller.signal },
      );
      const mediaTypes = item.kind === "show" && !canRemapShowToMovie(item) ? ["series"] as const : ["movie", "series"] as const;
      const responses = await Promise.all(mediaTypes.map(search));
      if (searchController !== controller) return;
      const buckets = responses.map((response) => response.results ?? []);
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
  const refreshFinishDate = setupCalendarDateInputs(finishDialog, dateSignal.signal);
  async function confirmFinishDate(item: StreamingItem) {
    if (item.kind !== 'show' || item.completion_evidence?.finish_date || 'finish_date' in item.outcome) return;
    const input = finishDialog.querySelector<HTMLInputElement>('[data-date-iso]')!;
    input.value = item.completion_evidence?.suggested_date || '';
    refreshFinishDate();
    finishDialog.returnValue = '';
    showDialog(finishDialog);
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
        workflow.innerHTML = `<div class="rounded-2xl border border-emerald-500/20 bg-emerald-500/5 p-5"><p class="font-semibold text-emerald-200">Importing reviewed changes…</p><p class="mt-2 text-sm text-zinc-400">Please keep this page open while AnyList saves your watch history.</p><button type="button" data-streaming-action="cancel" class="mt-4 rounded-lg px-3 py-2 text-sm font-semibold text-zinc-300 hover:bg-zinc-800">Cancel import</button></div>`;
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
        showError("Resolve each uncertain show or movie title before continuing."); return;
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
      if (status === "completed") await confirmFinishDate(item);
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
