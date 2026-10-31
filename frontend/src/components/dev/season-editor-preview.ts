import {formatDate, localToday} from '../../lib/date-time';
import {setupCalendarDateInputs} from '../../lib/calendar-date-input';
import {advanceEpisode, completeSeason, initialEntry, nextSeason, setSeasonDate, totalProgress, type PreviewEntry, type Season} from './season-editor-state';

const storageKey = 'anylist:dev:season-editor:v1';
let controller: AbortController | undefined;
const copy = <T>(value: T): T => structuredClone(value);

function setup() {
  controller?.abort();
  const root = document.querySelector<HTMLElement>('[data-season-lab]');
  if (!root) return;
  controller = new AbortController();
  const {signal} = controller;
  const find = <T extends Element = HTMLElement>(selector: string) => root.querySelector<T>(selector)!;
  const design = root.dataset.design!;
  let saved = initialEntry();
  try {
    const stored = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
    // Only accept this fixture's exact shape; an old preview must not break the lab.
    if (stored && typeof stored.score === 'string' && typeof stored.notes === 'string' && typeof stored.start === 'string' && typeof stored.favorite === 'boolean'
      && Array.isArray(stored.seasons) && stored.seasons.length === 5 && stored.seasons.every((s: Season, i: number) =>
        s.number === i+1 && s.total === saved.seasons[i].total && Number.isInteger(s.watched) && s.watched >= 0 && s.watched <= s.total
        && typeof s.completed === 'boolean' && typeof s.date === 'string' && typeof s.inferred === 'string'
        && (!s.date || Boolean(formatDate(s.date))) && [null,'manual','automatic','inferred','connection'].includes(s.source))) saved = stored;
  } catch { /* Storage is optional in the fixture preview. */ }
  let draft = copy(saved);
  let active = 2;
  let selected = new Set([2]);
  let dateOpen = false;
  let pendingSource: Season['source'] = 'manual';
  let dateEdited = false;
  const history: PreviewEntry[] = [];
  const list = find('[data-season-list]');
  const panel = find('[data-date-panel]');
  const dateForm = find<HTMLFormElement>('[data-date-form]');
  const dateInput = panel.querySelector<HTMLInputElement>('[data-date-iso]')!;
  const dateDisplay = panel.querySelector<HTMLInputElement>('[data-date-display]')!;
  const startInput = find<HTMLInputElement>('[name=start_date]');
  const refreshDates = setupCalendarDateInputs(root, signal);
  const sourceName = (season: Season) => ({connection:'Trakt',automatic:'Automatic',manual:'',inferred:'Inferred'}[season.source || 'manual']);
  const targets = () => draft.seasons.filter(s => design === 'batch' ? selected.has(s.number) : s.number === active);
  const feedback = (text: string) => {find('.se-feedback').textContent = text;};
  const checkpoint = () => {history.push(copy(draft)); if (history.length > 30) history.shift();};
  const clone = (id: string) => (find<HTMLTemplateElement>(id).content.firstElementChild!.cloneNode(true) as HTMLElement);

  function syncList() {
    find('[data-list-progress]').textContent = `${totalProgress(saved)} / 62`;
    find('[data-list-status]').textContent = saved.seasons.every(s => s.completed) ? 'Completed' : 'Watching';
  }

  function syncDatePanel() {
    const seasons = targets();
    const season = seasons[0];
    find('[data-date-heading]').textContent = design === 'batch'
      ? `${seasons.length} ${seasons.length === 1 ? 'season' : 'seasons'} selected`
      : `Season ${active}${season && sourceName(season) ? ` · ${sourceName(season)}` : ''}`;
    dateInput.value = season && seasons.every(s => s.date === season.date) ? season.date : '';
    dateDisplay.placeholder = seasons.length > 1 && !seasons.every(s => s.date === season?.date) ? 'Mixed dates' : '';
    pendingSource = 'manual';
    refreshDates();
    // The shared input intentionally removes placeholders when syncing a normal date.
    if (seasons.length > 1 && !seasons.every(s => s.date === season?.date)) dateDisplay.placeholder = 'Mixed dates';
    const infer = find<HTMLButtonElement>('[data-action=infer]');
    infer.hidden = !seasons.length || !seasons.every(s => s.completed && s.inferred);
    const apply = find<HTMLButtonElement>('[data-apply]');
    apply.textContent = design === 'batch' && seasons.length > 1 ? `Apply to ${seasons.length} seasons` : 'Apply';
    apply.disabled = !seasons.length;
    dateDisplay.disabled = !seasons.length;
    panel.querySelector<HTMLButtonElement>('[data-date-picker]')!.disabled = !seasons.length;
    for (const action of ['today','clear-date']) find<HTMLButtonElement>(`[data-action=${action}]`).disabled = !seasons.length;
  }

  function render() {
    dateEdited = false;
    // Move the live date control out before replacing row nodes, preserving its picker.
    find('.se-season-layout').append(panel);
    list.replaceChildren();
    const total = totalProgress(draft);
    find('[data-progress]').replaceChildren(document.createTextNode(`${total} `));
    const maximum = document.createElement('small'); maximum.textContent = '/ 62'; find('[data-progress]').append(maximum);
    const next = nextSeason(draft);
    find('[data-status]').textContent = next ? 'Watching' : 'Completed';
    root!.querySelectorAll<HTMLElement>('[data-next-label]').forEach(node => {node.textContent = next ? `Complete season ${next.number}` : 'All seasons completed';});
    root!.querySelectorAll<HTMLButtonElement>('[data-action=complete-next],[data-action=episode]').forEach(button => {button.disabled = !next;});
    find('[data-completed-count]').textContent = `${draft.seasons.filter(s => s.completed).length} / ${draft.seasons.length}`;
    find<HTMLButtonElement>('[data-action=undo]').disabled = !history.length;
    find('[data-action=favorite]').setAttribute('aria-pressed', String(draft.favorite));
    find<HTMLInputElement>('[data-score]').value = draft.score;
    find<HTMLTextAreaElement>('[data-notes]').value = draft.notes;
    startInput.value = draft.start;

    if (design === 'focus') {
      const tabs = find('.se-season-tabs'); tabs.replaceChildren();
      for (const season of draft.seasons) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = `S${season.number}`;
        button.dataset.season = String(season.number); button.dataset.completed = String(season.completed);
        button.setAttribute('aria-label', `Season ${season.number}${season.completed ? ', completed' : ''}`);
        button.setAttribute('aria-current', String(active === season.number)); tabs.append(button);
      }
      const season = draft.seasons[active-1];
      const card = clone('#se-focus-card'); card.querySelector('h4')!.textContent = `Season ${active}`;
      card.querySelector('[data-focus-status]')!.textContent = season.completed ? 'Completed' : season.watched ? 'Watching' : 'Not started';
      card.querySelector('[data-focus-episodes]')!.textContent = `${season.watched} / ${season.total} episodes`;
      card.querySelector('progress')!.value = season.watched / season.total * 100;
      const toggle = card.querySelector<HTMLButtonElement>('[data-focus-completion]')!;
      toggle.dataset.toggleSeason = String(active); toggle.setAttribute('aria-pressed', String(season.completed));
      toggle.querySelector('span:last-child')!.textContent = season.completed ? 'Completed' : 'Complete season';
      card.querySelector('[data-focus-source]')!.textContent = season.date ? `${formatDate(season.date)}${sourceName(season) ? ` · ${sourceName(season)}` : ''}` : season.completed ? 'Add date' : '';
      card.querySelector<HTMLButtonElement>('[data-action=previous]')!.disabled = active === 1;
      card.querySelector<HTMLButtonElement>('[data-action=next]')!.disabled = active === draft.seasons.length;
      list.append(card); panel.hidden = false;
    } else {
      for (const season of draft.seasons) {
        const row = clone(design === 'batch' ? '#se-batch-row' : '#se-season-row');
        row.querySelector('.se-season-name strong')!.textContent = `Season ${season.number}`;
        row.querySelector('.se-season-name>span')!.textContent = `${season.watched} / ${season.total} episodes`;
        if (design === 'batch') {
          row.dataset.selectSeason = String(season.number); row.setAttribute('aria-pressed', String(selected.has(season.number)));
          row.setAttribute('aria-label', `Season ${season.number}, ${season.completed ? 'completed' : 'not completed'}, ${season.date ? formatDate(season.date) : 'no date'}`);
          row.querySelector('.se-batch-date')!.textContent = season.date ? formatDate(season.date) : season.completed ? 'Add date' : '—';
        } else {
          const completion = row.querySelector<HTMLButtonElement>('[data-completion]')!;
          completion.dataset.toggleSeason = String(season.number);
          completion.setAttribute('aria-pressed', String(season.completed));
          completion.setAttribute('aria-label', `Season ${season.number} completed`);
          const edit = row.querySelector<HTMLButtonElement>('[data-edit-date]')!;
          edit.dataset.editSeason = String(season.number);
          edit.setAttribute('aria-label', `Season ${season.number} completion date${season.date ? `, ${formatDate(season.date)}` : ''}`);
          edit.title = sourceName(season);
          edit.classList.toggle('has-date', Boolean(season.date));
          edit.querySelector('span')!.textContent = season.date ? formatDate(season.date) : season.completed ? 'Add date' : 'Set date';
          edit.setAttribute('aria-expanded', String(dateOpen && active === season.number));
          row.classList.toggle('is-editing', dateOpen && active === season.number);
        }
        list.append(row);
        if (design === 'rows' && dateOpen && active === season.number) list.append(panel);
      }
      panel.hidden = design === 'rows' && !dateOpen;
    }
    syncDatePanel();
  }

  function mutate(action: () => void, message = '') {
    checkpoint(); action(); render(); feedback(message);
  }
  function focusSeasonButton() {
    find<HTMLButtonElement>(design === 'focus' ? `[data-season="${active}"]` : `[data-edit-season="${active}"]`)?.focus({preventScroll:true});
  }
  function closeStatus() {find('#se-status-menu').hidden = true; find('[data-action=status]').setAttribute('aria-expanded','false');}

  root.addEventListener('click', event => {
    const button = (event.target as Element).closest<HTMLButtonElement>('button');
    if (!button || button.disabled) return;
    const action = button.dataset.action;
    // Keep a typed/picked date when another editor control changes the draft.
    const changesDraft = button.dataset.season || button.dataset.editSeason || button.dataset.selectSeason || button.dataset.toggleSeason
      || ['complete-next','episode','favorite','score-up','score-down','previous','next','select-missing','select-all','dismiss-date'].includes(action || '');
    if (changesDraft && dateEdited && !applyDates()) return;
    if (button.dataset.season) {active = Number(button.dataset.season); render(); focusSeasonButton(); return;}
    if (button.dataset.editSeason) {
      const number = Number(button.dataset.editSeason);
      dateOpen = number === active ? !dateOpen : true; active = number; render();
      if (dateOpen) dateDisplay.focus({preventScroll:true}); else focusSeasonButton();
      return;
    }
    if (button.dataset.selectSeason) {
      const number = Number(button.dataset.selectSeason);
      if (selected.has(number)) selected.delete(number); else selected.add(number);
      render(); find<HTMLButtonElement>(`[data-select-season="${number}"]`).focus({preventScroll:true}); return;
    }
    if (button.dataset.toggleSeason) {
      const number = Number(button.dataset.toggleSeason);
      mutate(() => {
        const season = draft.seasons[number-1];
        if (season.completed) {season.completed = false; season.watched = Math.max(0,season.total-1); season.date = ''; season.source = null;}
        else completeSeason(season);
      });
      find<HTMLButtonElement>(`[data-toggle-season="${number}"]`).focus({preventScroll:true}); return;
    }
    if (action === 'status') {const menu = find('#se-status-menu'); menu.hidden = !menu.hidden; button.setAttribute('aria-expanded',String(!menu.hidden));}
    else if (action === 'complete-next' || action === 'episode') {
      closeStatus();
      const season = nextSeason(draft); if (!season) return;
      mutate(() => {if (action === 'episode') advanceEpisode(draft); else completeSeason(season);}, season.watched+1 >= season.total || action === 'complete-next' ? `Season ${season.number} completed` : 'Progress updated');
    } else if (action === 'today') {
      dateInput.value = localToday(); pendingSource = 'manual'; refreshDates();
    } else if (action === 'infer') {
      pendingSource = 'inferred'; dateInput.value = targets()[0]?.inferred || ''; refreshDates();
    } else if (action === 'clear-date') {
      mutate(() => targets().forEach(season => setSeasonDate(season,'')), 'Date cleared');
    } else if (action === 'dismiss-date') {dateOpen = false; render(); focusSeasonButton();}
    else if (action === 'previous' || action === 'next') {active += action === 'previous' ? -1 : 1; render(); focusSeasonButton();}
    else if (action === 'select-missing') {selected = new Set(draft.seasons.filter(s => s.completed && !s.date).map(s => s.number)); render();}
    else if (action === 'select-all') {selected = selected.size === draft.seasons.length ? new Set() : new Set(draft.seasons.map(s => s.number)); render();}
    else if (action === 'favorite') {mutate(() => {draft.favorite = !draft.favorite;});}
    else if (action === 'score-up' || action === 'score-down') {mutate(() => {draft.score = Math.min(10,Math.max(0,Number(draft.score)+(action === 'score-up' ? .1 : -.1))).toFixed(1);});}
    else if (action === 'undo') {const previous = history.pop(); if (previous) {draft = previous; render(); feedback('Undone');}}
    else if (action === 'save') {
      if (!find<HTMLInputElement>('[data-score]').reportValidity() || !find<HTMLInputElement>('#se-start').reportValidity()) return;
      // Apply an edited date before saving, even if its Apply button wasn't pressed.
      if (!panel.hidden && dateEdited) {if (!applyDates()) return;}
      saved = copy(draft);
      try {sessionStorage.setItem(storageKey, JSON.stringify(saved));} catch { /* Still usable without storage. */ }
      syncList(); feedback('Saved in preview');
    } else if (action === 'reset') {
      saved = initialEntry(); draft = copy(saved); history.length = 0; active = 2; selected = new Set([2]); dateOpen = false;
      try {sessionStorage.removeItem(storageKey);} catch { /* Optional storage. */ }
      render(); syncList(); feedback('Reset');
    } else if (action === 'close') {draft = copy(saved); history.length = 0; render(); find('.season-editor').hidden = true; feedback(''); find<HTMLButtonElement>('[data-action=open]').focus();}
    else if (action === 'open') {find('.season-editor').hidden = false; find<HTMLButtonElement>('[data-action=save]').focus({preventScroll:true});}
  }, {signal});
  function applyDates() {
    if (!dateDisplay.reportValidity()) return false;
    const seasons = targets(); if (!seasons.length) return false;
    const value = dateInput.value;
    const source = pendingSource;
    mutate(() => seasons.forEach(season => setSeasonDate(season,source === 'inferred' ? season.inferred : value,source)), 'Date updated');
    return true;
  }
  panel.addEventListener('input', () => {dateEdited = true; pendingSource = 'manual';}, {signal});
  dateInput.addEventListener('change', () => {dateEdited = true; pendingSource = 'manual';}, {signal});
  panel.addEventListener('click', event => {if ((event.target as Element).closest('[data-action=today],[data-action=infer]')) dateEdited = true;}, {signal});
  dateForm.addEventListener('submit', event => {event.preventDefault(); applyDates();}, {signal});
  find<HTMLInputElement>('[data-score]').addEventListener('change', event => {
    const input = event.target as HTMLInputElement; if (input.checkValidity()) {checkpoint(); draft.score = input.value; find<HTMLButtonElement>('[data-action=undo]').disabled = false;}
  }, {signal});
  find<HTMLTextAreaElement>('[data-notes]').addEventListener('input', event => {draft.notes = (event.target as HTMLTextAreaElement).value;}, {signal});
  find('#se-start').addEventListener('input', () => {draft.start = startInput.value;}, {signal});
  startInput.addEventListener('change', () => {draft.start = startInput.value;}, {signal});
  document.addEventListener('pointerdown', event => {if (!(event.target as Element).closest('.se-status-wrap')) closeStatus();}, {signal});
  root.addEventListener('keydown', event => {
    if (event.key === 'Escape') {closeStatus(); if (design === 'rows' && dateOpen) {dateOpen = false; render(); focusSeasonButton();}}
  }, {signal});
  render(); syncList();
}

document.addEventListener('astro:before-swap', () => controller?.abort());
document.addEventListener('astro:page-load', setup);
