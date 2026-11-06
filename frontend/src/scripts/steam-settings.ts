import { defaultSteamDraft, sortSteamGames, steamSelected, steamProgressPending, steamStatus, filterSteamGames, reconcileSteamCards, chooseSteamStatus, steamAccentText, steamScrollAnchor, steamWishlistSelected } from '../lib/steam-import';
import type { SteamDraft, SteamGame } from '../lib/steam-import';
import { showDialog, closeDialog } from '../lib/ui-motion';
import { setupCalendarDateInputs } from '../lib/calendar-date-input';
import { formatDate } from '../lib/date-time';

type Connection = { id: number; steam_id: string; imported: boolean; sync_achievements: boolean; sync_playtime: boolean; auto_add_games: boolean; sync_wishlist: boolean; selected_count: number; generation: number };
type Review = Connection & { wishlist: SteamGame[]; wishlist_error: string | null; library: SteamGame[]; draft: Partial<SteamDraft>; job: { status: string; current: number; total: number; error: string | null; warnings: string[] } | null };

let cleanup: (() => void) | undefined;
function init() {
  cleanup?.();
  const host = document.querySelector<HTMLElement>('[data-steam-settings]');
  if (!host) return;
  const controller = new AbortController();
  const { signal } = controller;
  const paintTheme = () => host.style.setProperty('--steam-on-accent', steamAccentText(getComputedStyle(document.body).getPropertyValue('--blue').trim()));
  paintTheme();
  document.addEventListener('anylist:profile-color-changed', () => queueMicrotask(paintTheme), { signal });
  const get = <T extends HTMLElement = HTMLElement>(name: string) => host.querySelector<T>(`[data-steam-${name}]`)!;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let saveTimer: ReturnType<typeof setTimeout> | undefined;
  let draft = defaultSteamDraft();
  let active: Review | undefined;
  let games: SteamGame[] = [];
  let wishlist: SteamGame[] = [], wishlistLimit = 80;
  let step: 0 | 1 | 2 = 0;
  let byId = new Map<string, SteamGame>();
  let limit = 80;
  let query = '';
  let selectedOnly = false;
  let visibleGames: SteamGame[] = [];
  let orderedGames: SteamGame[] = [];
  let writes: Promise<unknown> = Promise.resolve();
  let loadVersion = 0;
  let paused = false;
  const cards = new Map<string, HTMLButtonElement>();
  const wishlistCards = new Map<string, HTMLElement>();
  const dialog = get<HTMLDialogElement>('dialog');
  let detailsController = new AbortController();
  let reviewGames: SteamGame[] | null = null;
  let reviewLimit = 40;
  const scroller = get('scroll');
  const desktopList = matchMedia('(min-width:801px) and (hover:hover) and (pointer:fine)');
  const inlineEditors = new Map<string, { node: HTMLElement; status: string; controller: AbortController }>();
  const combinedList = () => desktopList.matches && dialog.dataset.layout === 'list';
  function clearInlineEditors() {
    for (const editor of inlineEditors.values()) { editor.controller.abort(); editor.node.remove(); }
    inlineEditors.clear();
  }
  function setLayout(layout: 'grid' | 'list') {
    if (active && step === 0 && combinedList() && !validateInlineDates()) return;
    const region = get(step === 2 ? 'wishlist-grid' : step === 1 ? 'details' : 'grid');
    const top = scroller.getBoundingClientRect().top;
    const anchor = Array.from(region.children).find(node => !node.hasAttribute('hidden') && node.getBoundingClientRect().bottom > top);
    const offset = anchor ? anchor.getBoundingClientRect().top - top : 0;
    dialog.dataset.layout = layout;
    for (const value of ['grid', 'list'] as const) get(`layout-${value}`).setAttribute('aria-pressed', String(value === layout));
    if (active) {
      clearInlineEditors();
      if (step === 1 && combinedList()) { setStep(0); render(false); }
      else paintSelections();
    }
    if (anchor) scroller.scrollTop += anchor.getBoundingClientRect().top - top - offset;
    try { localStorage.setItem('steam-import-layout', layout); } catch {}
  }
  let layout: 'grid' | 'list' = 'grid';
  try { if (localStorage.getItem('steam-import-layout') === 'list') layout = 'list'; } catch {}
  setLayout(layout);
  listen(get('layout-grid'), 'click', () => setLayout('grid'));
  listen(get('layout-list'), 'click', () => setLayout('list'));
  listen(desktopList, 'change', () => {
    if (!active) return;
    clearInlineEditors();
    if (step === 1 && combinedList()) { setStep(0); render(false); }
    else paintSelections();
  });
  const finishDate = (game: SteamGame) => Object.hasOwn(draft.completion_dates, game.app_id) ? draft.completion_dates[game.app_id]
    : game.last_played ? new Date(game.last_played * 1000).toLocaleDateString('sv-SE') : null;
  listen(get('resume'), 'click', () => {
    paused=false; showDialog(dialog);
    if(active) {
      if(get('picker').hidden) void load(active.id).catch(fail);
      else void loadProgress(++reviewEpoch,active.id,active.generation);
    }
  });
  const exitDialog = get<HTMLDialogElement>('exit-dialog');
  let leaving = false;
  listen(exitDialog,'cancel',event=>{if(leaving)event.preventDefault();});
  function confirmExit() { if (!importing) { get('exit-error').hidden = true; showDialog(exitDialog); get('keep-editing').focus({preventScroll:true}); } }
  listen(get('later'),'click',confirmExit);
  listen(dialog,'cancel',event=>{event.preventDefault();confirmExit();});
  listen(get('keep-editing'),'click',()=>{void closeDialog(exitDialog);});
  async function leave(abort: boolean) {
    if (!active || importing || leaving) return;
    if (!abort && ((reviewGames && get('details').querySelector('input:invalid')) || (combinedList() && invalidInlineDate()))) {
      get('exit-error').textContent='Correct the date before saving. Choose Keep editing to return.';
      get('exit-error').hidden=false; return;
    }
    leaving = true;
    const connection = active;
    clearTimeout(saveTimer);
    const buttons = exitDialog.querySelectorAll<HTMLButtonElement>('button');
    buttons.forEach(button=>button.disabled=true);
    try {
      if (abort) {
        await writes.catch(()=>{});
        const response = await fetch(`/api/proxy/auth/connections/${connection.id}`,{method:'DELETE',signal});
        if (!response.ok) throw new Error('Could not abort the import. Try again.');
        reviewEpoch++; clearTimeout(timer);
        sessionStorage.removeItem(`steam-import:${connection.id}:${connection.generation}`);
        active=undefined; get('review').hidden=true; get('resume').hidden=true;
      } else { save(); await writes; }
      paused=true; loadVersion++; reviewEpoch++; clearTimeout(timer);
      await closeDialog(exitDialog); await closeDialog(dialog);
      if (abort) await list();
    } catch(error) {
      get('exit-error').textContent = error instanceof Error ? error.message : 'Could not save your import. Try again.';
      get('exit-error').hidden=false;
    } finally { leaving=false; buttons.forEach(button=>button.disabled=false); }
  }
  listen(get('abort'),'click',()=>{void leave(true);});
  listen(get('save-exit'),'click',()=>{void leave(false);});
  let reviewEpoch = 0;
  let importing = false;
  const observer = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) more(); }, { root: scroller, rootMargin: '300px' });
  cleanup = () => { reviewEpoch++; controller.abort(); detailsController.abort(); clearInlineEditors(); clearTimeout(timer); clearTimeout(saveTimer); observer.disconnect(); };
  async function request<T>(path = '', method = 'GET', body?: unknown): Promise<T> {
    const response = await fetch(`/api/proxy/connections/steam${path}`, { method, signal,
      headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not save Steam settings. Try again.');
    return data;
  }
  function message(value: string, error = false) {
    if (signal.aborted) return;
    for (const name of ['message', 'review-message']) {
      const node = get(name); node.hidden = !value; node.textContent = value;
      node.toggleAttribute('data-error', error);
    }
  }
  function fail(error: unknown) { if (!signal.aborted) message(error instanceof Error ? error.message : 'Steam request failed.', true); }
  function paintCount(game: SteamGame) {
    const node = cards.get(game.app_id)?.querySelector<HTMLElement>('.steam-card-achievements');
    if (!node) return;
    const software = game.mapping_state === 'software';
    const loading = !software && game.achievement_count === null && game.achievement_state === 'loading';
    node.toggleAttribute('data-loading', loading);
    if (loading) node.setAttribute('role', 'img'); else node.removeAttribute('role');
    node.setAttribute('aria-label', loading ? 'Loading achievements' : '');
    node.textContent = software ? 'Software' : loading ? '' : game.achievement_total === 0 && game.achievement_count === 0 ? 'No achievements' : game.achievement_count === null ? 'Achievements unavailable'
      : `${game.achievement_count}${game.achievement_total !== null ? `/${game.achievement_total}` : ''} achievements`;
  }
  async function loadProgress(epoch: number, id: number, generation: number) {
    try {
      await request(`/${id}/background`, 'POST');
      while (epoch === reviewEpoch && !signal.aborted && !importing) {
        const result = await request<{ games: SteamGame[]; wishlist: SteamGame[] }>(`/${id}/achievement-progress`);
        if (epoch !== reviewEpoch || signal.aborted || importing) return;
        if (result.wishlist) { wishlist = result.wishlist; if (step === 2) renderWishlist(); }
        for (const value of result.games) { const game = byId.get(value.app_id); if (game) { Object.assign(game, value); paintCount(game); } }
        if (!reviewGames && (draft.sort === 'completion' || selectedOnly)) render(); else paintSelections();
        if (!games.some(steamProgressPending) && !wishlist.some(game => game.mapping_state === 'pending')) return;
        await new Promise<void>(resolve => { const finish = () => { clearTimeout(wait); signal.removeEventListener('abort', finish); resolve(); }; const wait = setTimeout(finish, 1500); signal.addEventListener('abort', finish, {once:true}); });
      }
      return;
    } catch { if (signal.aborted || importing) return; }
    const pending = sortSteamGames(games.filter(steamProgressPending), 'playtime');
    while (pending.length && epoch === reviewEpoch && !signal.aborted && !importing) {
      const exposed = pending.filter(game => cards.has(game.app_id) && (() => {
        const rect = cards.get(game.app_id)!.getBoundingClientRect(); return rect.bottom >= 0 && rect.top <= innerHeight;
      })()).slice(0, 4);
      const batch = exposed.length ? exposed : pending.slice(0, 4);
      const ids = new Set(batch.map(game => game.app_id));
      for (let index = pending.length - 1; index >= 0; index--) if (ids.has(pending[index].app_id)) pending.splice(index, 1);
      try {
        const result = await request<{ games: SteamGame[] }>(`/${id}/achievements`, 'POST', { generation, apps: [...ids] });
        if (epoch !== reviewEpoch || signal.aborted || importing) return;
        for (const value of result.games) {
          const game = byId.get(value.app_id);
          if (!game) continue;
          Object.assign(game, value); paintCount(game);
          if (value.achievement_state === 'loading' || value.achievement_refreshing) pending.push(game);
        }
      } catch {
        if (epoch !== reviewEpoch || signal.aborted || importing) return;
        for (const game of batch) { game.achievement_state = 'unavailable'; paintCount(game); }
      }
      // Only automatic selections change. Explicit card choices stay in draft.overrides.
      if (!reviewGames && (draft.sort === 'completion' || selectedOnly)) render(); else paintSelections();
      if (pending.length && pending.every(game => game.achievement_refresh_due === false)) {
        await new Promise<void>(resolve => {
          const finish = () => { clearTimeout(wait); signal.removeEventListener('abort', finish); resolve(); };
          const wait = setTimeout(finish, 1500); signal.addEventListener('abort', finish, { once: true });
        });
      }
    }
  }
  function listen(node: EventTarget, event: string, fn: EventListener) { node.addEventListener(event, fn, { signal }); }
  const payload = () => ({ ...draft, generation: active!.generation });
  function save() {
    if (!active) return;
    const id = active.id, body = payload();
    writes = writes.catch(() => {}).then(() => request(`/${id}/draft`, 'PUT', body));
    void writes.catch(fail);
  }
  function changed() {
    if (active) {
      try { sessionStorage.setItem(`steam-import:${active.id}:${active.generation}`, JSON.stringify(draft)); } catch {}
    }
    clearTimeout(saveTimer); saveTimer = setTimeout(save, 350);
    if (selectedOnly && !reviewGames) render(); else paintSelections();
  }
  function paintSelections() {
    for (const [id, card] of cards) {
      if (!card.isConnected || card.parentElement?.hidden) continue;
      const game = byId.get(id);
      if (game) {
        const selected = steamSelected(game, draft);
        if (card.getAttribute('aria-pressed') !== String(selected)) card.setAttribute('aria-pressed', String(selected));
        const unavailable = game.mapping_state === 'unavailable' || game.mapping_state === 'software';
        const wrapper = card.parentElement!;
        wrapper.dataset.unavailable = String(unavailable);
        card.setAttribute('aria-disabled', String(unavailable));
        card.title = game.mapping_state === 'software' ? 'Software cannot be imported' : unavailable ? 'Unavailable' : '';
        const choices = wrapper.querySelector<HTMLElement>('.steam-game-controls')!;
        choices.classList.toggle('is-visible',selected && !unavailable);
        choices.inert = !selected || unavailable;
        choices.setAttribute('aria-hidden',String(!selected || unavailable));
        const status = wrapper.querySelector<HTMLSelectElement>('.steam-game-status')!;
        const value = steamStatus(game, draft);
        if (status.value !== value) { status.value = value; status.dispatchEvent(new Event('input', { bubbles: true })); }
        if (status.disabled !== unavailable) status.disabled = unavailable;
        let editor = inlineEditors.get(id);
        if (combinedList() && selected && !unavailable) {
          if (editor && editor.status !== value) { editor.controller.abort(); editor.node.remove(); inlineEditors.delete(id); editor = undefined; }
          if (!editor) {
            const editorController = new AbortController();
            const node = reviewFields(game, editorController, 'inline'); node.classList.add('steam-inline-fields');
            editor = { node, status: value, controller: editorController }; inlineEditors.set(id, editor); wrapper.append(node);
          }
        }
        if (editor) { editor.node.hidden = !combinedList() || !selected || unavailable; editor.node.inert = editor.node.hidden; }
      }

    }
    const selected = reviewGames ? reviewGames.filter(game=>game.mapping_state!=='unavailable' && game.mapping_state!=='software').length : games.filter(game => steamSelected(game, draft)).length;
    get('selected').textContent = reviewGames ? `${selected.toLocaleString()} games` : `${selected.toLocaleString()} of ${games.length.toLocaleString()} selected`;
    get('total').textContent = games.length.toLocaleString();
    get('selected-total').textContent = selected.toLocaleString();
    get('view-all').setAttribute('aria-pressed',String(!selectedOnly));
    get('view-selected').setAttribute('aria-pressed',String(selectedOnly));
    get<HTMLButtonElement>('import').disabled = importing;
    get<HTMLButtonElement>('next').disabled = importing;
    if (step === 2) get('selected').textContent = `${wishlist.filter(game => steamWishlistSelected(game, draft, !!active?.sync_wishlist)).length.toLocaleString()} wishlist games selected`;
    const settled = games.filter(game => game.mapping_state === 'software' || (game.achievement_state !== 'loading' && !game.achievement_refreshing)).length;
    get<HTMLProgressElement>('progress').max = games.length || 1; get<HTMLProgressElement>('progress').value = settled;
    get('progress-label').textContent = `${settled} / ${games.length}`;
    get('progress-region').hidden = settled >= games.length;
  }
  function card(game: SteamGame) {
    let node = cards.get(game.app_id);
    if (node) return node.parentElement!;
    node = document.createElement('button'); node.type = 'button'; node.className = 'steam-card';
    node.setAttribute('aria-label', game.name); node.dataset.appId = game.app_id;
    const cover = document.createElement('span'); cover.className = 'steam-cover'; cover.setAttribute('aria-hidden', 'true');
    const fallback = document.createElement('span'); fallback.textContent = game.name.slice(0, 1); cover.append(fallback);
    const image = document.createElement('img'); image.alt = ''; image.loading = 'lazy'; image.decoding = 'async';
    if (game.poster) { image.src = game.poster; cover.replaceChildren(image); }
    listen(image, 'error', () => { cover.replaceChildren(fallback); });
    const title = document.createElement('span'); title.className = 'steam-card-title';
    title.textContent = game.name;
    const stats = document.createElement('span'); stats.className = 'steam-card-stats';
    stats.id = `steam-stats-${game.app_id}`; node.setAttribute('aria-describedby', stats.id);
    const minutes = game.playtime_minutes;
    stats.textContent = minutes >= 60 ? `${(minutes / 60).toLocaleString(undefined, { maximumFractionDigits: 1 })} hours played` : minutes === 0 ? 'Not played' : `${minutes} minutes played`;
    const count = document.createElement('span'); count.className = 'steam-card-achievements'; stats.append(count);
    const check = document.createElement('span'); check.className = 'steam-card-check'; check.textContent = '✓'; check.setAttribute('aria-hidden', 'true');
    node.append(cover, title, stats, check);
    if (game.last_played) {
      const last = document.createElement('span'); last.className = 'steam-card-last-played'; last.id = `steam-last-played-${game.app_id}`;
      const label = document.createElement('span'); label.textContent = 'Last played';
      const date = document.createElement('span'); date.textContent = formatDate(new Date(game.last_played * 1000).toLocaleDateString('sv-SE'));
      last.append(label, date); node.append(last); node.setAttribute('aria-describedby', `${stats.id} ${last.id}`);
    }
    listen(node, 'click', () => { if (game.mapping_state === 'unavailable' || game.mapping_state === 'software') return; draft.overrides[game.app_id] = !steamSelected(game, draft); changed(); });
    cards.set(game.app_id, node);
    const wrapper = document.createElement('article'); wrapper.className = 'steam-game'; wrapper.append(node);
    const choices = document.createElement('div'); choices.className = 'steam-game-controls';
    const choicesInner = document.createElement('div'); choicesInner.className='steam-game-controls-inner'; choices.append(choicesInner);
    const status = document.createElement('select'); status.className = 'steam-game-status'; status.setAttribute('aria-label', `Import status for ${game.name}`);
    for (const [value, label] of [['completed','Completed'],['playing','Playing'],['paused','Paused'],['dropped','Dropped'],['planning','Planning to play']]) {
      const option = document.createElement('option'); option.value = value; option.textContent = label; status.append(option);
    }
    listen(status, 'change', () => {
      chooseSteamStatus(game, draft, status.value as SteamDraft['import_status']);
      changed();
    });
    choicesInner.append(status);
    wrapper.append(choices);
    const lastPlayed = node.querySelector<HTMLElement>('.steam-card-last-played');
    if (lastPlayed) wrapper.append(lastPlayed);
    paintCount(game);
    return wrapper;
  }
  function render(preservePosition = true) {
    const focused = document.activeElement as HTMLElement | null;
    if (preservePosition || !orderedGames.length) orderedGames = sortSteamGames(games, draft.sort);
    visibleGames = filterSteamGames(orderedGames, query).filter(game => !selectedOnly || steamSelected(game,draft));
    const window = visibleGames.slice(0, limit);
    const bounds = scroller.getBoundingClientRect();
    const anchor = preservePosition ? steamScrollAnchor(cards.values(), new Set(window.map(game=>game.app_id)), bounds) : undefined;
    reconcileSteamCards(get('grid'), window.map(card));
    get('empty').hidden = visibleGames.length > 0;
    get('empty').querySelector('h3')!.textContent = query.trim() ? 'No games found' : selectedOnly ? 'No selected games' : 'Your library is empty';
    get('empty').querySelector('p')!.textContent = query.trim() ? 'Try another name.' : selectedOnly ? 'Choose games from your library to import.' : 'No games are available to import.';
    get('clear-search').hidden = !query.trim() && !selectedOnly;
    get('clear-search').textContent = query.trim() ? 'Clear search' : 'View all games';
    get('results').textContent = query.trim() ? `${visibleGames.length.toLocaleString()} ${visibleGames.length === 1 ? 'game' : 'games'} found` : `${games.length.toLocaleString()} games in your library`;
    const moreNode = get('more'); moreNode.hidden = !!reviewGames || limit >= visibleGames.length;
    observer.disconnect(); if (!moreNode.hidden) observer.observe(moreNode);
    paintSelections();
    if (focused && host?.contains(focused)) focused.focus({ preventScroll: true });
    if (focused?.closest('.steam-game[hidden]')) get('view-selected').focus({preventScroll:true});
    if (preservePosition && anchor?.element.isConnected && !anchor.element.parentElement?.hidden) scroller.scrollTop += anchor.element.getBoundingClientRect().top - anchor.top;
    else if (!preservePosition) scroller.scrollTop = 0;
  }
  function more() {
    if (limit >= visibleGames.length) return;
    limit += 80;
    reconcileSteamCards(get('grid'), visibleGames.slice(0, limit).map(card));
    get('more').hidden = limit >= visibleGames.length;
    paintSelections();
  }
  listen(get('more'), 'click', more);
  function controls() {
    get<HTMLInputElement>('minutes').value = String(draft.minimum_playtime);
    get<HTMLInputElement>('achievements').value = String(draft.minimum_achievements);
    get<HTMLInputElement>('playtime-enabled').checked = draft.playtime_enabled;
    get<HTMLInputElement>('achievements-enabled').checked = draft.achievements_enabled;
    get<HTMLSelectElement>('sort').value = draft.sort;
  }
  for (const [name, field] of [['minutes', 'minimum_playtime'], ['achievements', 'minimum_achievements']] as const) {
    listen(get(name), 'input', () => { const input = get<HTMLInputElement>(name); if (input.validity.valid && input.value !== '') { draft[field] = Number(input.value); changed(); } });
  }
  for (const [name, field] of [['playtime-enabled', 'playtime_enabled'], ['achievements-enabled', 'achievements_enabled']] as const) {
    listen(get(name), 'change', () => { draft[field] = get<HTMLInputElement>(name).checked; changed(); });
  }
  listen(get('sort'), 'change', () => { draft.sort = get<HTMLSelectElement>('sort').value as SteamDraft['sort']; render(); changed(); });
  function search() { query = get<HTMLInputElement>('search').value; limit = 80; render(false); }
  listen(get('search'), 'input', search);
  listen(get('clear-search'), 'click', () => { if (!query.trim()) selectedOnly = false; get<HTMLInputElement>('search').value = ''; search(); get('search').focus(); });
  for (const [name,value] of [['view-all',false],['view-selected',true]] as const) {
    listen(get(name),'click',()=>{selectedOnly=value;limit=80;render(false);});
  }
  listen(get('reset'), 'click', () => { draft = defaultSteamDraft(); controls(); render(); changed(); });
  async function load(id: number) {
    if (paused) return;
    const version = loadVersion;
    const data = await request<Review>(`/${id}`);
    if (signal.aborted || paused || version !== loadVersion) return;
    active = data;
    get('review').hidden = data.imported;
    get('resume').hidden = data.imported;
    if (data.imported) return;
    if (!dialog.open) showDialog(dialog);
    const status = data.job?.status;
    const available = data.library.length > 0 || status === 'completed';
    get('loading').hidden = available;
    get('picker').hidden = !available;
    get('retry').hidden = status !== 'failed' && status !== 'cancelled';
    if (available) {
      const epoch = ++reviewEpoch; importing = false;
      clearInlineEditors();
      wishlist = data.wishlist || []; get('wishlist-grid').replaceChildren(); wishlistCards.clear(); wishlistLimit = 80;
      get('wishlist-error').textContent = data.wishlist_error || ''; get('wishlist-error').hidden = !data.wishlist_error;
      get('wishlist-retry').hidden = !data.wishlist_error;
      games = data.library; byId = new Map(games.map(game => [game.app_id, game]));
      draft = { ...defaultSteamDraft(), ...data.draft };
      try {
        const local = sessionStorage.getItem(`steam-import:${data.id}:${data.generation}`);
        if (local) draft = { ...draft, ...JSON.parse(local) };
      } catch {}
      setStep(0); get('grid').replaceChildren(); cards.clear(); orderedGames = []; limit = 80;
      controls(); render(); message(data.job?.warnings.join(' ') || '');
      if (!dialog.open) showDialog(dialog);
      void loadProgress(epoch, data.id, data.generation);
    } else if (status === 'failed' || status === 'cancelled') {
      get('loading').hidden = true; message(data.job?.error || 'Loading was interrupted. Retry to continue.', true);
    } else {
      const progress = get('loading').querySelector('progress')!;
      progress.max = data.job?.total || 1; progress.value = data.job?.current || 0;
      get('loading-count').textContent = `${data.job?.current || 0} of ${data.job?.total || 0} games`;
      timer = setTimeout(() => void load(id).catch(fail), 1500);
    }
  }
  async function list() {
    const connections = await request<Connection[]>();
    if (signal.aborted) return;
    get('connections').replaceChildren();
    get('connect').hidden = connections.length > 0;
    for (const conn of connections) {
      const row = document.createElement('div'); row.className = 'space-y-3';
      const title = document.createElement('a'); title.href = `https://steamcommunity.com/profiles/${conn.steam_id}`;
      title.textContent = conn.imported ? `${conn.selected_count} games connected` : 'Import in progress'; title.className = 'block text-sm text-zinc-300 hover:text-white';
      row.append(title);
      if (conn.imported) {
        for (const [field, label] of [['sync_achievements', 'Achievement sync'], ['sync_playtime', 'Playtime sync'], ['auto_add_games', 'Automatically add new games'], ['sync_wishlist', 'Wishlist sync (one way)']] as const) {
          const wrapper = document.createElement('label'); wrapper.className = 'inline-flex items-center mr-6 text-sm text-zinc-300';
          const input = document.createElement('input'); input.type = 'checkbox'; input.checked = conn[field];
          listen(input, 'change', () => {
            const inputs = row.querySelectorAll<HTMLInputElement>('input');
            inputs.forEach(control => { control.disabled = true; });
            void request(`/${conn.id}`, 'PATCH', { sync_achievements: conn.sync_achievements, sync_playtime: conn.sync_playtime,
              auto_add_games: conn.auto_add_games, sync_wishlist: conn.sync_wishlist, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, [field]: input.checked })
              .then(() => { conn[field] = input.checked; message('Steam settings saved.'); })
              .catch(error => { input.checked = conn[field]; fail(error); }).finally(() => { inputs.forEach(control => { control.disabled = false; }); });
          });
          wrapper.append(input, label); row.append(wrapper);
        }
      }
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = conn.imported ? 'Disconnect' : 'Cancel import';
      remove.className = 'text-sm text-zinc-400 hover:text-red-400';
      listen(remove, 'click', () => { remove.disabled = true; clearTimeout(timer); reviewEpoch++;
        void fetch(`/api/proxy/auth/connections/${conn.id}`, { method: 'DELETE', signal }).then(async response => {
          if (!response.ok) throw new Error('Could not disconnect Steam. Try again.');
          get('review').hidden = true; get('resume').hidden = true; void closeDialog(dialog); active = undefined; await list(); message('Steam disconnected.');
        }).catch(fail).finally(() => { remove.disabled = false; });
      });
      if (conn.imported) row.append(remove);
      get('connections').append(row);
      if (!conn.imported) await load(conn.id);
    }
  }
  listen(get('connect'), 'submit', event => {
    event.preventDefault(); paused=false; loadVersion++; const form = get<HTMLFormElement>('connect'); const submit = form.querySelector<HTMLButtonElement>('button[type=submit]')!;
    submit.disabled = true; submit.textContent = 'Checking Steam…'; message('');
    const values = new FormData(form);
    void request<Connection>('', 'POST', { profile: values.get('profile'), sync_achievements: values.has('achievements'), sync_playtime: values.has('playtime'),
      auto_add_games: values.has('auto-add'), sync_wishlist: values.has('wishlist'), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone })
      .then(() => list()).catch(fail).finally(() => { submit.disabled = false; submit.textContent = 'Connect Steam'; });
  });
  function setStep(next: 0 | 1 | 2) {
    step = next;
    get('controls').hidden = next !== 0; get('grid').hidden = next !== 0;
    get('details').hidden = next !== 1; get('wishlist').hidden = next !== 2;
    get('empty').hidden = next !== 0 || visibleGames.length > 0;
    get('more').hidden = next !== 0 || limit >= visibleGames.length;
    get('next').hidden = next === 2; get('import').hidden = next !== 2; get('back').hidden = next === 0;
    get('details-more').hidden = next !== 1 || reviewLimit >= (reviewGames?.length || 0);
    get('dialog').querySelector('h2')!.textContent = next === 2 ? 'Choose your wishlist' : next === 1 ? 'Dates & ratings' : 'Choose your games';
    if (next === 0) { reviewGames = null; detailsController.abort(); get('details').replaceChildren(); }
    if (next === 2) renderWishlist();
    scroller.scrollTop = 0; paintSelections();
  }
  function renderWishlist() {
    const query = get<HTMLInputElement>('wishlist-search').value;
    const visible = filterSteamGames(wishlist, query);
    const grid = get('wishlist-grid');
    const nodes: HTMLElement[] = [];
    for (const game of visible.slice(0, wishlistLimit)) {
      let wrapper = wishlistCards.get(game.app_id);
      if (!wrapper) {
        wrapper = document.createElement('article'); wrapper.className = 'steam-game';
        const button = document.createElement('button'); button.type = 'button'; button.className = 'steam-card';
        button.dataset.wishlistApp = game.app_id; button.setAttribute('aria-label', game.name);
        button.setAttribute('aria-pressed', String(steamWishlistSelected(game, draft, !!active?.sync_wishlist)));
        button.disabled = ['software','unavailable'].includes(game.mapping_state || '');
        const cover = document.createElement('span'); cover.className = 'steam-cover';
        const image = document.createElement('img'); image.alt = ''; image.loading = 'lazy'; image.decoding = 'async'; image.src = game.poster;
        cover.append(image);
        const title = document.createElement('span'); title.className = 'steam-card-title'; title.textContent = game.name;
        const check = document.createElement('span'); check.className = 'steam-card-check'; check.textContent = '\u2713'; check.setAttribute('aria-hidden','true');
        button.append(cover, title, check);
        button.addEventListener('click', () => {
          const latest = wishlist.find(row => row.app_id === game.app_id) || game;
          draft.wishlist_overrides[game.app_id] = !steamWishlistSelected(latest, draft, !!active?.sync_wishlist);
          button.setAttribute('aria-pressed', String(steamWishlistSelected(latest, draft, !!active?.sync_wishlist))); changed();
        });
        wrapper.append(button); wishlistCards.set(game.app_id, wrapper);
      }
      const button = wrapper.querySelector<HTMLButtonElement>('button')!;
      button.disabled = ['software','unavailable'].includes(game.mapping_state || '');
      button.setAttribute('aria-pressed', String(steamWishlistSelected(game, draft, !!active?.sync_wishlist)));
      button.setAttribute('aria-label', game.name);
      wrapper.querySelector('.steam-card-title')!.textContent = game.name;
      const image = wrapper.querySelector<HTMLImageElement>('img')!;
      if (image.getAttribute('src') !== game.poster) image.src = game.poster;
      nodes.push(wrapper);
    }
    reconcileSteamCards(grid, nodes);
    get('wishlist-more').hidden = visible.length <= wishlistLimit;
    get('wishlist-empty').hidden = visible.length !== 0 || !get('wishlist-error').hidden;
    get('wishlist-empty').textContent = query.trim() ? 'No games found.' : 'Your wishlist is empty.';
    paintSelections();
  }
  listen(get('wishlist-search'), 'input', () => { wishlistLimit = 80; renderWishlist(); });
  listen(get('wishlist-more'), 'click', () => { wishlistLimit += 80; renderWishlist(); });
  for (const [name, selected] of [['wishlist-all',true],['wishlist-none',false]] as const)
    listen(get(name), 'click', () => { for (const game of wishlist) draft.wishlist_overrides[game.app_id] = selected; renderWishlist(); changed(); });
  listen(get('wishlist-retry'), 'click', () => {
    if (!active) return;
    get<HTMLButtonElement>('wishlist-retry').disabled = true;
    void request<{wishlist:SteamGame[]}>(`/${active.id}/wishlist`, 'POST').then(data => {
      wishlist = data.wishlist; get('wishlist-error').hidden = true; get('wishlist-retry').hidden = true; renderWishlist();
    }).catch(fail).finally(() => { get<HTMLButtonElement>('wishlist-retry').disabled = false; });
  });
  function reviewFields(game: SteamGame, editorController: AbortController, prefix: string) {
    const fields = document.createElement('div'); fields.className = 'steam-review-fields';
    const status = steamStatus(game,draft);
    if (status === 'completed' || status === 'playing') {
      const label = document.createElement('label'); label.className = 'steam-review-field';
      const caption = document.createElement('span'); caption.textContent = status === 'completed' ? 'Completion date' : 'Start date';
      const control = get<HTMLTemplateElement>('date-template').content.cloneNode(true) as DocumentFragment;
      const iso = control.querySelector<HTMLInputElement>('[data-date-iso]')!;
      const display = control.querySelector<HTMLInputElement>('[data-date-display]')!;
      display.id = `steam-date-${prefix}-${game.app_id}`; label.htmlFor = display.id;
      display.setAttribute('aria-label', `${caption.textContent} for ${game.name}`);
      control.querySelector('[data-date-picker]')?.setAttribute('aria-label', `Choose ${caption.textContent.toLowerCase()} for ${game.name}`);
      iso.value = status === 'completed' ? finishDate(game) || '' : draft.start_dates[game.app_id] || '';
      const clear = document.createElement('button'); clear.type = 'button'; clear.textContent = 'Clear'; clear.className = 'steam-text-button'; clear.setAttribute('aria-label', `Clear ${caption.textContent.toLowerCase()} for ${game.name}`);
      const dateField = document.createElement('div'); dateField.className='steam-date-field';
      const dateHead = document.createElement('div'); dateHead.className='steam-date-field-head';
      dateHead.append(caption,clear); label.append(dateHead,control); dateField.append(label); fields.append(dateField);
      const refresh = setupCalendarDateInputs(label,editorController.signal);
      const update = () => { (status==='completed'?draft.completion_dates:draft.start_dates)[game.app_id] = iso.value || null; changed(); };
      iso.addEventListener('change',update,{signal:editorController.signal});
      display.addEventListener('input',()=>{if(display.validity.valid) update();},{signal:editorController.signal});
      clear.addEventListener('click',()=>{iso.value='';refresh();update();},{signal:editorController.signal});
      refresh();
    }
    const rating = document.createElement('button'); rating.type='button'; rating.className='steam-quick-rate';
    const value = document.createElement('span');
    const paintRating = () => {
      const score=draft.ratings[game.app_id]; value.textContent=score == null ? '? / 10' : `${score.toFixed(1)} / 10`;
      rating.setAttribute('aria-label',`Rate ${game.name}${score == null ? '' : `. Current score: ${score}`}`);
    };
    rating.append(value); paintRating();
    const reviewSignal = editorController.signal;
    rating.addEventListener('click',()=>{
      const quickRate = (window as any).anyListQuickRate;
      if (!quickRate) return;
      quickRate({title:game.name,posterSrc:game.poster,score:draft.ratings[game.app_id] ?? null,onScore:(score:number|null)=>{
        if (reviewSignal.aborted) return;
        draft.ratings[game.app_id]=score;paintRating();changed();
      }});
    },{signal:reviewSignal});
    fields.append(rating); return fields;
  }
  function appendDetails() {
    const container = get('details');
    for (const game of reviewGames!.slice(container.children.length, reviewLimit)) {
      const row = document.createElement('article'); row.className = 'steam-review-game';
      const image = document.createElement('img'); if (game.poster) image.src = game.poster; image.alt = ''; image.loading = 'lazy';
      const info = document.createElement('div'); info.className = 'steam-review-info';
      const title = document.createElement('h3'); title.textContent = game.name;
      const last = document.createElement('p'); last.textContent = game.last_played ? `Last played · ${formatDate(new Date(game.last_played*1000).toLocaleDateString('sv-SE'))}` : 'Last played · Unknown';
      info.append(title,last); row.append(image,info);
      row.append(reviewFields(game, detailsController, 'review')); container.append(row);
    }
    get('details-more').hidden = reviewLimit >= reviewGames!.length;
  }
  function invalidInlineDate() {
    for (const [app, editor] of inlineEditors) {
      const game = byId.get(app);
      if (!game || !steamSelected(game, draft)) continue;
      const input = editor.node.querySelector<HTMLInputElement>('input:invalid');
      if (input) return { app, input };
    }
  }
  function validateInlineDates() {
    const invalid = invalidInlineDate();
    if (!invalid) return true;
    query = ''; selectedOnly = false; get<HTMLInputElement>('search').value = '';
    limit = Math.max(limit, orderedGames.findIndex(game => game.app_id === invalid.app) + 1);
    render(false); invalid.input.reportValidity(); return false;
  }
  function showDetails() {
    detailsController.abort(); detailsController = new AbortController(); reviewLimit = 40;
    get('details').replaceChildren(); appendDetails(); setStep(1);
  }
  listen(get('next'),'click',()=>{
    if (step === 1) {
      const invalid = get('details').querySelector<HTMLInputElement>('input:invalid');
      if (invalid) { invalid.reportValidity(); return; }
      setStep(2); changed(); get('dialog').querySelector<HTMLElement>('h2')!.focus({preventScroll:true}); return;
    }
    if (combinedList() && !validateInlineDates()) return;
    reviewGames = sortSteamGames(games.filter(game=>steamSelected(game,draft)),draft.sort);
    if (combinedList()) setStep(2); else showDetails();
    changed();
    get('dialog').querySelector<HTMLElement>('h2')!.focus({preventScroll:true});
  });
  listen(get('back'),'click',()=>{if (step === 2 && !combinedList()) { showDetails(); return; } setStep(0);render(false);get('search').focus();});
  listen(get('details-more'),'click',()=>{reviewLimit+=40;appendDetails();});
  listen(get('retry'), 'click', () => { if (active) void request(`/${active.id}/retry`, 'POST').then(() => load(active!.id)).catch(fail); });
  listen(get('import'), 'click', () => {
    if (!active || importing) return;
    if (step !== 2 || !reviewGames) { message('Review your selected games before importing.', true); return; }
    const invalid = get('details').querySelector<HTMLInputElement>('input:invalid');
    if (invalid) { invalid.reportValidity(); return; }
    importing = true; reviewEpoch++;
    const selectedGames = reviewGames.filter(game => game.mapping_state !== 'unavailable' && game.mapping_state !== 'software');
    const selection = { ...payload(), selected_wishlist: wishlist.filter(game => steamWishlistSelected(game, draft, !!active?.sync_wishlist)).map(game => game.app_id), selected_apps: selectedGames.map(game => game.app_id),
      completion_dates: Object.fromEntries(selectedGames.map(game => [game.app_id, finishDate(game)])),
      start_dates:Object.fromEntries(selectedGames.map(game=>[game.app_id,draft.start_dates[game.app_id]||null])) };
    clearTimeout(saveTimer); const button = get<HTMLButtonElement>('import'); button.disabled = true; button.textContent = 'Importing…';
    get('scroll').inert = true; get('controls').inert = true; get<HTMLButtonElement>('back').disabled = true;
    get<HTMLButtonElement>('later').disabled = true;
    void writes.catch(() => {}).then(() => request<{ imported: number; skipped?: number }>(`/${active!.id}/import`, 'POST', selection))
      .then(async result => {
        sessionStorage.removeItem(`steam-import:${active!.id}:${active!.generation}`);
        get('review').hidden = true; get('resume').hidden = true; void closeDialog(dialog); active = undefined; await list();
        message(`Imported ${result.imported} games. Steam now uses your account sync schedule.`);
      })
      .catch(async error => { if (active) await load(active.id).catch(fail); fail(error); })
      .finally(() => {
        importing = false; get<HTMLButtonElement>('back').disabled = false; get('scroll').inert = false; get('controls').inert = false;
        get<HTMLButtonElement>('later').disabled = false;
        button.textContent = 'Import selected'; paintSelections();
      });
  });
  void list().catch(fail);
}
document.addEventListener('astro:page-load', init);
document.addEventListener('astro:before-swap', () => cleanup?.());
init();
