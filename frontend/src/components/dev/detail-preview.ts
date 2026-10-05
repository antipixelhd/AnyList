import type {DetailSample, SampleKey} from './detail-fixtures';

type PreviewState = {progress: number; status: string; rating: string; seasonScores: (number | null)[]; useSeasonAverage: boolean; library: boolean; favorite: boolean; note: string; watched: string[]; spoilers: boolean; season: number};
type QuickRate = (detail: {title: string; poster?: string; score: number | null; ratingMode: 'manual' | 'average'; onScore: (score: number | null) => void}) => void;
let listeners: AbortController | undefined;
let toastTimer: ReturnType<typeof setTimeout> | undefined;
let observer: IntersectionObserver | undefined;

function initializeDetails() {
  listeners?.abort();
  observer?.disconnect();
  clearTimeout(toastTimer);
  const root = document.querySelector<HTMLElement>('[data-detail-lab]');
  const fixture = document.querySelector('#lab-fixture');
  if (!root || !fixture?.textContent) return;
  listeners = new AbortController();
  const {signal} = listeners;
  const item: DetailSample = JSON.parse(fixture.textContent);
  const kind = root.dataset.mediaKind as SampleKey;
  const storageKey = `anylist:detail-lab:v2:${kind}`;
  const allEpisodes = item.seasons?.flatMap((season,s) => season.map((ep,e) => ({...ep,key:`${s}:${e}`,season:s,episode:e}))) ?? [];
  const statusInput = root.querySelector<HTMLSelectElement>('#lab-status')!;
  const progressInput = root.querySelector<HTMLInputElement>('#lab-progress')!;
  const noteInput = root.querySelector<HTMLTextAreaElement>('#lab-note')!;
  const bar = root.querySelector<HTMLProgressElement>('#lab-progress-bar');
  const noteState = root.querySelector<HTMLElement>('[data-lab-note-state]')!;
  const defaultState = (): PreviewState => ({progress:item.progress,status:item.action,rating:kind === 'series' ? '9' : '',seasonScores:item.seasons?.map((_,s) => s === 0 ? 9 : null) ?? [],useSeasonAverage:false,library:false,favorite:false,note:item.note,watched:allEpisodes.slice(0,item.progress).map(e => e.key),spoilers:false,season:0});
  const validScore = (score: unknown) => typeof score === 'number' && Number.isFinite(score) && score >= .5 && score <= 10 && Number.isInteger(score*2);
  const clampProgress = (value: unknown) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return 0;
    return kind === 'game' ? Math.max(0,Math.min(100000,Math.round(parsed*2)/2)) : Math.max(0,Math.min(item.total,Math.floor(parsed)));
  };
  let state = defaultState();
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) ?? 'null');
    if (saved && typeof saved === 'object') {
      state.progress = clampProgress(saved.progress ?? state.progress);
      state.status = [...statusInput.options].some(o => o.value === saved.status) ? saved.status : state.status;
      state.rating = saved.rating === '' || validScore(Number(saved.rating)) ? String(saved.rating) : state.rating;
      state.seasonScores = state.seasonScores.map((score,s) => saved.seasonScores?.[s] === null ? null : validScore(saved.seasonScores?.[s]) ? saved.seasonScores[s] : score);
      state.useSeasonAverage = kind === 'series' && saved.useSeasonAverage === true;
      state.library = saved.library === true;
      state.favorite = saved.favorite === true;
      state.note = typeof saved.note === 'string' ? saved.note.slice(0,10000) : state.note;
      state.spoilers = saved.spoilers === true;
      state.season = saved.season === -1 || Number.isInteger(saved.season) && item.seasons?.[saved.season] ? saved.season : 0;
      state.watched = Array.isArray(saved.watched) ? [...new Set<string>(saved.watched.filter((key: unknown) => typeof key === 'string' && allEpisodes.some(ep => ep.key === key)))] : state.watched;
      if (kind === 'series') state.progress = state.watched.length;
    }
  } catch { /* A preview remains usable when storage is unavailable or corrupt. */ }
  const notify = (message: string) => {
    const feedback = root.querySelector<HTMLElement>('#lab-feedback')!;
    feedback.querySelector('[data-lab-feedback-text]')!.textContent = message;
    feedback.classList.add('is-visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => feedback.classList.remove('is-visible'),2500);
  };
  const save = () => {
    try { localStorage.setItem(storageKey,JSON.stringify(state)); }
    catch { notify('Updated for this session. Browser storage is unavailable.'); }
  };
  const seasonAverage = () => {
    const rated = state.seasonScores.filter((score): score is number => score !== null);
    return rated.length ? rated.reduce((sum,n) => sum+n,0)/rated.length : null;
  };
  const showScore = () => state.useSeasonAverage ? seasonAverage() : state.rating ? Number(state.rating) : null;
  const render = () => {
    statusInput.value = state.status;
    progressInput.value = String(state.progress);
    if (bar) bar.value = state.progress;
    root.dataset.spoilers = String(state.spoilers);
    const favorite = root.querySelector<HTMLButtonElement>('[data-lab-action="favorite"]')!;
    favorite.setAttribute('aria-pressed',String(state.favorite));
    favorite.setAttribute('aria-label',state.favorite ? 'Remove from favorites' : 'Add to favorites');
    favorite.querySelector('[data-lab-favorite-label]')!.textContent = state.favorite ? 'Favorited' : 'Favorite';
    root.querySelectorAll('[data-lab-personal-score]').forEach(el => el.textContent = showScore()?.toFixed(1) ?? '—');
    root.querySelector('#lab-rating')?.setAttribute('aria-label',`Rate ${item.title}${kind === 'series' ? ' show' : ''}: ${showScore()?.toFixed(1) ?? 'unrated'}`);
    root.querySelectorAll('[data-lab-rating-mode]').forEach(el => el.textContent = state.useSeasonAverage ? 'Season average' : 'Separate show score');
    const library = root.querySelector<HTMLButtonElement>('[data-lab-action="library"]');
    if (library) {library.setAttribute('aria-pressed',String(state.library));library.querySelector('[data-lab-library-label]')!.textContent = state.library ? 'In library' : 'Add to library';}
    root.querySelectorAll<HTMLButtonElement>('[data-lab-action="increment"]').forEach(button => button.disabled = kind !== 'game' && state.progress >= item.total);
    const nextCard = root.querySelector<HTMLElement>('[data-lab-next]');
    if (nextCard) nextCard.hidden = ['Completed','Dropped'].includes(state.status) || state.progress >= item.total;
    const nextTitle = root.querySelector<HTMLElement>('[data-lab-next-title]');
    const nextMeta = root.querySelector<HTMLElement>('[data-lab-next-meta]');
    if (kind === 'series') {
      const watched = new Set(state.watched);
      root.querySelectorAll<HTMLButtonElement>('[data-lab-episode]').forEach(button => {
        const key = button.dataset.labEpisode!;
        const yes = watched.has(key);
        const ep = allEpisodes.find(e => e.key === key)!;
        button.setAttribute('aria-pressed',String(yes));
        button.setAttribute('aria-label',`Mark season ${ep.season+1} episode ${ep.episode+1} ${yes ? 'unwatched' : 'watched'}`);
        const row = button.closest<HTMLElement>('.lab-episode')!;
        row.dataset.watched = String(yes);
        row.querySelector('[data-lab-episode-title]')!.textContent = yes || state.spoilers ? ep.title : `Episode ${ep.episode+1}`;
      });
      const next = allEpisodes.find(ep => !watched.has(ep.key));
      if (nextTitle) nextTitle.textContent = next ? state.spoilers ? next.title : `Episode ${next.episode+1}` : 'You’re all caught up';
      if (nextMeta) nextMeta.textContent = next ? `S${String(next.season+1).padStart(2,'0')} · E${String(next.episode+1).padStart(2,'0')} · ${next.minutes} min` : `${item.total} episodes watched`;
      const nextImage = root.querySelector<HTMLImageElement>('.lab-next > img');
      if (nextImage) {nextImage.hidden = !next?.image;if (next?.image) nextImage.src = next.image;}
      const art = root.querySelector<HTMLElement>('[data-lab-art-preview]');
      if (art) {
        const key = (url: string) => url.split('/').pop();
        const used = new Set([item.poster,item.backdrop,next?.image ?? ''].map(key));
        const distinct = item.gallery.find(url => !used.has(key(url)));
        art.closest<HTMLElement>('.lab-art-preview')!.hidden = !distinct;
        if (distinct) {art.dataset.labImage = distinct;art.querySelector<HTMLImageElement>('img')!.src = distinct;}
      }
      root.querySelectorAll<HTMLElement>('[data-lab-season]').forEach(el => el.hidden = Number(el.dataset.labSeason) !== state.season);
      item.seasons!.forEach((season,s) => {
        const count = season.filter((_,e) => watched.has(`${s}:${e}`)).length;
        root.querySelector(`[data-lab-season-progress="${s}"]`)!.textContent = `${count} / ${season.length} watched`;
        root.querySelector(`[data-lab-season-score="${s}"]`)!.textContent = state.seasonScores[s]?.toFixed(1) ?? 'Rate';
        root.querySelector(`[data-lab-rate-season="${s}"]`)!.setAttribute('aria-label',`Rate season ${s+1}: ${state.seasonScores[s]?.toFixed(1) ?? 'unrated'}`);
        root.querySelector<HTMLButtonElement>(`[data-lab-action="season-watched"][data-season="${s}"]`)!.disabled = count === season.length;
        root.querySelector(`[data-lab-expand-season="${s}"]`)!.setAttribute('aria-expanded',String(state.season === s));
      });
      root.querySelector<HTMLInputElement>('#lab-season-average')!.checked = state.useSeasonAverage;
      root.querySelector('[data-lab-average-preview]')!.textContent = seasonAverage() === null ? 'No rated seasons' : `${seasonAverage()!.toFixed(1)} / 10`;
      const spoilers = root.querySelector('[data-lab-action="spoilers"]')!;
      spoilers.setAttribute('aria-pressed',String(state.spoilers));
      root.querySelector('[data-lab-spoiler-label]')!.textContent = state.spoilers ? 'Titles visible' : 'Titles hidden';
    }
  };
  const syncCompletion = () => {
    if (kind === 'game') return;
    if (state.progress === item.total) state.status = 'Completed';
    else if (state.status === 'Completed') state.status = item.action;
  };
  const updateProgress = (value: unknown) => {
    state.progress = clampProgress(value);
    if (kind === 'series') state.watched = allEpisodes.slice(0,state.progress).map(e => e.key);
    syncCompletion(); render(); save();
  };
  const selectTab = (id: string, focus = false) => {
    root.querySelectorAll<HTMLButtonElement>('[data-lab-tab]').forEach(button => {
      const active = button.dataset.labTab === id;
      button.setAttribute('aria-selected',String(active)); button.tabIndex = active ? 0 : -1;
      if (active && focus) button.focus();
    });
    root.querySelectorAll<HTMLElement>('.lab-panel').forEach(panel => panel.hidden = panel.id !== `panel-${id}`);
  };
  const quickRate = (season?: number) => {
    const open = (window as Window & {anyListQuickRate?: QuickRate}).anyListQuickRate;
    if (!open) {notify('The rating picker is loading. Try again.');return;}
    open({title:season === undefined ? item.title : `${item.title} · Season ${season+1}`,poster:item.poster,score:season === undefined ? showScore() : state.seasonScores[season],ratingMode:season === undefined && state.useSeasonAverage ? 'average' : 'manual',onScore:score => {
      if (season === undefined) {state.rating = score === null ? '' : String(score);state.useSeasonAverage = false;}
      else state.seasonScores[season] = score;
      render();save();notify(season === undefined ? 'Score saved locally' : `Season ${season+1} score saved locally`);
    }});
    const help = document.querySelector('#quick-rating-help');
    if (help) help.textContent = 'Choose a half point. Saved in this preview.';
  };
  root.addEventListener('click',event => {
    const target = event.target instanceof Element ? event.target : null;
    const tab = target?.closest<HTMLElement>('[data-lab-tab], [data-lab-open-tab]');
    if (tab) { selectTab(tab.dataset.labTab ?? tab.dataset.labOpenTab!,!!tab.dataset.labOpenTab); return; }
    const seasonRating = target?.closest<HTMLElement>('[data-lab-rate-season]');
    if (seasonRating) {quickRate(Number(seasonRating.dataset.labRateSeason));return;}
    const expandSeason = target?.closest<HTMLElement>('[data-lab-expand-season]');
    if (expandSeason) {const season = Number(expandSeason.dataset.labExpandSeason);state.season = state.season === season ? -1 : season;render();save();return;}
    const close = target?.closest('[data-lab-close-dialog]');
    if (close) { close.closest<HTMLDialogElement>('dialog')!.close(); return; }
    const image = target?.closest<HTMLElement>('[data-lab-image]');
    if (image) {
      const caption = image.dataset.labImageCaption ?? item.title;
      const preview = root.querySelector<HTMLImageElement>('#lab-lightbox-image')!;
      preview.src = image.dataset.labImage!; preview.alt = caption;
      root.querySelector('#lab-lightbox-caption')!.textContent = caption;
      root.querySelector<HTMLDialogElement>('#lab-lightbox')!.showModal(); return;
    }
    const episode = target?.closest<HTMLButtonElement>('[data-lab-episode]');
    if (episode) {
      const key = episode.dataset.labEpisode!;
      state.watched = state.watched.includes(key) ? state.watched.filter(e => e !== key) : [...state.watched,key];
      state.progress = state.watched.length; syncCompletion(); render(); save(); notify('Episode updated'); return;
    }
    const action = target?.closest<HTMLElement>('[data-lab-action]')?.dataset.labAction;
    if (action === 'increment') {
      if (kind === 'series') {
        const next = allEpisodes.find(ep => !state.watched.includes(ep.key));
        if (!next) return;
        state.watched.push(next.key); state.progress = state.watched.length; syncCompletion(); render(); save();
      } else updateProgress(state.progress + item.increment);
      notify(kind === 'movie' ? 'Marked watched' : 'Progress updated');
    } else if (action === 'library') {state.library = !state.library;render();save();notify(state.library ? 'Added to library in this preview' : 'Removed from library in this preview');}
    else if (action === 'favorite') { state.favorite = !state.favorite; render(); save(); notify(state.favorite ? 'Added to favorites' : 'Removed from favorites'); }
    else if (action === 'synopsis') {
      const more = root.querySelector<HTMLElement>('#lab-more-story')!;
      more.hidden = !more.hidden;
      const button = root.querySelector<HTMLButtonElement>('[data-lab-action="synopsis"]')!;
      button.setAttribute('aria-expanded',String(!more.hidden));
      button.firstChild!.textContent = more.hidden ? 'Read more ' : 'Read less ';
    } else if (action === 'notes') root.querySelector<HTMLDialogElement>('#lab-design-notes')!.showModal();
    else if (action === 'spoilers') { state.spoilers = !state.spoilers; render(); save(); }
    else if (action === 'season-watched') {
      const season = Number(target?.closest<HTMLElement>('[data-season]')?.dataset.season);
      const seasonKeys = allEpisodes.filter(ep => ep.season === season).map(ep => ep.key);
      state.watched = [...new Set([...state.watched,...seasonKeys])]; state.progress = state.watched.length;
      syncCompletion(); render(); save(); notify('Season marked watched');
    } else if (action === 'save-note') { state.note = noteInput.value.slice(0,10000); save(); noteState.textContent = 'Saved · Private'; notify('Note saved locally'); }
    else if (action === 'rate') quickRate();
    else if (action === 'reset') { state = defaultState(); noteInput.value = state.note; noteState.textContent = 'Private'; render(); save(); notify('Preview reset'); }
  },{signal});
  progressInput.addEventListener('change',() => {updateProgress(progressInput.value);notify('Progress updated');},{signal});
  statusInput.addEventListener('change',() => {
    state.status = statusInput.value;
    if (state.status === 'Completed' && kind !== 'game') updateProgress(item.total);
    else {render();save();}
    notify('List status updated');
  },{signal});
  root.querySelector<HTMLInputElement>('#lab-season-average')?.addEventListener('change',event => {state.useSeasonAverage = (event.target as HTMLInputElement).checked;render();save();notify(state.useSeasonAverage ? 'Show score uses your rated seasons' : 'Separate show score restored');},{signal});
  noteInput.addEventListener('input',() => {noteState.textContent = 'Unsaved · Private';},{signal});
  root.querySelector('.lab-tabs')?.addEventListener('keydown',event => {
    const keyEvent = event as KeyboardEvent;
    const tabs = [...root.querySelectorAll<HTMLButtonElement>('[data-lab-tab]')];
    const index = tabs.indexOf(document.activeElement as HTMLButtonElement);
    if (index < 0 || !['ArrowLeft','ArrowRight','Home','End'].includes(keyEvent.key)) return;
    keyEvent.preventDefault();
    const next = keyEvent.key === 'Home' ? 0 : keyEvent.key === 'End' ? tabs.length-1 : (index + (keyEvent.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    selectTab(tabs[next].dataset.labTab!,true);
  },{signal});
  root.querySelectorAll<HTMLDialogElement>('dialog').forEach(dialog => {
    dialog.addEventListener('click',event => {
      if (event.target !== dialog) return;
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    },{signal});
    dialog.addEventListener('close',() => {if (dialog.id === 'lab-lightbox') root.querySelector<HTMLImageElement>('#lab-lightbox-image')!.removeAttribute('src');},{signal});
  });
  noteInput.value = state.note;
  render();
  const artwork = [...root.querySelectorAll<HTMLImageElement>('.lab-hero-image, .lab-hero-poster img, .lab-sidebar .lab-poster')];
  let entranceTimer: ReturnType<typeof setTimeout> | undefined;
  const ready = Promise.all([document.fonts.ready, ...artwork.map(img => img.decode().catch(() => {}))]);
  const deadline = new Promise<void>(resolve => { entranceTimer = setTimeout(resolve, 1000); });
  void Promise.race([ready, deadline]).then(async () => {
    clearTimeout(entranceTimer);
    // Let font metrics and any development stylesheet updates reach layout.
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    if (signal.aborted || !root.isConnected) return;
    if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
      observer = new IntersectionObserver(entries => entries.forEach(entry => {if (entry.isIntersecting) {entry.target.classList.add('lab-in-view');observer?.unobserve(entry.target);}}),{threshold:.05});
      root.querySelectorAll('.lab-module').forEach(el => {
        const bounds = el.getBoundingClientRect();
        if (bounds.top < innerHeight && bounds.bottom > 0) el.classList.add('lab-in-view');
        else observer!.observe(el);
      });
    }
    root.removeAttribute('data-lab-loading');
    root.setAttribute('aria-busy', 'false');
    root.dispatchEvent(new Event('lab:ready'));
  });
  signal.addEventListener('abort', () => clearTimeout(entranceTimer), {once: true});
  document.addEventListener('astro:before-swap',() => {listeners?.abort();observer?.disconnect();clearTimeout(toastTimer);},{once:true,signal});
}

document.addEventListener('astro:page-load',initializeDetails);
