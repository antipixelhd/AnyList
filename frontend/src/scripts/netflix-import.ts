type MatchCandidate = {
  media_type: "movie" | "show";
  tmdb_id: number;
  title: string;
  year?: number | string | null;
  poster_path?: string | null;
};

type EpisodeResolution = "exact" | "guessed" | "covered" | "discarded";
type NetflixEpisode = {
  source_title?: string | null;
  source_episode_title?: string | null;
  title?: string | null;
  dates?: string[];
  season_number?: number | null;
  episode_number?: number | null;
  resolution?: EpisodeResolution;
};
type NetflixSeason = { season_number: number; represented: number; total_released: number | null };
type NetflixItem = {
  id: string | number;
  kind: "movie" | "show";
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
  episodes?: NetflixEpisode[];
  seasons?: NetflixSeason[];
  decision: { action: "confirm" | "remap" | "skip" | null };
  outcome: {
    status: "completed" | "partial" | "skip";
    latest_season?: number | null;
    latest_episode?: number | null;
    tracking_status?: "watching" | "paused" | "dropped";
  };
};

type NetflixSession = {
  id: string;
  status: "preparing" | "review" | "ready" | "committed" | "cancelled" | "failed" | string;
  phase: string;
  progress: { current: number; total: number; message: string };
  revision: number;
  items: NetflixItem[];
  summary: Record<string, unknown> | null;
  errors?: (string | { message?: string; row?: number })[];
};

type SearchResult = MatchCandidate & { id?: number | null; type?: string; title: string };

function mountNetflixImport() {
  const app = document.getElementById("netflix-import-app");
  if (!app || app.dataset.netflixInitialized === "true") return;
  app.dataset.netflixInitialized = "true";
  const upload = document.getElementById("netflix-import-upload") as HTMLDivElement;
  const fileInput = document.getElementById("netflix-import-file") as HTMLInputElement;
  const languageInput = document.getElementById("netflix-import-language") as HTMLSelectElement;
  const workflow = document.getElementById("netflix-import-workflow") as HTMLElement;
  const errorBox = document.getElementById("netflix-import-error") as HTMLElement;
  const dialog = document.getElementById("netflix-import-dialog") as HTMLDialogElement;
  const dialogErrorBox = document.getElementById("netflix-import-dialog-error") as HTMLElement;
  const token = document.getElementById("app-data")?.getAttribute("data-token") ?? "";
  const hasTmdbKey = app.getAttribute("data-has-tmdb-key") === "true";

  let session: NetflixSession | null = null;
  let step = 1;
  let pollTimer: number | undefined;
  let saving = false;
  let showingCompleted = false;
  let searchingItemId: string | number | null = null;
  let searchResults: SearchResult[] = [];
  let searchError = "";
  let saveQueue: Promise<void> = Promise.resolve();
  let idempotencyKey = "";
  let busy = false;
  let uploadController: AbortController | null = null;
  const esc = (value: unknown) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]!);

  function posterSrc(path?: string | null): string {
    if (!path) return "";
    if (path.startsWith("/api/proxy/media/image/") || path.startsWith("/api/proxy/media/rating-poster/")) return path;
    if (path.startsWith("/")) return `/api/proxy/media/image/w342/${path.replace(/^\/+/, "")}`;
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
    const target = dialog.open ? dialogErrorBox : errorBox;
    target.textContent = message;
    target.classList.remove("hidden");
  }

  function clearError() {
    for (const target of [errorBox, dialogErrorBox]) {
      target.textContent = "";
      target.classList.add("hidden");
    }
  }

  function openDialog() {
    if (!dialog.open) dialog.showModal();
  }

  function closeDialog() {
    if (dialog.open) dialog.close();
  }

  dialog.addEventListener("cancel", (event) => event.preventDefault());

  function summaryNumber(...keys: string[]): number {
    const summary = session?.summary ?? {};
    for (const key of keys) {
      const value = summary[key];
      if (typeof value === "number") return value;
    }
    return 0;
  }

  function seasonSummary(item: NetflixItem): string {
    const seasons = (item.seasons ?? []).filter((season) => season.season_number > 0);
    if (!seasons.length) return "Episode positions unavailable";
    return seasons.map((season) => `S${season.season_number} ${season.represented}/${season.total_released ?? "?"}`).join(" · ");
  }

  function episodeResolutionDetails(item: NetflixItem): string {
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
      const title = episode.source_episode_title || episode.source_title || episode.title || "Netflix episode";
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

    return `<details class="mt-3 rounded-lg border border-zinc-800 bg-zinc-950/40"><summary class="cursor-pointer px-3 py-2 text-xs font-semibold text-zinc-300">Episode resolution · ${esc(counts)}</summary><ul class="max-h-64 space-y-1.5 overflow-auto border-t border-zinc-800 p-2">${rows}</ul></details>`;
  }

  function isIncomplete(item: NetflixItem): boolean {
    const seasons = (item.seasons ?? []).filter((season) => season.season_number > 0 && season.total_released > 0);
    const unknownSeasons = (item.seasons ?? []).filter((season) => season.season_number > 0 && season.total_released == null);
    if (unknownSeasons.length) return true;
    if (seasons.length) return seasons.some((season) => season.represented < (season.total_released ?? 0));
    return true;
  }

  function reviewItems(): NetflixItem[] {
    return (session?.items ?? []).filter((item) => {
      if (item.decision?.action === "skip" || item.decision?.action === "confirm" || item.decision?.action === "remap") return false;
      return item.match?.state !== "matched" || item.match?.confidence !== "high" || !item.match?.candidate;
    });
  }

  function titleReviewComplete(): boolean {
    return reviewItems().length === 0;
  }

  function autoMatchedItems(): NetflixItem[] {
    return (session?.items ?? []).filter((item) =>
      item.match?.state === "matched" && item.match?.confidence === "high" && !!item.match?.candidate && item.decision?.action === "confirm",
    );
  }

  function itemCard(item: NetflixItem, needsReview: boolean): string {
    const candidate = item.match?.candidate;
    const poster = posterSrc(candidate?.poster_path);
    const candidateTitle = candidate ? `${candidate.title}${candidate.year ? ` (${candidate.year})` : ""}` : "No confident match found";
    const reasons = item.match?.reason || (item.match?.state === "unmatched" ? "No matching title was found." : "This match needs a quick check.");
    const badge = item.decision?.action === "skip" ? "Skipped" : item.decision?.action === "remap" ? "Remapped" : needsReview ? "Review needed" : "High confidence";
    const badgeStyle = item.decision?.action === "skip" ? "border-zinc-700 bg-zinc-800 text-zinc-300" : needsReview ? "border-amber-400/30 bg-amber-400/10 text-amber-200" : "border-emerald-400/20 bg-emerald-400/5 text-emerald-300";
    const candidateOptions = (item.match?.candidates ?? []).filter((option) => option.tmdb_id && option.tmdb_id !== candidate?.tmdb_id).slice(0, 3);
    const candidatesHtml = candidateOptions.map((option) => `
      <button type="button" data-netflix-action="candidate" data-item-id="${esc(item.id)}" data-tmdb-id="${esc(option.tmdb_id)}" data-media-type="${esc(option.media_type)}" class="flex w-full items-center gap-3 rounded-xl border border-zinc-800 p-2 text-left transition-colors hover:border-zinc-600 hover:bg-zinc-800/50">
        ${option.poster_path ? `<img loading="lazy" src="${esc(posterSrc(option.poster_path))}" alt="" class="h-12 w-9 shrink-0 rounded object-cover">` : `<span class="h-12 w-9 shrink-0 rounded bg-zinc-800"></span>`}
        <span class="min-w-0"><strong class="block truncate text-sm text-zinc-200">${esc(option.title)}</strong><small class="text-xs text-zinc-500">${esc(option.year ?? "")}</small></span>
      </button>`).join("");

    return `<article class="rounded-2xl border ${needsReview ? "border-amber-500/25 bg-amber-500/[0.035]" : "border-zinc-800 bg-zinc-900/50"} p-4 sm:p-5" data-netflix-item="${esc(item.id)}">
      <div class="flex flex-col gap-4 sm:flex-row">
        <div class="flex min-w-0 flex-1 gap-3">
          ${poster ? `<img loading="lazy" src="${esc(poster)}" alt="Poster for ${esc(candidateTitle)}" class="h-20 w-14 shrink-0 rounded-lg bg-zinc-800 object-cover">` : `<div class="flex h-20 w-14 shrink-0 items-center justify-center rounded-lg bg-zinc-800 text-lg font-bold text-zinc-500">${esc((candidate?.title || item.source_title).slice(0, 1).toUpperCase())}</div>`}
          <div class="min-w-0">
            <p class="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">Netflix title</p>
            <p class="break-words font-semibold text-zinc-100">${esc(item.source_title)}</p>
            <p class="mt-1 text-xs text-zinc-500">${item.kind === "show" ? "Series" : "Movie"}${item.source_dates?.length ? ` · ${esc(item.source_dates[0])}` : ""}</p>
            <p class="mt-2 text-xs text-zinc-400">Suggested catalogue match: <span class="font-semibold text-zinc-200">${esc(candidateTitle)}</span></p>
            <div class="mt-2 flex flex-wrap items-center gap-2">
              <span class="rounded-full border px-2 py-0.5 text-xs ${badgeStyle}">${badge}</span>
              <span class="text-xs text-zinc-500">${esc(reasons)}</span>
            </div>
            ${item.kind === "show" ? `<p class="mt-2 text-xs text-zinc-400">${esc(seasonSummary(item))}</p>${episodeResolutionDetails(item)}` : ""}
          </div>
        </div>
        <div class="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
          ${candidate ? `<button type="button" data-netflix-action="confirm" data-item-id="${esc(item.id)}" class="rounded-lg bg-emerald-600 px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-emerald-500">Confirm ${item.kind === "show" ? "show" : "movie"}</button>` : ""}
          <button type="button" data-netflix-action="open-search" data-item-id="${esc(item.id)}" class="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm font-semibold text-zinc-200 transition-colors hover:bg-zinc-700">Remap</button>
          <button type="button" data-netflix-action="skip-match" data-item-id="${esc(item.id)}" class="rounded-lg px-3 py-2 text-sm font-semibold text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200">Skip</button>
        </div>
      </div>
      ${searchingItemId === item.id ? `<div class="mt-4 border-t border-zinc-800 pt-4">
        <label class="mb-2 block text-sm font-medium text-zinc-300" for="netflix-search-${esc(item.id)}">Search movies and shows</label>
        <div class="flex flex-col gap-2 sm:flex-row"><input id="netflix-search-${esc(item.id)}" data-netflix-search-input="${esc(item.id)}" value="${esc(item.source_title)}" type="search" minlength="2" placeholder="Search title" class="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-blue-500"><button type="button" data-netflix-action="search" data-item-id="${esc(item.id)}" class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-500">Search</button></div>
        ${searchError ? `<p class="mt-2 text-sm text-red-300">${esc(searchError)}</p>` : ""}
        ${searchResults.length ? `<div class="mt-3 grid gap-2 sm:grid-cols-2">${searchResults.map((result) => `<button type="button" data-netflix-action="search-result" data-item-id="${esc(item.id)}" data-tmdb-id="${esc(result.tmdb_id)}" data-media-type="${esc(result.media_type)}" class="flex min-w-0 items-center gap-3 rounded-xl border border-zinc-800 p-2 text-left transition-colors hover:border-zinc-600 hover:bg-zinc-800/50">
          ${result.poster_path ? `<img loading="lazy" src="${esc(posterSrc(result.poster_path))}" alt="" class="h-14 w-10 shrink-0 rounded object-cover">` : `<span class="h-14 w-10 shrink-0 rounded bg-zinc-800"></span>`}
          <span class="min-w-0"><strong class="block truncate text-sm text-zinc-200">${esc(result.title)}</strong><small class="text-xs text-zinc-500">${esc(result.year ?? (result as any).release_date ?? "")}${result.media_type === "show" ? " · Series" : " · Movie"}</small></span>
        </button>`).join("")}</div>` : ""}
      </div>` : ""}
    </article>`;
  }

  function outcomeButtons(item: NetflixItem): string {
    return ([
      ["completed", "Completed"], ["partial", "Partial"], ["skip", "Skip"],
    ] as const).map(([value, label]) => `<button type="button" data-netflix-action="outcome" data-item-id="${esc(item.id)}" data-status="${value}" aria-pressed="${item.outcome.status === value}" class="flex-1 rounded-lg px-2 py-2 text-xs font-semibold transition-colors sm:text-sm ${item.outcome.status === value ? "bg-blue-600 text-white" : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"}">${label}</button>`).join("");
  }

  function partialControls(item: NetflixItem): string {
    if (item.outcome.status !== "partial") return "";
    const seasons = (item.seasons ?? []).filter((season) => season.season_number > 0 && season.total_released > 0).sort((a, b) => a.season_number - b.season_number);
    if (!seasons.length) return `<p class="mt-3 text-xs text-amber-200">Episode details are not available for this show yet. Choose Skip or wait for the catalogue to finish loading.</p>`;
    const currentSeason = seasons.find((season) => season.season_number === item.outcome.latest_season) ?? seasons[seasons.length - 1];
    const season = currentSeason.season_number;
    const total = currentSeason.total_released;
    const episode = Math.max(1, Math.min(total, item.outcome.latest_episode ?? total));
    const seasonOptions = seasons.map((meta) => `<option value="${meta.season_number}" ${meta.season_number === season ? "selected" : ""}>Season ${meta.season_number}</option>`).join("");
    const episodeOptions = Array.from({ length: Math.min(total, 100) }, (_, i) => i + 1).map((n) => `<option value="${n}" ${n === episode ? "selected" : ""}>Episode ${n}</option>`).join("");
    const tracking = item.outcome.tracking_status ?? "watching";
    return `<div class="grid gap-3 sm:grid-cols-3">
      <label class="min-w-0 text-xs font-medium text-zinc-400"><span class="flex min-h-9 items-end">Latest watched season</span><select data-netflix-change="latest-season" data-item-id="${esc(item.id)}" class="mt-1 w-full min-w-0 rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-2 text-sm text-zinc-100">${seasonOptions}</select></label>
      <label class="min-w-0 text-xs font-medium text-zinc-400"><span class="flex min-h-9 items-end">Latest watched episode</span><select data-netflix-change="latest-episode" data-item-id="${esc(item.id)}" class="mt-1 w-full min-w-0 rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-2 text-sm text-zinc-100">${episodeOptions}</select></label>
      <label class="min-w-0 text-xs font-medium text-zinc-400"><span class="flex min-h-9 items-end">Tracking status</span><select data-netflix-change="tracking-status" data-item-id="${esc(item.id)}" class="mt-1 w-full min-w-0 rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-2 text-sm text-zinc-100"><option value="watching" ${tracking === "watching" ? "selected" : ""}>Watching</option><option value="paused" ${tracking === "paused" ? "selected" : ""}>Paused</option><option value="dropped" ${tracking === "dropped" ? "selected" : ""}>Dropped</option></select></label>
    </div>`;
  }

  function showCard(item: NetflixItem): string {
    const candidate = item.match.candidate;
    const poster = posterSrc(candidate?.poster_path);
    const seasons = (item.seasons ?? []).filter((season) => season.season_number > 0);
    return `<article class="flex h-full min-w-0 flex-col gap-3 rounded-2xl border border-zinc-800 bg-zinc-900/50 p-4 lg:row-span-3 lg:grid lg:[grid-template-rows:subgrid]" data-netflix-item="${esc(item.id)}">
      <div class="flex gap-3">
        ${poster ? `<img loading="lazy" src="${esc(poster)}" alt="Poster for ${esc(candidate?.title ?? item.source_title)}" class="h-24 w-16 shrink-0 rounded-lg bg-zinc-800 object-cover">` : `<div class="h-24 w-16 shrink-0 rounded-lg bg-zinc-800"></div>`}
        <div class="min-w-0 flex-1">
          <h3 class="break-words font-semibold text-zinc-100">${esc(candidate?.title ?? item.source_title)}</h3>
          ${seasons.length ? `<div class="mt-2 flex flex-wrap gap-1.5">${seasons.map((season) => `<span class="rounded-md bg-zinc-800 px-2 py-1 text-[11px] text-zinc-400">S${season.season_number}: ${season.represented}/${season.total_released ?? "?"}</span>`).join("")}</div>` : ""}
          ${episodeResolutionDetails(item)}
        </div>
      </div>
      <div class="flex rounded-xl border border-zinc-800 bg-zinc-950 p-1" role="group" aria-label="Import progress for ${esc(candidate?.title ?? item.source_title)}">${outcomeButtons(item)}</div>
      <div>${item.outcome.status === "skip" ? `<p class="text-xs text-zinc-500">Skip leaves this show and any existing AnyList progress untouched.</p>` : partialControls(item)}</div>
    </article>`;
  }

  function progressView(): string {
    const showItems = (session?.items ?? []).filter((item) => item.kind === "show" && item.decision?.action !== "skip");
    const incomplete = showItems.filter(isIncomplete);
    const visible = (showingCompleted ? showItems : incomplete).slice().sort((a, b) =>
      (a.match.candidate?.title ?? a.source_title).localeCompare(b.match.candidate?.title ?? b.source_title),
    );
    const completedCount = showItems.length - incomplete.length;
    const partialCount = incomplete.filter((item) => item.outcome.status === "partial").length;
    return `<div class="space-y-4">
      <div><p class="text-xs font-bold uppercase tracking-[0.16em] text-blue-300">Step 3 of 4 · Review show progress</p><h2 class="mt-1 text-xl font-bold text-zinc-100">Choose how each show should appear in AnyList</h2><p class="mt-1 text-sm text-zinc-400">Episode counts show what the export represents. Partial progress fills episodes through your selected episode.</p></div>
      <div class="rounded-xl border border-blue-500/20 bg-blue-500/5 px-4 py-3"><p class="text-sm font-semibold text-blue-100">Partial progress</p><p class="mt-1 text-sm text-zinc-300">${incomplete.length} show${incomplete.length === 1 ? "" : "s"} have episodes missing from the export. Partial progress gets automatically filled until the latest watched episode for a show.</p></div>
      <label class="flex cursor-pointer items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-3 text-sm text-zinc-300"><input type="checkbox" data-netflix-change="show-completed" ${showingCompleted ? "checked" : ""} class="h-4 w-4 rounded border-zinc-600 bg-zinc-950 text-blue-600 focus:ring-blue-500"><span>Show fully represented shows <span class="text-zinc-500">(${completedCount})</span></span></label>
      ${visible.length ? `<div class="grid gap-3 lg:grid-cols-2">${visible.map(showCard).join("")}</div>` : `<div class="rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-8 text-center text-sm text-zinc-400">${showItems.length ? "All shows are represented through their latest released episode. Use the control above to inspect them." : "No matched shows in this export."}</div>`}
      <p class="text-xs text-zinc-500">${partialCount} show${partialCount === 1 ? "" : "s"} currently set to Partial. You can change any choice before importing.</p>
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
      <div><p class="text-xs font-bold uppercase tracking-[0.16em] text-blue-300">Step 4 of 4 · Final review</p><h2 class="mt-1 text-xl font-bold text-zinc-100">Ready to import</h2><p class="mt-1 text-sm text-zinc-400">Review what will change in your AnyList account. Nothing is applied until you select Import.</p></div>
      <div class="space-y-3">
        ${rows.map(([label, value]) => `<div class="flex items-center justify-between gap-6 py-3"><span class="text-base text-zinc-300">${label}</span><strong class="text-2xl tabular-nums text-zinc-100">${value.toLocaleString()}</strong></div>`).join("")}
      </div>
      <p class="text-xs text-zinc-500">Episode total includes the positions filled by your final Partial and Completed choices.</p>
      ${errors ? `<p class="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-sm text-amber-200">${errors} CSV row${errors === 1 ? "" : "s"} could not be read.</p>` : ""}
    </div>`;
  }

  function stepHeader(): string {
    const labels = ["Upload", "Review matches", "Show progress", "Import"];
    return `<div class="mb-5 flex flex-wrap items-center gap-2">${labels.map((label, index) => `<div class="flex items-center gap-2"><span class="flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${step === index + 1 ? "bg-blue-600 text-white" : step > index + 1 ? "bg-emerald-500/20 text-emerald-300" : "bg-zinc-800 text-zinc-500"}">${step > index + 1 ? "✓" : index + 1}</span><span class="text-xs ${step === index + 1 ? "font-semibold text-zinc-200" : "text-zinc-500"}">${label}</span></div>${index < labels.length - 1 ? `<span class="h-px min-w-4 flex-1 bg-zinc-800"></span>` : ""}`).join("")}</div>`;
  }

  function render() {
    if (!session) {
      closeDialog();
      workflow.classList.add("hidden");
      upload.classList.remove("hidden");
      return;
    }
    openDialog();
    workflow.classList.remove("hidden");
    upload.classList.add("hidden");
    const isPreparing = session.status === "preparing";
    const isCommitted = session.status === "committed";
    const isFailed = session.status === "failed";
    let content = "";
    if (isCommitted) {
      step = 4;
      content = `<div class="rounded-2xl border border-emerald-500/25 bg-emerald-500/5 p-5"><p class="text-xs font-bold uppercase tracking-[0.16em] text-emerald-300">Import complete</p><h2 class="mt-1 text-xl font-bold text-zinc-100">Netflix history added</h2><p class="mt-2 text-sm text-zinc-300">Your reviewed movies and show progress have been added to AnyList.</p></div>`;
    } else if (isFailed) {
      content = `<div class="rounded-xl border border-red-500/25 bg-red-500/5 p-4"><h2 class="font-semibold text-red-200">We couldn't prepare this CSV</h2><p class="mt-1 text-sm text-zinc-400">${esc(session.errors?.map((error) => typeof error === "string" ? error : error.message).filter(Boolean).join(" · ") || session.progress?.message || "Try downloading a new copy of your Netflix viewing history.")}</p><button type="button" data-netflix-action="restart" class="mt-3 rounded-lg border border-zinc-700 px-3 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800">Choose another file</button></div>`;
    } else if (isPreparing) {
      const total = Math.max(0, session.progress?.total ?? 0);
      const current = Math.max(0, session.progress?.current ?? 0);
      const pct = total > 0 ? Math.max(0, Math.min(100, Math.round(current * 100 / total))) : 0;
      content = `<div class="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-4 sm:p-6"><p class="text-xs font-bold uppercase tracking-[0.16em] text-blue-300">Step 1 of 4 · Preparing import</p><h2 class="mt-1 text-lg font-bold text-zinc-100">${esc(session.progress?.message || (session.phase === "parse" ? "Reading your CSV…" : "Matching titles and resolving episode positions…"))}</h2><div class="mt-4 h-2 overflow-hidden rounded-full bg-zinc-800"><div class="h-full rounded-full bg-blue-500 transition-all" style="width:${pct}%"></div></div><div class="mt-2 flex justify-between gap-3 text-xs text-zinc-500"><span>${total ? `${current.toLocaleString()} of ${total.toLocaleString()} items` : "Getting started"}</span><span>${total ? `${pct}%` : esc(session.phase)}</span></div><p class="mt-3 text-xs text-zinc-500">Duplicate rows are grouped before matching so the same title is only looked up once.</p></div>`;
    } else if (step === 2) {
      const needing = reviewItems();
      const auto = autoMatchedItems();
      const skipped = (session?.items ?? []).filter((item) => item.decision?.action === "skip");
      content = `<div class="space-y-4"><div><p class="text-xs font-bold uppercase tracking-[0.16em] text-blue-300">Step 2 of 4 · Review title matches</p><h2 class="mt-1 text-xl font-bold text-zinc-100">Confirm anything uncertain</h2><p class="mt-1 text-sm text-zinc-400">High confidence show and movie matches are preconfirmed. Check any title that does not look right and remap or skip it.</p></div>
        ${needing.length ? `<div class="space-y-3">${needing.map((item) => itemCard(item, true)).join("")}</div>` : `<div class="rounded-xl border border-emerald-500/20 bg-emerald-500/5 px-4 py-3 text-sm text-emerald-200">All show and movie titles are resolved. Episode positions will be assigned automatically where the catalogue permits.</div>`}
        ${auto.length ? `<details class="rounded-xl border border-zinc-800 bg-zinc-900/30"><summary class="cursor-pointer px-4 py-3 text-sm font-semibold text-zinc-300">${auto.length} high confidence match${auto.length === 1 ? "" : "es"} accepted automatically</summary><div class="space-y-3 border-t border-zinc-800 p-3">${auto.map((item) => itemCard(item, false)).join("")}</div></details>` : ""}
        ${skipped.length ? `<details class="rounded-xl border border-zinc-800 bg-zinc-900/30"><summary class="cursor-pointer px-4 py-3 text-sm font-semibold text-zinc-400">${skipped.length} title${skipped.length === 1 ? "" : "s"} skipped · reopen to change</summary><div class="space-y-3 border-t border-zinc-800 p-3">${skipped.map((item) => itemCard(item, false)).join("")}</div></details>` : ""}
      </div>`;
    } else if (step === 3) {
      content = progressView();
    } else {
      content = summaryView();
    }

    const nav = isPreparing
      ? `<button type="button" data-netflix-action="cancel" class="rounded-lg px-3 py-2 text-sm font-semibold text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">Cancel</button>`
      : isFailed
      ? `<button type="button" data-netflix-action="cancel" class="rounded-lg px-3 py-2 text-sm font-semibold text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">Cancel</button>`
      : `<div class="flex w-full flex-col-reverse gap-2 sm:w-auto sm:flex-row"><button type="button" data-netflix-action="cancel" class="rounded-lg px-3 py-2 text-sm font-semibold text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">Cancel import</button>${step > 2 ? `<button type="button" data-netflix-action="back" class="rounded-lg border border-zinc-700 px-4 py-2 text-sm font-semibold text-zinc-200 hover:bg-zinc-800">Back</button>` : ""}${step < 4 ? `<button type="button" data-netflix-action="next" ${step === 2 && !titleReviewComplete() ? "disabled" : ""} class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-40">Continue</button>` : `<button type="button" data-netflix-action="commit" ${busy || saving ? "disabled" : ""} class="rounded-lg bg-emerald-600 px-5 py-2 text-sm font-bold text-white transition-colors hover:bg-emerald-500 disabled:cursor-wait disabled:opacity-50">${busy ? "Importing…" : "Import"}</button>`}</div>`;
    workflow.innerHTML = `${!isCommitted ? stepHeader() : ""}<div class="space-y-5">${content}<div class="flex flex-col-reverse items-stretch justify-between gap-3 border-t border-zinc-800 pt-4 sm:flex-row sm:items-center">${isPreparing ? `<span class="text-xs text-zinc-500">Matching in progress</span>` : ""}${!isCommitted ? nav : `<button type="button" data-netflix-action="restart" class="self-start rounded-lg bg-blue-600 px-4 py-2 text-sm font-bold text-white hover:bg-blue-500 sm:self-auto">Import another file</button>`}</div></div>`;
  }

  function byId(id: string | number): NetflixItem | undefined {
    return session?.items.find((item) => String(item.id) === String(id));
  }

  function saveItem(item: NetflixItem, changes: Record<string, unknown>, decision = false): Promise<void> {
    if (!session) return Promise.resolve();
    Object.assign(decision ? item.decision : item.outcome, changes);
    render();
    saving = true;
    render();
    saveQueue = saveQueue.then(async () => {
      if (!session) return;
      const body = { revision: session.revision, ...changes };
      const updated = await request<NetflixSession>(`/imports/${encodeURIComponent(session.id)}/items/${encodeURIComponent(String(item.id))}`, {
        method: "PATCH", body: JSON.stringify(body),
      });
      if (updated?.id) session = updated;
    }).catch(async (error) => {
      if (session) {
        try { session = await request<NetflixSession>(`/imports/${encodeURIComponent(session.id)}`); }
        catch { /* Keep the current draft visible if refresh also fails. */ }
      }
      showError(error instanceof Error ? error.message : "Could not save this choice. Please try again.");
    }).finally(() => {
      saving = false;
      render();
    });
    return saveQueue;
  }

  async function poll() {
    if (!session || session.status !== "preparing") return;
    try {
      const updated = await request<NetflixSession>(`/imports/${encodeURIComponent(session.id)}`);
      session = updated;
      if (session.status === "preparing") {
        render();
        pollTimer = window.setTimeout(poll, 1000);
        return;
      }
      if (session.status === "review" || session.status === "ready") {
        step = 2;
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
    if (!file.name.toLowerCase().endsWith(".csv")) {
      showError("Choose the .csv file downloaded from Netflix viewing activity.");
      return;
    }
    clearError();
    busy = true;
    upload.classList.add("hidden");
    workflow.classList.remove("hidden");
    openDialog();
    step = 1;
    workflow.innerHTML = `<div class="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5"><div class="flex items-center justify-between gap-3"><p class="text-sm font-semibold text-zinc-200">Uploading ${esc(file.name)}…</p><button type="button" data-netflix-action="cancel-upload" class="rounded-lg px-3 py-2 text-sm font-semibold text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">Cancel</button></div><div class="mt-3 h-2 overflow-hidden rounded-full bg-zinc-800"><div class="h-full w-1/3 animate-pulse rounded-full bg-blue-500"></div></div></div>`;
    const form = new FormData();
    form.append("file", file);
    if (languageInput.value !== "auto") form.append("language", languageInput.value);
    uploadController = new AbortController();
    try {
      session = await request<NetflixSession>("/imports/netflix", { method: "POST", body: form, signal: uploadController.signal });
      step = 1;
      render();
      if (session.status === "preparing") pollTimer = window.setTimeout(poll, 250);
      else if (session.status === "review" || session.status === "ready") { step = 2; render(); }
      else render();
    } catch (error) {
      upload.classList.remove("hidden");
      workflow.classList.add("hidden");
      closeDialog();
      if (!(error instanceof DOMException && error.name === "AbortError")) showError(error instanceof Error ? error.message : "Could not upload the Netflix CSV.");
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
    closeDialog();
    upload.classList.remove("hidden");
    fileInput.value = "";
  }

  async function searchTitles(item: NetflixItem) {
    const input = document.querySelector<HTMLInputElement>(`[data-netflix-search-input="${CSS.escape(String(item.id))}"]`);
    const query = input?.value.trim() ?? "";
    if (query.length < 2) { searchError = "Enter at least two characters."; render(); return; }
    searchError = "";
    searchResults = [];
    render();
    try {
      const params = new URLSearchParams({ q: query, type: item.kind === "movie" ? "movie" : "series" });
      const result = await request<{ results: SearchResult[] }>(`/media/search?${params}`);
      searchResults = (result.results ?? []).filter((candidate) => candidate.tmdb_id).slice(0, 8).map((candidate) => ({
        ...candidate,
        media_type: candidate.media_type ?? (candidate.type === "movie" ? "movie" : "show"),
        year: candidate.year ?? (candidate as any).release_date?.slice?.(0, 4),
      }));
      if (!searchResults.length) searchError = "No results found. Try another title.";
    } catch (error) {
      searchError = error instanceof Error ? error.message : "Search failed.";
    }
    render();
  }

  async function cancelSession() {
    if (!session) return;
    if (pollTimer) window.clearTimeout(pollTimer);
    try { await request(`/imports/${encodeURIComponent(session.id)}`, { method: "DELETE" }); }
    catch (error) { showError(error instanceof Error ? error.message : "Could not cancel the import."); return; }
    session = null;
    step = 1;
    showingCompleted = false;
    searchingItemId = null;
    searchResults = [];
    clearError();
    render();
  }

  async function commit() {
    if (!session || busy) return;
    await saveQueue;
    busy = true;
    clearError();
    render();
    idempotencyKey ||= crypto.randomUUID();
    try {
      session = await request<NetflixSession>(`/imports/${encodeURIComponent(session.id)}/commit`, {
        method: "POST", body: JSON.stringify({ revision: session.revision, idempotency_key: idempotencyKey }),
      });
      if (session.status === "committing") {
        workflow.innerHTML = `<div class="rounded-2xl border border-emerald-500/20 bg-emerald-500/5 p-5"><p class="font-semibold text-emerald-200">Importing reviewed changes…</p><p class="mt-2 text-sm text-zinc-400">Please keep this page open while AnyList saves your watch history.</p><button type="button" data-netflix-action="cancel" class="mt-4 rounded-lg px-3 py-2 text-sm font-semibold text-zinc-300 hover:bg-zinc-800">Cancel import</button></div>`;
        const waitForCommit = async () => {
          if (!session) return;
          try {
            session = await request<NetflixSession>(`/imports/${encodeURIComponent(session.id)}`);
            if (session.status !== "committing") {
              busy = false;
              if (session.status === "review") {
                step = 4;
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
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-netflix-action]");
    if (!button) return;
    const action = button.dataset.netflixAction;
    const id = button.dataset.itemId;
    const item = id ? byId(id) : undefined;
    clearError();
    if (action === "cancel-upload") { cancelUpload(); return; }
    if (action === "cancel") { await cancelSession(); return; }
    if (action === "restart") { session = null; step = 1; clearError(); render(); return; }
    if (action === "back") { step = Math.max(2, step - 1); render(); dialog.querySelector(".overflow-y-auto")?.scrollTo(0, 0); return; }
    if (action === "next") {
      if (step === 2) {
        if (!titleReviewComplete()) { showError("Resolve each uncertain show or movie title before continuing."); return; }
        step = 3;
      } else if (step === 3) {
        await saveQueue;
        step = 4;
      }
      render();
      dialog.querySelector(".overflow-y-auto")?.scrollTo(0, 0);
      return;
    }
    if (action === "commit") { await commit(); return; }
    if (!item) return;
    if (action === "confirm") { await saveItem(item, { action: "confirm" }, true); return; }
    if (action === "skip-match") { await saveItem(item, { action: "skip" }, true); return; }
    if (action === "open-search") {
      searchingItemId = searchingItemId === item.id ? null : item.id;
      searchResults = [];
      searchError = "";
      render();
      document.querySelector<HTMLInputElement>(`[data-netflix-search-input="${CSS.escape(String(item.id))}"]`)?.focus();
      return;
    }
    if (action === "search") { await searchTitles(item); return; }
    if (action === "candidate" || action === "search-result") {
      const tmdbId = Number(button.dataset.tmdbId);
      const mediaType = button.dataset.mediaType === "movie" ? "movie" : "show";
      if (!tmdbId) return;
      await saveItem(item, { action: "remap", media_type: mediaType, tmdb_id: tmdbId }, true);
      searchingItemId = null;
      searchResults = [];
      render();
      return;
    }
    if (action === "outcome") {
      const status = button.dataset.status as NetflixItem["outcome"]["status"];
      await saveItem(item, { status });
    }
  });

  workflow.addEventListener("change", (event) => {
    const input = event.target as HTMLInputElement | HTMLSelectElement;
    const action = input.dataset.netflixChange;
    const id = input.dataset.itemId;
    if (action === "show-completed") { showingCompleted = (input as HTMLInputElement).checked; render(); return; }
    const item = id ? byId(id) : undefined;
    if (!item) return;
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

  workflow.addEventListener("keydown", (event) => {
    const input = event.target as HTMLInputElement;
    if (event.key === "Enter" && input.matches("[data-netflix-search-input]")) {
      event.preventDefault();
      const item = byId(input.dataset.netflixSearchInput!);
      if (item) void searchTitles(item);
    }
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

document.addEventListener("astro:page-load", mountNetflixImport);
mountNetflixImport();
