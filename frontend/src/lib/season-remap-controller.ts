import { escapeHtml as esc } from './settings-feedback.ts';
import { applyResponsiveArtwork } from './responsive-artwork.ts';
import { showOverlay, hideOverlay } from './ui-motion.ts';

function applyResponsiveImages(root: ParentNode) {
  root.querySelectorAll<HTMLImageElement>('img[data-artwork-path]').forEach(img => {
    applyResponsiveArtwork(img, img.dataset.artworkPath, {
      size: 'w185',
      sizes: '32px',
      route: 'direct',
    });
  });
}

export function mountSeasonRemap(token: string, onSaved: () => void) {
  const modal = document.getElementById('season-remap-modal') as HTMLElement;
  if (!modal) return;

  const lifetime = new AbortController();
  // Searches end with the page; an already-started save still finishes applying.
  const request = (url: string, options: RequestInit) => fetch(url, {
    ...options,
    ...(options.method && options.method !== 'GET' ? {} : { signal: lifetime.signal }),
  });
  let srcTmdbId = 0;
  let srcSeason = 0;
  let targetTmdbId = 0;
  let targetTvdbId = 0;
  // null = nothing picked yet, distinct from a legitimately-selected Season 0
  // (TVDB stores specials/OVAs there for many anime - see #363). 0 is falsy
  // in JS, so a plain `!targetSeasonNumber` check would reject it even once
  // it's selected.
  let targetSeasonNumber: number | null = null;
  let modalMode: 'remap' | 'match' | 'match-movie' = 'remap';
  let matchSeriesName = '';
  let matchMovieTitle = '';

  const closeModal = () => {
    if (lifetime.signal.aborted) return;
    void hideOverlay(modal);
    (modal.querySelector<HTMLInputElement>('#remap-search-input') as HTMLInputElement).value = '';
    (modal.querySelector<HTMLInputElement>('#remap-tvdb-input') as HTMLInputElement).value = '';
    (modal.querySelector('#remap-search-results') as HTMLElement).innerHTML = '';
    (modal.querySelector('#remap-tvdb-results') as HTMLElement).innerHTML = '';
    (modal.querySelector('#remap-seasons-list') as HTMLElement).innerHTML = '';
    (modal.querySelector('#remap-selected-area') as HTMLElement).classList.add('hidden');
    (modal.querySelector('#match-tab-bar') as HTMLElement).classList.add('hidden');
    (modal.querySelector<HTMLButtonElement>('#remap-save') as HTMLButtonElement).disabled = true;
    (modal.querySelector('#remap-error') as HTMLElement).classList.add('hidden');
    targetTmdbId = 0;
    targetTvdbId = 0;
    targetSeasonNumber = null;
    modalMode = 'remap';
    matchSeriesName = '';
    matchMovieTitle = '';
  };

  const setMatchTab = (tab: 'tvdb' | 'tmdb') => {
    const tvdbBtn = modal.querySelector<HTMLButtonElement>('#match-tab-tvdb') as HTMLElement;
    const tmdbBtn = modal.querySelector<HTMLButtonElement>('#match-tab-tmdb') as HTMLElement;
    const tvdbSection = modal.querySelector('#remap-tvdb-section') as HTMLElement;
    const tmdbSection = modal.querySelector('#remap-tmdb-section') as HTMLElement;
    const selectedArea = modal.querySelector('#remap-selected-area') as HTMLElement;
    const saveBtn = modal.querySelector<HTMLButtonElement>('#remap-save') as HTMLButtonElement;
    // Reset selection
    selectedArea.classList.add('hidden');
    saveBtn.disabled = true;
    targetTmdbId = 0;
    targetTvdbId = 0;
    if (tab === 'tvdb') {
      tvdbBtn.className = 'flex-1 px-3 py-1.5 text-sm font-medium rounded-lg transition-colors cursor-pointer bg-zinc-800 text-zinc-100';
      tmdbBtn.className = 'flex-1 px-3 py-1.5 text-sm font-medium rounded-lg transition-colors cursor-pointer text-zinc-400 hover:text-zinc-200';
      tvdbSection.classList.remove('hidden');
      tmdbSection.classList.add('hidden');
      (modal.querySelector('#remap-tvdb-results') as HTMLElement).innerHTML = '';
      const tvdbInput = modal.querySelector<HTMLInputElement>('#remap-tvdb-input') as HTMLInputElement;
      tvdbInput.focus();
      if (tvdbInput.value.trim()) doTvdbSearch();
    } else {
      tmdbBtn.className = 'flex-1 px-3 py-1.5 text-sm font-medium rounded-lg transition-colors cursor-pointer bg-zinc-800 text-zinc-100';
      tvdbBtn.className = 'flex-1 px-3 py-1.5 text-sm font-medium rounded-lg transition-colors cursor-pointer text-zinc-400 hover:text-zinc-200';
      tmdbSection.classList.remove('hidden');
      tvdbSection.classList.add('hidden');
      (modal.querySelector('#remap-search-results') as HTMLElement).innerHTML = '';
      const tmdbInput = modal.querySelector<HTMLInputElement>('#remap-search-input') as HTMLInputElement;
      tmdbInput.focus();
      if (tmdbInput.value.trim()) doSearch();
    }
  };

  modal.querySelector<HTMLButtonElement>('#match-tab-tvdb')?.addEventListener('click', () => setMatchTab('tvdb'), { signal: lifetime.signal });
  modal.querySelector<HTMLButtonElement>('#match-tab-tmdb')?.addEventListener('click', () => setMatchTab('tmdb'), { signal: lifetime.signal });

  modal.querySelector<HTMLButtonElement>('#remap-close')?.addEventListener('click', closeModal, { signal: lifetime.signal });
  modal.querySelector<HTMLButtonElement>('#remap-cancel')?.addEventListener('click', closeModal, { signal: lifetime.signal });
  modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); }, { signal: lifetime.signal });

  modal.querySelector<HTMLButtonElement>('#remap-clear-selection')?.addEventListener('click', () => {
    (modal.querySelector('#remap-selected-area') as HTMLElement).classList.add('hidden');
    const selectedPoster = modal.querySelector('#remap-selected-poster') as HTMLImageElement;
    selectedPoster.removeAttribute('src');
    selectedPoster.removeAttribute('srcset');
    selectedPoster.removeAttribute('sizes');
    (modal.querySelector('#remap-seasons-list') as HTMLElement).innerHTML = '';
    (modal.querySelector<HTMLButtonElement>('#remap-save') as HTMLButtonElement).disabled = true;
    targetTmdbId = 0;
    targetTvdbId = 0;
    targetSeasonNumber = null;
  }, { signal: lifetime.signal });

  const searchInput = modal.querySelector<HTMLInputElement>('#remap-search-input') as HTMLInputElement;
  const searchBtn = modal.querySelector<HTMLButtonElement>('#remap-search-btn') as HTMLButtonElement;

  const wireResultClick = (btn: HTMLElement) => {
    btn.addEventListener('click', async () => {
      const tmdbId = parseInt(btn.dataset.tmdb ?? '0');
      const tvdbId = parseInt(btn.dataset.tvdb ?? '0');
      if (tvdbId) {
        targetTvdbId = tvdbId;
        targetTmdbId = 0;
      } else {
        targetTmdbId = tmdbId;
        targetTvdbId = 0;
      }
      targetSeasonNumber = null;
      const selectedPoster = modal.querySelector('#remap-selected-poster') as HTMLImageElement;
      const selectedPosterPath = btn.dataset.poster ?? '';
      selectedPoster.removeAttribute('srcset');
      selectedPoster.removeAttribute('sizes');
      if (tvdbId) {
        selectedPoster.src = selectedPosterPath;
      } else {
        applyResponsiveArtwork(selectedPoster, selectedPosterPath, {
          size: 'w185',
          sizes: '40px',
          route: 'direct',
        });
      }
      (modal.querySelector('#remap-selected-title') as HTMLElement).textContent = btn.dataset.title ?? '';
      (modal.querySelector('#remap-selected-year') as HTMLElement).textContent = btn.dataset.year ?? '';
      (modal.querySelector('#remap-selected-area') as HTMLElement).classList.remove('hidden');
      (modal.querySelector('#remap-search-results') as HTMLElement).innerHTML = '';
      (modal.querySelector('#remap-tvdb-results') as HTMLElement).innerHTML = '';
      searchInput.value = '';

      const saveBtn = modal.querySelector<HTMLButtonElement>('#remap-save') as HTMLButtonElement;
      const seasonsArea = modal.querySelector('#remap-seasons-area') as HTMLElement;

      if (modalMode === 'match' || modalMode === 'match-movie') {
        // No season selection needed for show/movie matching — enable save immediately
        seasonsArea.classList.add('hidden');
        saveBtn.disabled = false;
        return;
      }

      // Remap mode: fetch and render season list
      saveBtn.disabled = true;
      seasonsArea.classList.remove('hidden');
      const seasonsList = modal.querySelector('#remap-seasons-list') as HTMLElement;
      const seasonsLoading = modal.querySelector('#remap-seasons-loading') as HTMLElement;
      seasonsList.innerHTML = '';
      seasonsLoading.classList.remove('hidden');
      try {
        const showUrl = tvdbId ? `/api/proxy/shows/tvdb/${tvdbId}` : `/api/proxy/shows/${tmdbId}`;
        const showRes = await request(showUrl, {
          headers: { 'Authorization': `Bearer ${token}` },
        });
        const showData = showRes.ok ? await showRes.json() : null;
        // >= 0, not > 0 - TVDB stores specials/OVAs in Season 0 for a lot of
        // anime, and that's a legitimate remap target (#363).
        const seasons: any[] = (showData?.seasons_meta ?? []).filter((s: any) => s.season_number >= 0);
        seasonsLoading.classList.add('hidden');
        if (!seasons.length) {
          seasonsList.innerHTML = '<p class="text-xs text-zinc-500 py-1">No seasons found.</p>';
          return;
        }
        seasons.forEach((s: any) => {
          const seasonBtn = document.createElement('button');
          seasonBtn.type = 'button';
          seasonBtn.className = 'remap-season-pick w-full flex items-center gap-3 px-3 py-2 rounded-lg border border-transparent hover:border-blue-600/50 hover:bg-blue-600/10 transition-colors cursor-pointer text-left';
          seasonBtn.dataset.season = String(s.season_number);
          seasonBtn.innerHTML = `
            <img data-artwork-path="${esc(s.poster_path ?? '')}" alt="" class="w-8 h-11 object-cover rounded bg-zinc-700 shrink-0" loading="lazy" decoding="async" onerror="this.style.display='none'" />
            <div class="flex-1 min-w-0">
              <p class="text-sm text-zinc-100">${esc(s.name ?? `Season ${s.season_number}`)}</p>
              <p class="text-xs text-zinc-500">Season ${s.season_number}${s.episode_count ? ` · ${s.episode_count} eps` : ''}</p>
            </div>`;
          seasonBtn.addEventListener('click', () => {
            targetSeasonNumber = s.season_number;
            seasonsList.querySelectorAll('.remap-season-pick').forEach(b => {
              b.classList.remove('border-blue-600/50', 'bg-blue-600/10');
              b.classList.add('border-transparent');
            });
            seasonBtn.classList.add('border-blue-600/50', 'bg-blue-600/10');
            seasonBtn.classList.remove('border-transparent');
            saveBtn.disabled = false;
          }, { signal: lifetime.signal });
          seasonsList.appendChild(seasonBtn);
        });
        applyResponsiveImages(seasonsList);
      } catch (_) {
        seasonsLoading.classList.add('hidden');
        seasonsList.innerHTML = '<p class="text-xs text-red-400 py-1">Could not load seasons.</p>';
      }
    }, { signal: lifetime.signal });
  };

  const doSearch = async () => {
    const q = searchInput.value.trim();
    if (!q) return;
    const resultsEl = modal.querySelector('#remap-search-results') as HTMLElement;
    resultsEl.innerHTML = '<p class="text-xs text-zinc-500 py-2">Searching…</p>';
    try {
      const searchType = modalMode === 'match-movie' ? 'movie' : 'series';
      const res = await request(`/api/proxy/media/search?q=${encodeURIComponent(q)}&type=${searchType}`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (!res.ok) throw new Error('Search failed');
      const data = await res.json();
      const results: any[] = data.results?.slice(0, 8) ?? [];
      if (!results.length) {
        resultsEl.innerHTML = '<p class="text-xs text-zinc-500 py-2">No results.</p>';
        return;
      }
      resultsEl.innerHTML = results.map((r: any) => {
        const year = esc(String(r.release_date ?? r.first_air_date ?? '').slice(0, 4));
        // Same rationale as the TVDB results below (#364) - an exact-title
        // remake/reboot is otherwise indistinguishable, especially when
        // TMDB hasn't got a release/air date on record yet. TMDB has no
        // network/status on a search result the way TVDB does, but the id
        // is always there, and original_title catches e.g. a dub sharing
        // its English title with an unrelated show.
        const subtitle = [
          year || null,
          `TMDB ${r.tmdb_id}`,
          r.original_title && r.original_title !== r.title ? esc(r.original_title) : null,
        ].filter(Boolean).join(' · ');
        return `
        <button type="button" class="remap-result w-full flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-zinc-800 transition-colors cursor-pointer text-left"
          data-tmdb="${r.tmdb_id}" data-title="${esc(r.title ?? '')}" data-year="${year}" data-poster="${esc(r.poster_path ?? '')}">
          <img data-artwork-path="${esc(r.poster_path ?? '')}" alt="" class="w-8 h-11 object-cover rounded bg-zinc-700 shrink-0" loading="lazy" decoding="async" onerror="this.style.display='none'" />
          <div class="flex-1 min-w-0">
            <p class="text-sm text-zinc-100 truncate">${esc(r.title ?? '')}</p>
            <p class="text-xs text-zinc-500">${subtitle}</p>
          </div>
        </button>`;
      }).join('');
      applyResponsiveImages(resultsEl);
      resultsEl.querySelectorAll('.remap-result').forEach(btn => wireResultClick(btn as HTMLElement));
    } catch (_) {
      resultsEl.innerHTML = '<p class="text-xs text-red-400 py-2">Search failed.</p>';
    }
  };

  const doTvdbSearch = async () => {
    const q = (modal.querySelector<HTMLInputElement>('#remap-tvdb-input') as HTMLInputElement).value.trim();
    if (!q) return;
    const resultsEl = modal.querySelector('#remap-tvdb-results') as HTMLElement;
    resultsEl.innerHTML = '<p class="text-xs text-zinc-500 py-2">Searching…</p>';
    try {
      const res = await request(`/api/proxy/media/search-tvdb?q=${encodeURIComponent(q)}`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        const detail: string = err.detail ?? 'Search failed';
        if (detail.toLowerCase().includes('api key')) {
          resultsEl.innerHTML = `<div class="py-2 space-y-1">
            <p class="text-xs text-amber-400">A TVDB API key is required to search. Set one in <a href="/settings" class="underline hover:text-amber-300">Settings</a> or ask your admin to configure a global key.</p>
          </div>`;
        } else {
          resultsEl.innerHTML = `<p class="text-xs text-red-400 py-2">${esc(detail)}</p>`;
        }
        return;
      }
      const results: any[] = await res.json();
      if (!results.length) {
        resultsEl.innerHTML = '<p class="text-xs text-zinc-500 py-2">No results found on TVDB.</p>';
        return;
      }
      resultsEl.innerHTML = results.slice(0, 8).map((r: any) => {
        // A blank/missing year plus an identical title (remakes/reboots
        // sharing a name, e.g. Ranma 1/2) makes two results indistinguishable
        // - the TVDB id is always shown so there's an unambiguous fallback
        // to pick the right one by (#364).
        const subtitle = [
          r.year ? esc(String(r.year)) : null,
          `TVDB ${r.tvdb_id}`,
          r.network ? esc(r.network) : null,
          r.status ? esc(r.status) : null,
        ].filter(Boolean).join(' · ');
        return `
        <button type="button" class="remap-result w-full flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-zinc-800 transition-colors cursor-pointer text-left"
          data-tvdb="${r.tvdb_id}" data-title="${esc(r.title ?? '')}" data-year="${esc(String(r.year ?? ''))}" data-poster="${esc(r.image_url ?? '')}">
          <img src="${esc(r.image_url ?? '')}" alt="" class="w-8 h-11 object-cover rounded bg-zinc-700 shrink-0" onerror="this.style.display='none'" />
          <div class="flex-1 min-w-0">
            <p class="text-sm text-zinc-100 truncate">${esc(r.title ?? '')}</p>
            <p class="text-xs text-zinc-500">${subtitle}</p>
          </div>
        </button>`;
      }).join('');
      resultsEl.querySelectorAll('.remap-result').forEach(btn => wireResultClick(btn as HTMLElement));
    } catch (e: any) {
      resultsEl.innerHTML = `<p class="text-xs text-red-400 py-2">Search failed: ${esc(e.message ?? '')}</p>`;
    }
  };

  searchBtn.addEventListener('click', doSearch, { signal: lifetime.signal });
  searchInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); }, { signal: lifetime.signal });
  modal.querySelector<HTMLButtonElement>('#remap-tvdb-search-btn')?.addEventListener('click', doTvdbSearch, { signal: lifetime.signal });
  (modal.querySelector<HTMLInputElement>('#remap-tvdb-input'))?.addEventListener('keydown', (e) => { if (e.key === 'Enter') doTvdbSearch(); }, { signal: lifetime.signal });

  modal.querySelector<HTMLButtonElement>('#remap-save')?.addEventListener('click', async () => {
    const saveBtn = modal.querySelector<HTMLButtonElement>('#remap-save') as HTMLButtonElement;
    const errEl = modal.querySelector('#remap-error') as HTMLElement;
    errEl.classList.add('hidden');
    saveBtn.disabled = true;

    if (modalMode === 'match') {
      if (!targetTvdbId && !targetTmdbId) return;
      saveBtn.textContent = 'Matching…';
      try {
        const body: any = { show_title: matchSeriesName };
        if (targetTvdbId) body.tvdb_id = targetTvdbId;
        else body.tmdb_id = targetTmdbId;
        const res = await request('/api/proxy/sync/match-unmatched-show', {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(await res.text());
        closeModal();
        if (!lifetime.signal.aborted) onSaved();
      } catch (e: any) {
        errEl.textContent = `Error: ${e.message ?? 'Unknown error'}`;
        errEl.classList.remove('hidden');
        saveBtn.disabled = false;
        saveBtn.textContent = 'Match & Apply';
      }
      return;
    }

    if (modalMode === 'match-movie') {
      if (!targetTmdbId) return;
      saveBtn.textContent = 'Matching…';
      try {
        const res = await request('/api/proxy/sync/match-unmatched-movie', {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ movie_title: matchMovieTitle, tmdb_id: targetTmdbId }),
        });
        if (!res.ok) throw new Error(await res.text());
        closeModal();
        if (!lifetime.signal.aborted) onSaved();
      } catch (e: any) {
        errEl.textContent = `Error: ${e.message ?? 'Unknown error'}`;
        errEl.classList.remove('hidden');
        saveBtn.disabled = false;
        saveBtn.textContent = 'Match & Apply';
      }
      return;
    }

    // Remap mode
    if ((!targetTmdbId && !targetTvdbId) || targetSeasonNumber === null) return;
    saveBtn.textContent = 'Saving…';
    try {
      const remapBody: any = {
        source_show_tmdb_id: srcTmdbId,
        source_season_number: srcSeason,
        target_season_number: targetSeasonNumber,
      };
      if (targetTvdbId) remapBody.target_show_tvdb_id = targetTvdbId;
      else remapBody.target_show_tmdb_id = targetTmdbId;
      const createRes = await request('/api/proxy/sync/season-overrides', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(remapBody),
      });
      if (!createRes.ok) throw new Error(await createRes.text());
      const override = await createRes.json();

      saveBtn.textContent = 'Applying…';
      const applyRes = await request(`/api/proxy/sync/season-overrides/${override.id}/apply`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (!applyRes.ok) throw new Error(await applyRes.text());

      closeModal();
      if (!lifetime.signal.aborted) onSaved();
    } catch (e: any) {
      errEl.textContent = `Error: ${e.message ?? 'Unknown error'}`;
      errEl.classList.remove('hidden');
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save & Apply';
    }
  }, { signal: lifetime.signal });

  const openRemapModal = (tmdbId: number, season: number, title: string) => {
    modalMode = 'remap';
    srcTmdbId = tmdbId;
    srcSeason = season;
    (modal.querySelector('#remap-modal-title') as HTMLElement).textContent = 'Remap Season';
    (modal.querySelector('#remap-source-label') as HTMLElement).textContent = `"${title}" — Season ${season}`;
    (modal.querySelector('#remap-tmdb-label') as HTMLElement).textContent = 'Search for the correct TMDB show';
    (modal.querySelector('#remap-seasons-area') as HTMLElement).classList.remove('hidden');
    (modal.querySelector<HTMLButtonElement>('#remap-save') as HTMLButtonElement).textContent = 'Save & Apply';
    // TMDB/TVDB toggle (#178) - some shows' season structure only exists on
    // TVDB, so the remap target needs the same TMDB/TVDB choice the "Match"
    // flow already has, not just TMDB search.
    (modal.querySelector('#match-tab-bar') as HTMLElement).classList.remove('hidden');
    showOverlay(modal);
    setMatchTab('tmdb');
  };

  const openMatchModal = (seriesName: string) => {
    modalMode = 'match';
    matchSeriesName = seriesName;
    (modal.querySelector('#remap-modal-title') as HTMLElement).textContent = 'Match Show';
    (modal.querySelector('#remap-source-label') as HTMLElement).textContent = `"${seriesName}"`;
    (modal.querySelector('#remap-seasons-area') as HTMLElement).classList.add('hidden');
    (modal.querySelector<HTMLButtonElement>('#remap-save') as HTMLButtonElement).textContent = 'Match & Apply';
    (modal.querySelector('#remap-tmdb-label') as HTMLElement).textContent = 'Search for the correct TMDB show';
    (modal.querySelector('#match-tab-bar') as HTMLElement).classList.remove('hidden');
    const tvdbInput = modal.querySelector<HTMLInputElement>('#remap-tvdb-input') as HTMLInputElement;
    tvdbInput.value = seriesName;
    (modal.querySelector<HTMLInputElement>('#remap-search-input') as HTMLInputElement).value = seriesName;
    showOverlay(modal);
    // Default to TVDB tab and auto-search
    setMatchTab('tvdb');
  };

  const openMatchMovieModal = (movieTitle: string) => {
    modalMode = 'match-movie';
    matchMovieTitle = movieTitle;
    (modal.querySelector('#remap-modal-title') as HTMLElement).textContent = 'Match Movie on TMDB';
    (modal.querySelector('#remap-source-label') as HTMLElement).textContent = `"${movieTitle}"`;
    (modal.querySelector('#remap-tmdb-label') as HTMLElement).textContent = 'Search for the correct TMDB movie';
    (modal.querySelector('#remap-tmdb-section') as HTMLElement).classList.remove('hidden');
    (modal.querySelector('#remap-tvdb-section') as HTMLElement).classList.add('hidden');
    (modal.querySelector('#remap-seasons-area') as HTMLElement).classList.add('hidden');
    (modal.querySelector<HTMLButtonElement>('#remap-save') as HTMLButtonElement).textContent = 'Match & Apply';
    searchInput.value = movieTitle;
    showOverlay(modal);
    searchInput.focus();
    // Auto-search immediately so results appear right away
    doSearch();
  };
  // Delegation covers refreshed warnings and the static remap list without rebinding.
  document.addEventListener('click', async event => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest<HTMLButtonElement>('.remap-season-btn, .match-show-btn, .unmatch-show-btn, .delete-override-btn');
    if (!button || button.disabled) return;
    if (button.classList.contains('remap-season-btn')) {
      openRemapModal(parseInt(button.dataset.srcTmdb ?? '0'), parseInt(button.dataset.srcSeason ?? '0'), button.dataset.srcTitle ?? '');
      return;
    }
    const title = button.dataset.seriesName ?? '';
    const isMovie = button.dataset.mediaType === 'movie';
    if (button.classList.contains('match-show-btn')) {
      if (isMovie) openMatchMovieModal(title);
      else openMatchModal(title);
      return;
    }
    if (button.classList.contains('unmatch-show-btn')) {
      if (!title) return;
      button.disabled = true;
      button.textContent = 'Removing…';
      try {
        const res = await request(`/api/proxy/sync/${isMovie ? 'unmatch-movie' : 'unmatch-show'}`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify(isMovie ? { movie_title: title } : { show_title: title }),
        });
        if (!res.ok) throw new Error(await res.text());
        if (!lifetime.signal.aborted) onSaved();
      } catch (error) {
        if (lifetime.signal.aborted) return;
        button.disabled = false;
        button.textContent = 'Unmatch';
        alert(`Failed to unmatch: ${error instanceof Error ? error.message : 'Unknown error'}`);
      }
      return;
    }
    const overrideId = button.dataset.overrideId;
    if (!overrideId || !confirm('Remove this season remap?')) return;
    button.disabled = true;
    button.textContent = 'Removing…';
    try {
      const res = await request(`/api/proxy/sync/season-overrides/${overrideId}`, {
        method: 'DELETE', headers: { 'Authorization': `Bearer ${token}` },
      });
      if (lifetime.signal.aborted) return;
      if (res.ok) {
        button.closest('[data-override-id]')?.remove();
        const list = document.getElementById('season-remaps-list');
        if (list && list.children.length === 0) document.getElementById('season-remaps-panel')?.remove();
      } else {
        button.disabled = false;
        button.textContent = 'Remove';
      }
    } catch {
      if (lifetime.signal.aborted) return;
      button.disabled = false;
      button.textContent = 'Remove';
    }
  }, { signal: lifetime.signal });

  return { openRemapModal, openMatchModal, openMatchMovieModal, stop: () => lifetime.abort() };
}
