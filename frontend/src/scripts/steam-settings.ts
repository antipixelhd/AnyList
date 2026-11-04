import { defaultSteamDraft, sortSteamGames, steamSelected, steamProgressPending } from '../lib/steam-import';
import type { SteamDraft, SteamGame } from '../lib/steam-import';
import { showDialog, closeDialog } from '../lib/ui-motion';
import { setupCalendarDateInputs } from '../lib/calendar-date-input';
import { formatDate } from '../lib/date-time';

type Connection = { id: number; steam_id: string; imported: boolean; sync_achievements: boolean; sync_playtime: boolean; auto_add_games: boolean; selected_count: number; generation: number };
type Review = Connection & { library: SteamGame[]; draft: Partial<SteamDraft>; job: { status: string; current: number; total: number; error: string | null; warnings: string[] } | null };

let cleanup: (() => void) | undefined;
function init() {
  cleanup?.();
  const host = document.querySelector<HTMLElement>('[data-steam-settings]');
  if (!host) return;
  const controller = new AbortController();
  const { signal } = controller;
  const get = <T extends HTMLElement = HTMLElement>(name: string) => host.querySelector<T>(`[data-steam-${name}]`)!;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let saveTimer: ReturnType<typeof setTimeout> | undefined;
  let draft = defaultSteamDraft();
  let active: Review | undefined;
  let games: SteamGame[] = [];
  let byId = new Map<string, SteamGame>();
  let limit = 80;
  let writes: Promise<unknown> = Promise.resolve();
  const cards = new Map<string, HTMLButtonElement>();
  const dialog = get<HTMLDialogElement>('dialog');
  const dateDialog = get<HTMLDialogElement>('date-dialog');
  const refreshDate = setupCalendarDateInputs(dateDialog, signal);
  let dateApp: string | undefined;
  let dateKind:'start'|'completion'='completion';
  const selectedDate=(game:SteamGame)=>draft.import_status==='playing'?draft.start_dates[game.app_id]||null:finishDate(game);
  const finishDate = (game: SteamGame) => Object.hasOwn(draft.completion_dates, game.app_id) ? draft.completion_dates[game.app_id]
    : game.last_played ? new Date(game.last_played * 1000).toLocaleDateString('sv-SE') : null;
  listen(get('resume'), 'click', () => showDialog(dialog));
  listen(get('later'), 'click', () => { clearTimeout(saveTimer); save(); void closeDialog(dialog); });
  listen(dialog, 'cancel', event => { event.preventDefault(); clearTimeout(saveTimer); save(); void closeDialog(dialog); });
  listen(get('cancel'), 'click', () => get('connections').querySelector<HTMLButtonElement>('button')?.click());
  listen(dateDialog, 'close', () => {
    if (!dateApp || !['save','discard'].includes(dateDialog.returnValue)) return;
    const input = dateDialog.querySelector<HTMLInputElement>('[data-date-iso]')!;
    (dateKind==='start'?draft.start_dates:draft.completion_dates)[dateApp] = dateDialog.returnValue === 'discard' ? null : input.value || null;
    changed(); dateApp = undefined;
  });
  const datesSeen = new Set<string>();
  const datesPending = new Set<string>();
  let dateLoading = false;
  let reviewEpoch = 0;
  let importing = false;
  const dateObserver = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      const app = (entry.target as HTMLElement).dataset.appId!;
      dateObserver.unobserve(entry.target);
      if (!datesSeen.has(app)) { datesSeen.add(app); datesPending.add(app); }
    }
    void loadDates();
  }, { rootMargin: '100px' });
  const observer = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) more(); }, { rootMargin: '500px' });
  cleanup = () => { reviewEpoch++; controller.abort(); clearTimeout(timer); clearTimeout(saveTimer); observer.disconnect(); dateObserver.disconnect(); };
  async function request<T>(path = '', method = 'GET', body?: unknown): Promise<T> {
    const response = await fetch(`/api/proxy/connections/steam${path}`, { method, signal,
      headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not save Steam settings. Try again.');
    return data;
  }
  function message(value: string, error = false) {
    if (signal.aborted) return;
    const node = get('message'); node.hidden = !value; node.textContent = value;
    node.classList.toggle('text-red-400', error);
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
    node.textContent = software ? 'Software' : loading ? '' : game.achievement_count === null ? 'Achievements unavailable'
      : `${game.achievement_count}${game.achievement_total !== null ? `/${game.achievement_total}` : ''} achievements`;
  }
  async function loadProgress(epoch: number, id: number, generation: number) {
    try {
      await request(`/${id}/background`, 'POST');
      while (epoch === reviewEpoch && !signal.aborted && !importing) {
        const result = await request<{ games: SteamGame[] }>(`/${id}/achievement-progress`);
        if (epoch !== reviewEpoch || signal.aborted || importing) return;
        for (const value of result.games) { const game = byId.get(value.app_id); if (game) { Object.assign(game, value); paintCount(game); } }
        if (draft.sort === 'completion') render(); else paintSelections();
        if (!games.some(steamProgressPending)) return;
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
      if (draft.sort === 'completion') render(); else paintSelections();
      if (pending.length && pending.every(game => game.achievement_refresh_due === false)) {
        await new Promise<void>(resolve => {
          const finish = () => { clearTimeout(wait); signal.removeEventListener('abort', finish); resolve(); };
          const wait = setTimeout(finish, 1500); signal.addEventListener('abort', finish, { once: true });
        });
      }
    }
  }
  async function loadDates() {
    if (dateLoading || signal.aborted) return;
    dateLoading = true;
    try {
      while (datesPending.size && !signal.aborted) {
        const apps = [...datesPending].slice(0, 8);
        for (const app of apps) datesPending.delete(app);
        const result = await request<Record<string, { release_date: string | null }>>('/metadata', 'POST', { apps });
        for (const [app, info] of Object.entries(result)) {
          const game = byId.get(app);
          if (!game || !info.release_date) continue;
          game.release_date = info.release_date;
          const title = cards.get(app)?.querySelector('.steam-card-title');
          if (title) title.textContent = `${game.name} (${info.release_date.slice(0, 4)})`;
        }
      }
    } catch { /* Optional public metadata never blocks the import. */ }
    finally { dateLoading = false; }
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
    paintSelections();
  }
  function paintSelections() {
    for (const [id, card] of cards) {
      const game = byId.get(id);
      if (game) {
        card.setAttribute('aria-pressed', String(steamSelected(game, draft)));
        const unavailable = game.mapping_state === 'unavailable' || game.mapping_state === 'software';
        card.setAttribute('aria-disabled', String(unavailable));
        card.title = game.mapping_state === 'software' ? 'Software cannot be imported' : unavailable ? 'Unavailable' : '';
      }
      const date = card.parentElement?.querySelector<HTMLButtonElement>('.steam-completion');
      if (date && game) {
        date.hidden = !steamSelected(game, draft) || !['completed','playing'].includes(draft.import_status);
        const value = selectedDate(game); date.textContent = value ? `${draft.import_status==='playing'?'Started':'Completed'} ${formatDate(value)}` : draft.import_status==='playing'?'Set start date':'Set completion date';
      }
    }
    get('selected').textContent = `${games.filter(game => steamSelected(game, draft)).length} of ${games.length} selected`;
    const settled = games.filter(game => game.mapping_state === 'software' || (game.achievement_state !== 'loading' && !game.achievement_refreshing)).length;
    get<HTMLProgressElement>('progress').max = games.length || 1; get<HTMLProgressElement>('progress').value = settled;
    get('progress-label').textContent = `${settled} / ${games.length}`;
  }
  function card(game: SteamGame) {
    let node = cards.get(game.app_id);
    if (node) return node.parentElement!;
    node = document.createElement('button'); node.type = 'button'; node.className = 'steam-card';
    node.setAttribute('aria-label', game.name); node.dataset.appId = game.app_id;
    const image = document.createElement('img'); image.src = game.poster; image.alt = ''; image.loading = 'lazy'; image.decoding = 'async';
    listen(image, 'error', () => { image.hidden = true; });
    const title = document.createElement('span'); title.className = 'steam-card-title';
    title.textContent = game.name + (game.release_date ? ` (${game.release_date.slice(0, 4)})` : '');
    const stats = document.createElement('span'); stats.className = 'steam-card-stats';
    stats.id = `steam-stats-${game.app_id}`; node.setAttribute('aria-describedby', stats.id);
    const minutes = game.playtime_minutes;
    stats.textContent = minutes >= 60 ? `${(minutes / 60).toLocaleString(undefined, { maximumFractionDigits: 1 })} h` : `${minutes} min`;
    const count = document.createElement('span'); count.className = 'steam-card-achievements'; stats.append(count);
    const check = document.createElement('span'); check.className = 'steam-card-check'; check.textContent = '✓'; check.setAttribute('aria-hidden', 'true');
    node.append(image, title, stats, check);
    listen(node, 'click', () => { if (game.mapping_state === 'unavailable' || game.mapping_state === 'software') return; draft.overrides[game.app_id] = !steamSelected(game, draft); changed(); });
    cards.set(game.app_id, node);
    const wrapper = document.createElement('article'); wrapper.className = 'steam-game'; wrapper.append(node);
    const date = document.createElement('button'); date.type = 'button'; date.className = 'steam-completion';
    listen(date, 'click', () => {
      dateApp = game.app_id; dateKind=draft.import_status==='playing'?'start':'completion'; dateDialog.returnValue = '';
      const label=dateKind==='start'?'Start date':'Completion date';
      dateDialog.querySelector('h2')!.textContent=label;
      dateDialog.querySelector('[data-date-display]')?.setAttribute('aria-label',label);
      dateDialog.querySelector<HTMLInputElement>('[data-date-iso]')!.value = selectedDate(game) || '';
      refreshDate(); showDialog(dateDialog);
    });
    wrapper.append(date);
    paintCount(game);
    if (!game.release_date) dateObserver.observe(node);
    return wrapper;
  }
  function render() {
    const focused = document.activeElement as HTMLElement | null;
    const anchor = [...cards.values()].find(node => { const rect = node.getBoundingClientRect(); return rect.top >= 0 && rect.top < innerHeight; });
    const top = anchor?.getBoundingClientRect().top;
    const ordered = sortSteamGames(games, draft.sort);
    get('grid').replaceChildren(...ordered.slice(0, limit).map(card));
    const moreNode = get('more'); moreNode.hidden = limit >= games.length;
    observer.disconnect(); if (!moreNode.hidden) observer.observe(moreNode);
    paintSelections();
    if (focused && host?.contains(focused)) focused.focus({ preventScroll: true });
    if (anchor?.isConnected && top !== undefined) dialog.scrollTop += anchor.getBoundingClientRect().top - top;
  }
  function more() {
    if (limit >= games.length) return;
    const previous = limit; limit += 80;
    get('grid').append(...sortSteamGames(games, draft.sort).slice(previous, limit).map(card));
    get('more').hidden = limit >= games.length;
    paintSelections();
  }
  listen(get('more'), 'click', more);
  function controls() {
    get<HTMLInputElement>('minutes').value = String(draft.minimum_playtime);
    get<HTMLInputElement>('achievements').value = String(draft.minimum_achievements);
    get<HTMLInputElement>('playtime-enabled').checked = draft.playtime_enabled;
    get<HTMLInputElement>('achievements-enabled').checked = draft.achievements_enabled;
    get<HTMLSelectElement>('sort').value = draft.sort;
    get<HTMLSelectElement>('status').value = draft.import_status;
  }
  for (const [name, field] of [['minutes', 'minimum_playtime'], ['achievements', 'minimum_achievements']] as const) {
    listen(get(name), 'input', () => { const input = get<HTMLInputElement>(name); if (input.validity.valid && input.value !== '') { draft[field] = Number(input.value); changed(); } });
  }
  for (const [name, field] of [['playtime-enabled', 'playtime_enabled'], ['achievements-enabled', 'achievements_enabled']] as const) {
    listen(get(name), 'change', () => { draft[field] = get<HTMLInputElement>(name).checked; changed(); });
  }
  listen(get('sort'), 'change', () => { draft.sort = get<HTMLSelectElement>('sort').value as SteamDraft['sort']; render(); changed(); });
  listen(get('status'), 'change', () => { draft.import_status = get<HTMLSelectElement>('status').value as SteamDraft['import_status']; changed(); });
  listen(get('reset'), 'click', () => { draft = defaultSteamDraft(); controls(); render(); changed(); });
  async function load(id: number) {
    const data = await request<Review>(`/${id}`);
    if (signal.aborted) return;
    active = data;
    get('review').hidden = data.imported;
    get('resume').hidden = data.imported;
    if (data.imported) return;
    const status = data.job?.status;
    const available = data.library.length > 0 || status === 'completed';
    get('loading').hidden = available;
    get('picker').hidden = !available;
    get('retry').hidden = status !== 'failed' && status !== 'cancelled';
    if (available) {
      const epoch = ++reviewEpoch; importing = false;
      games = data.library; byId = new Map(games.map(game => [game.app_id, game]));
      draft = { ...defaultSteamDraft(), ...data.draft };
      try {
        const local = sessionStorage.getItem(`steam-import:${data.id}:${data.generation}`);
        if (local) draft = { ...draft, ...JSON.parse(local) };
      } catch {}
      cards.clear(); dateObserver.disconnect(); limit = 80;
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
        for (const [field, label] of [['sync_achievements', 'Achievement sync'], ['sync_playtime', 'Playtime sync'], ['auto_add_games', 'Automatically add new games']] as const) {
          const wrapper = document.createElement('label'); wrapper.className = 'inline-flex items-center mr-6 text-sm text-zinc-300';
          const input = document.createElement('input'); input.type = 'checkbox'; input.checked = conn[field];
          listen(input, 'change', () => {
            const inputs = row.querySelectorAll<HTMLInputElement>('input');
            inputs.forEach(control => { control.disabled = true; });
            void request(`/${conn.id}`, 'PATCH', { sync_achievements: conn.sync_achievements, sync_playtime: conn.sync_playtime,
              auto_add_games: conn.auto_add_games, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, [field]: input.checked })
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
      row.append(remove); get('connections').append(row);
      if (!conn.imported) await load(conn.id);
    }
  }
  listen(get('connect'), 'submit', event => {
    event.preventDefault(); const form = get<HTMLFormElement>('connect'); const submit = form.querySelector<HTMLButtonElement>('button[type=submit]')!;
    submit.disabled = true; submit.textContent = 'Checking Steam…'; message('');
    const values = new FormData(form);
    void request<Connection>('', 'POST', { profile: values.get('profile'), sync_achievements: values.has('achievements'), sync_playtime: values.has('playtime'),
      auto_add_games: values.has('auto-add'), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone })
      .then(() => list()).catch(fail).finally(() => { submit.disabled = false; submit.textContent = 'Connect Steam'; });
  });
  listen(get('retry'), 'click', () => { if (active) void request(`/${active.id}/retry`, 'POST').then(() => load(active!.id)).catch(fail); });
  listen(get('import'), 'click', () => {
    if (!active) return;
    importing = true; reviewEpoch++;
    const selectedGames = games.filter(game => steamSelected(game, draft));
    const selection = { ...payload(), selected_apps: selectedGames.map(game => game.app_id),
      completion_dates: Object.fromEntries(selectedGames.map(game => [game.app_id, finishDate(game)])),
      start_dates:Object.fromEntries(selectedGames.map(game=>[game.app_id,draft.start_dates[game.app_id]||null])) };
    clearTimeout(saveTimer); const button = get<HTMLButtonElement>('import'); button.disabled = true; button.textContent = 'Importing…';
    void writes.catch(() => {}).then(() => request<{ imported: number; skipped?: number }>(`/${active!.id}/import`, 'POST', selection))
      .then(async result => {
        sessionStorage.removeItem(`steam-import:${active!.id}:${active!.generation}`);
        get('review').hidden = true; get('resume').hidden = true; void closeDialog(dialog); active = undefined; await list();
        message(`Imported ${result.imported} games. Steam now uses your account sync schedule.`);
      })
      .catch(error => { fail(error); if (active) void load(active.id).catch(fail); })
      .finally(() => { button.disabled = false; button.textContent = 'Import selected'; });
  });
  void list().catch(fail);
}
document.addEventListener('astro:page-load', init);
document.addEventListener('astro:before-swap', () => cleanup?.());
init();
