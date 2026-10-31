import { defaultSteamDraft, sortSteamGames, steamSelected } from '../lib/steam-import';
import type { SteamDraft, SteamGame } from '../lib/steam-import';

type Connection = { id: number; steam_id: string; imported: boolean; sync_achievements: boolean; sync_playtime: boolean; selected_count: number; generation: number };
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
  const datesSeen = new Set<string>();
  const datesPending = new Set<string>();
  let dateLoading = false;
  const dateObserver = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      const app = (entry.target as HTMLElement).dataset.appId!;
      dateObserver.unobserve(entry.target);
      if (!datesSeen.has(app)) { datesSeen.add(app); datesPending.add(app); }
    }
    void loadDates();
  }, { rootMargin: '100px' });
  const observer = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) more(); }, { rootMargin: '500px' });
  cleanup = () => { controller.abort(); clearTimeout(timer); clearTimeout(saveTimer); observer.disconnect(); dateObserver.disconnect(); };
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
      if (game) card.setAttribute('aria-pressed', String(steamSelected(game, draft)));
    }
    get('selected').textContent = `${games.filter(game => steamSelected(game, draft)).length} of ${games.length} selected`;
  }
  function card(game: SteamGame) {
    let node = cards.get(game.app_id);
    if (node) return node;
    node = document.createElement('button'); node.type = 'button'; node.className = 'steam-card';
    node.setAttribute('aria-label', game.name); node.dataset.appId = game.app_id;
    const image = document.createElement('img'); image.src = game.poster; image.alt = ''; image.loading = 'lazy'; image.decoding = 'async';
    listen(image, 'error', () => { image.hidden = true; });
    const title = document.createElement('span'); title.className = 'steam-card-title';
    title.textContent = game.name + (game.release_date ? ` (${game.release_date.slice(0, 4)})` : '');
    const stats = document.createElement('span'); stats.className = 'steam-card-stats';
    const minutes = game.playtime_minutes;
    stats.textContent = `${minutes >= 60 ? `${(minutes / 60).toLocaleString(undefined, { maximumFractionDigits: 1 })} h` : `${minutes} min`} · ${game.achievement_count === null ? 'Achievements unavailable' : `${game.achievement_count}${game.achievement_total ? `/${game.achievement_total}` : ''} achievements`}`;
    const check = document.createElement('span'); check.className = 'steam-card-check'; check.textContent = '✓'; check.setAttribute('aria-hidden', 'true');
    node.append(image, title, stats, check);
    listen(node, 'click', () => { draft.overrides[game.app_id] = !steamSelected(game, draft); changed(); });
    cards.set(game.app_id, node);
    if (!game.release_date) dateObserver.observe(node);
    return node;
  }
  function render() {
    const ordered = sortSteamGames(games, draft.sort);
    get('grid').replaceChildren(...ordered.slice(0, limit).map(card));
    const moreNode = get('more'); moreNode.hidden = limit >= games.length;
    observer.disconnect(); if (!moreNode.hidden) observer.observe(moreNode);
    paintSelections();
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
  }
  for (const [name, field] of [['minutes', 'minimum_playtime'], ['achievements', 'minimum_achievements']] as const) {
    listen(get(name), 'input', () => { const input = get<HTMLInputElement>(name); if (input.validity.valid && input.value !== '') { draft[field] = Number(input.value); changed(); } });
  }
  for (const [name, field] of [['playtime-enabled', 'playtime_enabled'], ['achievements-enabled', 'achievements_enabled']] as const) {
    listen(get(name), 'change', () => { draft[field] = get<HTMLInputElement>(name).checked; changed(); });
  }
  listen(get('sort'), 'change', () => { draft.sort = get<HTMLSelectElement>('sort').value as SteamDraft['sort']; render(); changed(); });
  listen(get('reset'), 'click', () => { draft = defaultSteamDraft(); controls(); render(); changed(); });
  async function load(id: number) {
    const data = await request<Review>(`/${id}`);
    if (signal.aborted) return;
    active = data;
    get('review').hidden = data.imported;
    if (data.imported) return;
    const status = data.job?.status;
    get('loading').hidden = status === 'completed';
    get('picker').hidden = status !== 'completed';
    get('retry').hidden = status !== 'failed' && status !== 'cancelled';
    if (status === 'completed') {
      games = data.library; byId = new Map(games.map(game => [game.app_id, game]));
      draft = { ...defaultSteamDraft(), ...data.draft };
      try {
        const local = sessionStorage.getItem(`steam-import:${data.id}:${data.generation}`);
        if (local) draft = { ...draft, ...JSON.parse(local) };
      } catch {}
      cards.clear(); dateObserver.disconnect(); limit = 80;
      controls(); render(); message(data.job?.warnings.join(' ') || '');
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
      title.textContent = conn.imported ? `${conn.selected_count} games connected` : 'Import in progress'; title.className = 'text-sm text-zinc-300 hover:text-white';
      row.append(title);
      if (conn.imported) {
        for (const [field, label] of [['sync_achievements', 'Achievement sync'], ['sync_playtime', 'Playtime sync']] as const) {
          const wrapper = document.createElement('label'); wrapper.className = 'inline-flex items-center mr-6 text-sm text-zinc-300';
          const input = document.createElement('input'); input.type = 'checkbox'; input.checked = conn[field];
          listen(input, 'change', () => {
            const inputs = row.querySelectorAll<HTMLInputElement>('input');
            inputs.forEach(control => { control.disabled = true; });
            void request(`/${conn.id}`, 'PATCH', { sync_achievements: conn.sync_achievements, sync_playtime: conn.sync_playtime, [field]: input.checked })
              .then(() => { conn[field] = input.checked; message('Steam settings saved.'); })
              .catch(error => { input.checked = conn[field]; fail(error); }).finally(() => { inputs.forEach(control => { control.disabled = false; }); });
          });
          wrapper.append(input, label); row.append(wrapper);
        }
      }
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = conn.imported ? 'Disconnect' : 'Cancel import';
      remove.className = 'text-sm text-zinc-400 hover:text-red-400';
      listen(remove, 'click', () => { remove.disabled = true; clearTimeout(timer);
        void fetch(`/api/proxy/auth/connections/${conn.id}`, { method: 'DELETE', signal }).then(async response => {
          if (!response.ok) throw new Error('Could not disconnect Steam. Try again.');
          get('review').hidden = true; active = undefined; await list(); message('Steam disconnected.');
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
    void request<Connection>('', 'POST', { profile: values.get('profile'), sync_achievements: values.has('achievements'), sync_playtime: values.has('playtime') })
      .then(() => list()).catch(fail).finally(() => { submit.disabled = false; submit.textContent = 'Connect Steam'; });
  });
  listen(get('retry'), 'click', () => { if (active) void request(`/${active.id}/retry`, 'POST').then(() => load(active!.id)).catch(fail); });
  listen(get('import'), 'click', () => {
    if (!active) return;
    clearTimeout(saveTimer); const button = get<HTMLButtonElement>('import'); button.disabled = true; button.textContent = 'Importing…';
    void writes.catch(() => {}).then(() => request<{ imported: number; skipped?: number }>(`/${active!.id}/import`, 'POST', payload()))
      .then(async result => {
        sessionStorage.removeItem(`steam-import:${active!.id}:${active!.generation}`);
        get('review').hidden = true; active = undefined; await list();
        message(`Imported ${result.imported} games.${result.skipped ? ` ${result.skipped} existing add-ons or editions could not be added as independent games.` : ''} Steam now uses your account sync schedule.`);
      })
      .catch(fail).finally(() => { button.disabled = false; button.textContent = 'Import selected'; });
  });
  void list().catch(fail);
}
document.addEventListener('astro:page-load', init);
document.addEventListener('astro:before-swap', () => cleanup?.());
init();
