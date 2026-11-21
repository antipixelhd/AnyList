import {defaultGameCategories} from '../lib/list-categories';
import {setupScoreSteppers} from '../lib/scoreStepper';
import {applyResponsiveArtwork} from '../lib/responsive-artwork';
import {formatDate} from '../lib/date-time';

type Game = {work_id: number; title: string; status: string; score: number | null; total_seconds: number | null;
  start_date: string | null; finish_date: string | null; image_url: string | null; existing: boolean};
type IgnoredGame = {title: string; reason: string; image_url: string | null};
type Summary = {games: Game[]; ignored: IgnoredGame[]};
const endpoint = '/api/proxy/tracking/games/import/backloggd';
const categories = defaultGameCategories().categories;

export function mountBackloggdImport(root: HTMLElement) {
  const lifecycle = new AbortController();
  const {signal} = lifecycle;
  const zone = root.querySelector<HTMLButtonElement>('[data-backloggd-upload]')!;
  const fileInput = root.querySelector<HTMLInputElement>('[data-backloggd-file]')!;
  const loading = root.querySelector<HTMLElement>('[data-game-import-loading]')!;
  const message = root.querySelector<HTMLElement>('[data-backloggd-error]')!;
  const form = root.querySelector<HTMLFormElement>('[data-backloggd-review]')!;
  const heading = root.querySelector<HTMLElement>('[data-backloggd-heading]')!;
  const groups = root.querySelector<HTMLElement>('[data-backloggd-groups]')!;
  const reset = root.querySelector<HTMLButtonElement>('[data-backloggd-reset]')!;
  const save = root.querySelector<HTMLButtonElement>('[data-backloggd-save]')!;
  const groupTemplate = root.querySelector<HTMLTemplateElement>('[data-backloggd-group-template]')!;
  const scoreTemplate = root.querySelector<HTMLTemplateElement>('[data-backloggd-score-template]')!;
  let scoreBindings: AbortController | undefined;
  let summary: Summary | undefined;
  let busy = false;

  function report(text = '') {
    message.textContent = text;
    message.hidden = !text;
  }

  async function post(path: string, body: BodyInit): Promise<Summary> {
    const response = await fetch(path, {method: 'POST', signal, body, headers: {'Content-Type': 'application/json'}});
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(typeof payload?.detail === 'string' ? payload.detail : 'Backloggd import is unavailable. Try again.');
    }
    return payload as Summary;
  }

  function addGroup(label: string, count: number) {
    const group = groupTemplate.content.firstElementChild!.cloneNode(true) as HTMLDetailsElement;
    const trigger = group.querySelector('summary')!;
    trigger.querySelector('[data-label]')!.textContent = label;
    const countNode = trigger.querySelector('[data-count]')!;
    countNode.textContent = String(count);
    // Empty categories keep their count without offering an empty disclosure.
    if (!count) {
      const row = document.createElement('div');
      row.className = 'backloggd-empty-group';
      row.append(trigger.querySelector('[data-label]')!, countNode);
      groups.append(row);
      return null;
    }
    groups.append(group);
    return {list: group.querySelector('ul')!, countNode};
  }

  function addPoster(row: HTMLElement, imageUrl: string | null) {
    const slot = document.createElement('span');
    slot.className = 'backloggd-poster';
    slot.setAttribute('aria-hidden', 'true');
    if (imageUrl) slot.dataset.poster = imageUrl;
    row.prepend(slot);
  }

  groups.addEventListener('toggle', event => {
    const group = event.target;
    if (!(group instanceof HTMLDetailsElement) || !group.open) return;
    for (const slot of group.querySelectorAll<HTMLElement>('[data-poster]')) {
      const img = document.createElement('img');
      img.alt = '';
      img.width = 44;
      img.height = 60;
      img.loading = 'lazy';
      img.decoding = 'async';
      img.addEventListener('error', () => img.remove(), {once: true});
      applyResponsiveArtwork(img, slot.dataset.poster, {size: 'w92'});
      slot.append(img);
      delete slot.dataset.poster;
    }
  }, {capture: true, signal});

  function appendTitle(list: HTMLElement, game: {title: string; image_url: string | null}, reason?: string) {
    const row = document.createElement('li');
    const text = document.createElement('span');
    text.textContent = game.title;
    if (reason) {
      const detail = document.createElement('small');
      detail.textContent = reason;
      text.append(detail);
    }
    row.append(text);
    addPoster(row, game.image_url);
    list.append(row);
  }

  function detailsFor(game: Game) {
    return [game.existing && game.score !== null ? `Score ${game.score} / 10` : '',
      game.existing && game.total_seconds !== null ? 'Playtime' : '',
      game.start_date ? `Started ${formatDate(game.start_date)}` : '',
      game.finish_date ? `Finished ${formatDate(game.finish_date)}` : ''].filter(Boolean).join(' · ');
  }

  function render(result: Summary, saved: boolean) {
    summary = result;
    const {games, ignored} = result;
    scoreBindings?.abort();
    scoreBindings = new AbortController();
    groups.replaceChildren();
    const number = games.length;
    const gameCount = `${number} ${number === 1 ? 'game' : 'games'}`;
    const updates = games.filter(game => game.existing);
    heading.textContent = saved ? updates.length ? `${number - updates.length} added · ${updates.length} updated` : `${gameCount} imported`
      : number ? `${gameCount} ready to import` : 'No games ready to import';
    for (const category of categories) {
      const matches = games.filter(game => !game.existing && game.status === category.id);
      const group = addGroup(category.label, matches.length);
      if (group) for (const game of matches) appendTitle(group.list, game, detailsFor(game));
    }
    if (updates.length) {
      const group = addGroup(saved ? 'Existing games updated' : 'Existing games to update', updates.length)!;
      for (const game of updates) appendTitle(group.list, game, detailsFor(game));
    }
    const rated = games.filter(game => game.score !== null);
    const scores = addGroup(saved ? 'Scores imported' : 'Scores to import', rated.length);
    if (scores) for (const game of rated) {
      if (saved) {
        appendTitle(scores.list, game, `${game.score} / 10`);
        continue;
      }
      const row = scoreTemplate.content.firstElementChild!.cloneNode(true) as HTMLElement;
      const input = row.querySelector('input')!;
      input.id = `backloggd-score-${game.work_id}`;
      input.setAttribute('aria-label', `Score for ${game.title}`);
      input.value = String(game.score);
      input.dataset.workId = String(game.work_id);
      const label = row.querySelector('label')!;
      label.htmlFor = input.id;
      label.textContent = game.title;
      addPoster(row, game.image_url);
      scores.list.append(row);
    }
    const skipped = addGroup('Ignored games', ignored.length);
    if (skipped) for (const game of ignored) appendTitle(skipped.list, game, game.reason);
    if (!saved && scores) {
      const inputs = [...scores.list.querySelectorAll<HTMLInputElement>('input')];
      scores.list.addEventListener('input', () => {
        scores.countNode.textContent = String(inputs.filter(input => input.validity.valid && input.valueAsNumber > 0).length);
      }, {signal: scoreBindings.signal});
      setupScoreSteppers(scores.list, scoreBindings.signal);
    }
    zone.hidden = true;
    form.hidden = false;
    save.hidden = saved || !number;
    save.textContent = `Import ${gameCount}`;
    heading.focus({preventScroll: true});
  }

  async function preview(file: File) {
    if (busy) return;
    report();
    if (!file.name.toLowerCase().endsWith('.json')) return report('Choose a Toolkittd JSON file.');
    if (file.size > 5 * 1024 * 1024) return report('Choose a JSON file up to 5 MB.');
    busy = true;
    zone.disabled = true;
    zone.hidden = true;
    loading.hidden = false;
    try {
      render(await post(`${endpoint}/preview`, file), false);
    } catch (error) {
      if (!signal.aborted) {
        report((error as Error).message);
        zone.hidden = false;
        zone.disabled = false;
        zone.focus({preventScroll: true});
      }
    } finally {
      loading.hidden = true;
      zone.disabled = false;
      busy = false;
    }
  }

  zone.addEventListener('click', () => fileInput.click(), {signal});
  fileInput.addEventListener('change', () => {
    const file = fileInput.files?.[0];
    fileInput.value = '';
    if (file) void preview(file);
  }, {signal});
  zone.addEventListener('dragover', event => {
    event.preventDefault();
    zone.dataset.drag = 'true';
  }, {signal});
  zone.addEventListener('dragleave', () => delete zone.dataset.drag, {signal});
  zone.addEventListener('drop', event => {
    event.preventDefault();
    delete zone.dataset.drag;
    const files = event.dataTransfer?.files;
    if (files?.length === 1) void preview(files[0]);
    else if (files?.length) report('Choose one Backloggd JSON file at a time.');
  }, {signal});
  reset.addEventListener('click', () => {
    summary = undefined;
    scoreBindings?.abort();
    groups.replaceChildren();
    form.hidden = true;
    zone.hidden = false;
    report();
    zone.focus();
  }, {signal});
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !summary || save.hidden) return;
    report();
    const inputs = [...groups.querySelectorAll<HTMLInputElement>('input[data-work-id]')];
    const invalid = inputs.find(input => !input.validity.valid);
    if (invalid) {
      invalid.closest('details')!.open = true;
      invalid.reportValidity();
      return;
    }
    const scores = new Map(inputs.map(input => [Number(input.dataset.workId), input.valueAsNumber || null]));
    const games = summary.games.map(({work_id, status, total_seconds, start_date, finish_date, existing}) => ({
      work_id, status, total_seconds, start_date, finish_date, existing, score: scores.get(work_id) ?? null,
    }));
    const ignored = summary.ignored;
    busy = true;
    save.disabled = reset.disabled = true;
    for (const input of inputs) input.disabled = true;
    save.textContent = 'Importing…';
    try {
      const result = await post(endpoint, JSON.stringify({games}));
      render({...result, ignored: [...ignored, ...result.ignored]}, true);
    } catch (error) {
      if (!signal.aborted) {
        report((error as Error).message);
        save.textContent = `Import ${games.length} ${games.length === 1 ? 'game' : 'games'}`;
      }
    } finally {
      busy = false;
      save.disabled = reset.disabled = false;
      for (const input of inputs) input.disabled = false;
    }
  }, {signal});

  return () => {
    lifecycle.abort();
    scoreBindings?.abort();
  };
}
