import {formatDate, localToday} from '../../lib/date-time';
import {setupCalendarDateInputs} from '../../lib/calendar-date-input';
import {setupScoreSteppers} from '../../lib/scoreStepper';
import {setupEditorSelect} from '../../lib/editor-select';
import {showDialog, closeDialog} from '../../lib/ui-motion';
import {statuses} from '../../lib/tracking-statuses';
import {completeSeason, initialWork, nextSeason, setSeasonDate, setPreviewProgress, totalProgress, type PreviewEntry, type PreviewMedia} from './season-editor-state';

type Fixture = {id: PreviewMedia; title: string; kind: string; poster: string; backdrop: string};
const storageKey = 'anylist:dev:season-editor:v2';
const kinds: PreviewMedia[] = ['series','movie','game'];
const copy = <T>(value: T): T => structuredClone(value);
let controller: AbortController | undefined;

function setup() {
  controller?.abort();
  const root = document.querySelector<HTMLElement>('[data-season-lab]');
  if (!root) return;
  controller = new AbortController();
  const {signal} = controller;
  const find = <T extends Element = HTMLElement>(selector: string) => root.querySelector<T>(selector)!;
  const dialog = find<HTMLDialogElement>('#season-preview-editor');
  const form = find<HTMLFormElement>('#sp-form');
  const field = (name: string) => form.elements.namedItem(name) as HTMLInputElement;
  const fixtures: Fixture[] = JSON.parse(find('#sp-fixtures').textContent!);
  let saved = Object.fromEntries(kinds.map(kind => [kind,initialWork(kind)])) as Record<PreviewMedia, PreviewEntry>;
  try {
    const stored = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
    if (stored && kinds.every(kind => {
      const entry = stored[kind];
      return entry && ['score','notes','start','finish','status','firstHours','achievements'].every(key=>typeof entry[key]==='string')
        && ['hours','rewatches','replays'].every(key=>Number.isFinite(entry[key])) && typeof entry.favorite==='boolean'
        && Array.isArray(entry.seasons) && entry.seasons.length===(kind==='series'?5:0)
        && entry.seasons.every((s: PreviewEntry['seasons'][number],i:number)=>s.number===i+1 && s.total===saved.series.seasons[i].total
          && Number.isInteger(s.watched) && s.watched>=0 && s.watched<=s.total && typeof s.completed==='boolean'
          && typeof s.date==='string' && (!s.date || Boolean(formatDate(s.date))) && typeof s.inferred==='string');
    })) saved = stored;
  } catch { /* The mock remains usable without browser storage. */ }
  let current: PreviewMedia = 'series';
  let draft = copy(saved.series);
  let opener: HTMLElement | undefined;
  const statusSelect = find<HTMLSelectElement>('#sp-status');
  const statusControl = setupEditorSelect(statusSelect.closest<HTMLElement>('[data-editor-select]')!,signal);
  const refreshDates = setupCalendarDateInputs(dialog,signal);
  setupScoreSteppers(dialog,signal);
  const sourceNames = {connection:'Trakt',automatic:'Automatic',manual:'',inferred:'Inferred'};
  const feedback = (message='') => {find('.sp-editor-feedback').textContent=message;};
  const sourceName = (season: PreviewEntry['seasons'][number]) => sourceNames[season.source || 'manual'];

  function syncRows() {
    for (const kind of kinds) {
      const entry = saved[kind];
      const label = entry.status==='playing'?'Playing':statuses.find(([key])=>key===entry.status)?.[1]||entry.status;
      const status = find(`[data-work-status="${kind}"]`); if (status) status.textContent=label;
      const score = find(`[data-work-score="${kind}"]`); if (score) score.textContent=entry.score;
      const progress = find(`[data-work-progress="${kind}"]`);
      if (progress) progress.textContent=kind==='series'?`${totalProgress(entry)} / 62`:kind==='game'?`${entry.hours} h`:entry.status==='completed'?'1 / 1':'0 / 1';
    }
  }

  function syncStatus() {
    const next = nextSeason(draft);
    const complete = [...statusSelect.options].find(option=>option.value==='completed')!;
    complete.textContent=current==='series'&&next?`Complete season ${next.number}`:'Completed';
    statusSelect.value=draft.status;
    statusControl.sync();
    find('[data-next-label]').textContent=next?`Complete season ${next.number}`:'All seasons completed';
    find<HTMLButtonElement>('[data-action=complete-next]').disabled=!next;
  }

  function syncSeasons() {
    if (current!=='series') return;
    find('[data-season-count]').textContent=`${draft.seasons.filter(season=>season.completed).length} / ${draft.seasons.length}`;
    field('progress').value=String(totalProgress(draft));
    for (const season of draft.seasons) {
      const number=season.number;
      const row=find<HTMLElement>(`[data-season-row="${number}"]`);
      row.dataset.completed=String(season.completed);
      find(`[data-season-progress="${number}"]`).textContent=`${season.watched} / ${season.total}`;
      const label=find(`[data-date-label="${number}"]`);
      if (label) {label.textContent=season.date?formatDate(season.date):'Finish date'; label.title=sourceName(season);}
      const completion=find(`[data-complete-season="${number}"]`);
      completion.setAttribute('aria-pressed',String(season.completed));
      const text=completion.querySelector('span:last-child:not(.ui-icon)');
      if (text) text.textContent=season.completed?'Completed':'Complete season';
      field(`season_date_${number}`).value=season.date;
      field(`season_score_${number}`).value=season.score || '';
      find<HTMLButtonElement>(`[data-clear-date="${number}"]`).disabled=!season.date;
      find<HTMLButtonElement>(`[data-infer="${number}"]`).hidden=!season.completed || !season.inferred || Boolean(season.date);
      find(`[data-date-source="${number}"]`).textContent=sourceName(season);
    }
    syncStatus();
  }

  function populate() {
    form.reset();
    const fixture=fixtures.find(item=>item.id===current)!;
    dialog.dataset.kind=current;
    // This is the app's existing game-layout switch, shared by all three mocks.
    dialog.dataset.editorDomain=current==='game'?'game':'movies-series';
    find('#sp-editor-title').textContent=fixture.title;
    find('[data-editor-kind]').textContent=fixture.kind;
    const setArtwork=(image:HTMLImageElement,src:string)=>{
      if (image.getAttribute('src')===src && image.complete && image.naturalWidth) {image.hidden=false;return;}
      image.hidden=true; image.src=src;
      if (image.complete && image.naturalWidth) image.hidden=false;
      else image.addEventListener('load',()=>{image.hidden=false;},{once:true,signal});
    };
    const poster=find<HTMLImageElement>('[data-editor-poster]'); setArtwork(poster,fixture.poster); poster.alt=`${fixture.title} poster`;
    setArtwork(find<HTMLImageElement>('[data-editor-backdrop]'),fixture.backdrop);
    dialog.querySelectorAll<HTMLElement>('[data-series-only]').forEach(node=>{node.hidden=current!=='series';});
    dialog.querySelectorAll<HTMLElement>('[data-game-only]').forEach(node=>{node.hidden=current!=='game';});
    dialog.querySelectorAll<HTMLElement>('[data-watch-only]').forEach(node=>{node.hidden=current==='game';});
    // Hidden fields never validate or submit in another media domain.
    for (const control of form.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>('input,select,textarea')) {
      control.disabled=Boolean(control.closest('[data-series-only]') && current!=='series'
        || control.closest('[data-game-only]') && current!=='game'
        || control.closest('[data-watch-only]') && current==='game');
    }
    const watching=statusSelect.querySelector<HTMLOptionElement>('option[value=watching],option[value=playing]')!;
    watching.value=current==='game'?'playing':'watching'; watching.textContent=current==='game'?'Playing':'Watching';
    for (const name of ['score','notes','start','finish','rewatches','hours','replays','firstHours','achievements'] as const) field(name).value=String(draft[name]);
    field('favorite').checked=draft.favorite;
    find<HTMLDetailsElement>('.sp-seasons').open=false;
    dialog.querySelectorAll<HTMLDetailsElement>('.sp-season-drawer').forEach(node=>{node.open=false;});
    dialog.querySelectorAll<HTMLElement>('[data-expanded-date]').forEach(node=>{node.hidden=true;});
    dialog.querySelectorAll<HTMLElement>('[data-edit-season]').forEach(node=>node.setAttribute('aria-expanded','false'));
    find('[data-first-hours]').hidden=!draft.firstHours;
    field('firstHours').disabled=current!=='game'||!draft.firstHours;
    find('[data-achievements]').hidden=!draft.achievements;
    field('achievements').disabled=current!=='game'||!draft.achievements;
    find('[data-action=achievements]').textContent=draft.achievements?'Remove':'Add';
    syncStatus(); syncSeasons(); refreshDates(); feedback();
  }

  async function close() {await closeDialog(dialog); opener?.focus({preventScroll:true});}
  root.addEventListener('click',event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('button');
    if (!button || button.disabled) return;
    if (button.dataset.openWork) {
      current=button.dataset.openWork as PreviewMedia; draft=copy(saved[current]); opener=button;
      populate(); showDialog(dialog); dialog.scrollTop=0; return;
    }
    if (button.dataset.editSeason) {
      const number=button.dataset.editSeason;
      const panel=find<HTMLElement>(`[data-expanded-date="${number}"]`);
      const wasOpen=!panel.hidden;
      // The row expansion changes only visibility; the calendar input stays mounted.
      dialog.querySelectorAll<HTMLElement>('[data-expanded-date]').forEach(node=>{node.hidden=true;});
      dialog.querySelectorAll<HTMLElement>('[data-edit-season]').forEach(node=>node.setAttribute('aria-expanded','false'));
      panel.hidden=wasOpen; button.setAttribute('aria-expanded',String(!wasOpen));
      if (!wasOpen) panel.querySelector<HTMLInputElement>('[data-date-display]')!.focus();
      return;
    }
    const number=Number(button.dataset.completeSeason || button.dataset.today || button.dataset.infer || button.dataset.clearDate);
    if (number && current==='series') {
      const season=draft.seasons[number-1];
      if (button.dataset.completeSeason) {
        if (season.completed) {season.completed=false; season.watched=Math.max(0,season.total-1); setSeasonDate(season,'');}
        else completeSeason(season);
      } else if (button.dataset.today) setSeasonDate(season,localToday());
      else if (button.dataset.infer) setSeasonDate(season,season.inferred,'inferred');
      else setSeasonDate(season,'');
      syncSeasons(); refreshDates(); return;
    }
    const action=button.dataset.action;
    if (action==='close') void close();
    else if (action==='complete-next') {
      const season=nextSeason(draft); if (!season) return;
      completeSeason(season); syncSeasons(); refreshDates(); feedback(`Season ${season.number} completed`);
    } else if (action==='reset') {
      saved=Object.fromEntries(kinds.map(kind=>[kind,initialWork(kind)])) as typeof saved;
      try {sessionStorage.removeItem(storageKey);} catch { /* Optional storage. */ }
      syncRows(); find('.sp-lab-feedback').textContent='Reset';
    } else if (action==='library') {
      const active=button.getAttribute('aria-pressed')!=='true'; button.setAttribute('aria-pressed',String(active)); button.classList.toggle('is-active',active);
      button.setAttribute('aria-label',active?'Remove from library':'Add to library');
    } else if (action==='first-hours') {
      const panel=find('[data-first-hours]'); panel.hidden=!panel.hidden; field('firstHours').disabled=panel.hidden;
      if (!panel.hidden) field('firstHours').focus();
    } else if (action==='achievements') {
      const panel=find('[data-achievements]'); panel.hidden=!panel.hidden; field('achievements').disabled=panel.hidden;
      button.textContent=panel.hidden?'Add':'Remove';
      if (panel.hidden) {draft.achievements=''; field('achievements').value='';} else field('achievements').focus();
    } else if (action==='delete') {
      // Fixture-only removal is reversible with Reset.
      saved[current]=initialWork(current); saved[current].status='planning'; saved[current].score='';
      if (current==='series') {setPreviewProgress(saved[current],0); saved[current].seasons.forEach(season=>{season.score='';});}
      saveStorage(); syncRows(); void close(); find('.sp-lab-feedback').textContent='Entry removed in preview';
    }
  },{signal});

  function readInput(event: Event) {
    const input=event.target;
    if (!(input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement || input instanceof HTMLSelectElement) || input.disabled) return;
    const control=input.closest('[data-calendar-input]');
    const valueInput=control?.querySelector<HTMLInputElement>('[data-date-iso]') || input;
    if (!input.checkValidity()) return;
    const {name,value}=valueInput;
    if (name.startsWith('season_date_')) {
      const season=draft.seasons[Number(name.split('_').at(-1))-1];
      if (season) {setSeasonDate(season,value); syncSeasons();}
    } else if (name.startsWith('season_score_')) {
      draft.seasons[Number(name.split('_').at(-1))-1].score=value;
    } else if (name==='progress') {
      setPreviewProgress(draft,Number(value)); syncSeasons(); refreshDates();
    } else if (name==='status') {
      if (current==='series' && value==='completed' && nextSeason(draft)) {
        completeSeason(nextSeason(draft)!); syncSeasons(); refreshDates();
      } else {draft.status=value; if (value==='completed' && !draft.finish) {draft.finish=localToday(); field('finish').value=draft.finish; refreshDates();}}
      syncStatus();
    } else if (['rewatches','hours','replays'].includes(name)) {
      (draft[name as 'rewatches'|'hours'|'replays'] as number)=Number(value);
    } else if (['score','notes','start','finish','firstHours','achievements'].includes(name)) {
      draft[name as 'score'|'notes'|'start'|'finish'|'firstHours'|'achievements']=value;
    } else if (name==='favorite') draft.favorite=(input as HTMLInputElement).checked;
  }
  form.addEventListener('input',readInput,{signal});
  form.addEventListener('change',readInput,{signal});
  for (const drawer of dialog.querySelectorAll<HTMLDetailsElement>('.sp-season-drawer')) {
    drawer.addEventListener('toggle',()=>{
      if (!drawer.open || !dialog.open) return;
      requestAnimationFrame(()=>{
        if (signal.aborted || !drawer.open) return;
        const bottom=dialog.getBoundingClientRect().bottom;
        const footer=find('.sp-mobile-actions').getBoundingClientRect().height;
        const overflow=drawer.getBoundingClientRect().bottom-bottom+footer+18;
        if (overflow>0) dialog.scrollBy({top:overflow,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
      });
    },{signal});
  }
  const saveStorage=()=>{try {sessionStorage.setItem(storageKey,JSON.stringify(saved));} catch { /* Keep the fixture usable without storage. */ }};
  // Disable native submit validation so a collapsed season can be revealed before reporting an error.
  form.noValidate=true;
  form.addEventListener('submit',event=>{
    event.preventDefault();
    const invalid=form.querySelector<HTMLInputElement>(':invalid');
    if (invalid) {
      let parent=invalid.parentElement;
      while (parent && parent!==form) {
        if (parent instanceof HTMLDetailsElement) parent.open=true;
        if (parent.hasAttribute('data-expanded-date')) {
          parent.hidden=false;
          find(`[data-edit-season="${parent.dataset.expandedDate}"]`).setAttribute('aria-expanded','true');
        }
        parent=parent.parentElement;
      }
      invalid.reportValidity(); return;
    }
    saved[current]=copy(draft); saveStorage(); syncRows();
    find('.sp-lab-feedback').textContent='Saved in preview'; void close();
  },{signal});
  dialog.addEventListener('click',event=>{if (event.target===dialog) {
    const rect=dialog.getBoundingClientRect();
    if (event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom) void close();
  }},{signal});
  signal.addEventListener('abort',()=>{if (dialog.open) dialog.close();},{once:true});
  syncRows();
}

document.addEventListener('astro:before-swap',()=>controller?.abort());
document.addEventListener('astro:page-load',setup);
