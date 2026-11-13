import {setupEditorSelect} from './editor-select';
import {animate} from 'motion/mini';
import {formatDateTime, localToday} from './date-time';
import {editorStore, type EditorTitle} from './editor-store';
import {closeDialog, showDialog} from './ui-motion';
import {setupCalendarDateInputs} from './calendar-date-input';
import {setupScoreSteppers} from './scoreStepper';
import {responsiveArtwork} from './responsive-artwork';

type Episode = {id:number; number:number; watched:boolean};
type Season = {season_number:number; membership:string; name?:string|null; poster?:string|null; total:number; episodes:Episode[];
  upcoming?:Pick<Episode,'id'|'number'>[];
  score?:number|null; start_date?:string|null; completion_date?:string|null;
  start_source?:string|null; date_source?:string|null; completed?:boolean};
type Airing = {season_number:number; latest_episode:number; next_date?:string|null; final_date?:string|null;
  latest_episode_id:number; watched:boolean; previous_season?:number|null; previous_episode_id?:number|null};
export type SeriesEditorSummary = {ready:boolean; pending?:boolean; retry_after?:number; revision?:string;
  order?:string; seasons:Season[]; airing?:Airing|null};
export type EditorEpisodeMetadata = {id:number; title:string; runtime?:number|null; air_date?:string|null; image?:string|null};
type Draft = {season_number:number; membership:string; start_date?:string|null; completion_date?:string|null; score?:number|null};
type Choice = 'catch_up'|'complete_previous';

export function createSeriesEditor(dialog:HTMLDialogElement, form:HTMLFormElement, page:AbortSignal,
  failure:(message:string)=>void) {
  const panel = form.querySelector<HTMLElement>('[data-series-editor]')!;
  const overallStart = form.querySelector<HTMLElement>('.editor-start-date')!;
  const overallFinish = form.querySelector<HTMLElement>('.editor-finish-date')!;
  const find = <T extends HTMLElement = HTMLElement>(name:string) => panel.querySelector<T>(`[data-series-${name}]`)!;
  const field = (name:string) => form.elements.namedItem(name) as HTMLInputElement;
  const rows = find('rows');
  const rowTemplate = find<HTMLTemplateElement>('row-template');
  const expand = dialog.querySelector<HTMLButtonElement>('[data-editor-seasons]')!;
  const selector = panel.querySelector<HTMLSelectElement>('#series-season')!;
  const selectControl = setupEditorSelect(selector.closest<HTMLElement>('[data-editor-select]')!,page);
  const countInput = find<HTMLInputElement>('count');
  const selection = find('selection');
  const content = find('content');
  const prompt = document.querySelector<HTMLDialogElement>('[data-series-airing]')!;
  const drafts = new Map<number,Draft>();
  let title:EditorTitle|null = null;
  let summary:SeriesEditorSummary|undefined;
  let selected:number|undefined;
  let allSeasons = false;
  let viewMotion:ReturnType<typeof animate>|undefined;
  const renderedRows = new Map<number,HTMLElement>();
  const rowControls = new Map<number, () => void>();
  let stagedPosition:{id:number|null; count:number; season:number}|undefined;
  let active:AbortController|undefined;
  let timer:ReturnType<typeof setTimeout>|undefined;
  let request:Promise<void>|undefined;
  let loadFailed = false;
  let generation = 0;
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const seasons = () => summary?.seasons ?? [];
  const season = () => seasons().find(item => item.season_number === selected);
  const released = () => seasons().filter(item=>item.episodes.length);
  const watchedCount = (s:Season) => s.episodes.filter(ep=>ep.watched).length;
  const full = (s:Season) => s.completed || (s.episodes.length > 0 && s.episodes.length === s.total &&
    s.episodes.every(ep=>ep.watched));
  const canFinishSeason = (s:Season) => {
    if (!stagedPosition) return full(s);
    return s.episodes.length>0 && s.episodes.length===s.total &&
      (stagedPosition.season>s.season_number ||
       (stagedPosition.season===s.season_number && stagedPosition.count===s.total));
  };
  const fromRow = (node:Element) => {
    const number=Number(node.closest<HTMLElement>('[data-season-number]')?.dataset.seasonNumber);
    return seasons().find(item=>item.season_number===number);
  };
  const status = () => field('status').value;
  const currentMode = () => Boolean(title && !allSeasons && ['watching','paused'].includes(status()));
  const tracked = () => Boolean(title && (allSeasons || currentMode()));
  const membershipDraft = (s:Season) => {
    let draft = drafts.get(s.season_number);
    if (!draft) {
      draft = {season_number:s.season_number,membership:s.membership};
      drafts.set(s.season_number,draft);
    }
    return draft;
  };
  const selectCurrent = () => {
    const list = released();
    const latest = list.findLastIndex(s=>watchedCount(s)>0);
    const current = list.slice(Math.max(latest,0)).find(s=>s.episodes.some(ep=>!ep.watched)) ?? list.at(-1) ?? seasons()[0];
    selected = current?.season_number;
  };
  const sharedDates = () => {
    if(!title || !seasons().length) return;
    const first=seasons()[0],last=seasons().filter(s=>s.episodes.length).at(-1);
    const start=drafts.get(first.season_number);
    const finish=last && drafts.get(last.season_number);
    if(start && 'start_date' in start) field('start_date').value=start.start_date??'';
    if(finish && 'completion_date' in finish) field('finish_date').value=finish.completion_date??'';
    form.dispatchEvent(new CustomEvent('anylist:series-dates-sync',{bubbles:true}));
  };
  const layout = () => {
    const isTracked = tracked();
    const current=currentMode();
    expand.hidden=!title;
    expand.setAttribute('aria-expanded',String(allSeasons));
    form.querySelector('.editor-score-field label')!.textContent=title?'Series score':'Score';
    field('manual_score').setAttribute('aria-label',title?'Series score':'Score');
    panel.hidden = !isTracked;
    overallStart.hidden = isTracked;
    overallFinish.hidden = isTracked;
    for(const region of [overallStart,overallFinish])for(const input of region.querySelectorAll<HTMLInputElement|HTMLButtonElement>('input,button'))input.disabled=isTracked;
    panel.parentElement?.toggleAttribute('data-series-tracked',isTracked);
    panel.parentElement?.toggleAttribute('data-series-current',current);
    panel.toggleAttribute('data-all-seasons',allSeasons);
  };
  const previewCount = (s:Season) => stagedPosition
    ? s.season_number<stagedPosition.season ? s.episodes.length : s.season_number===stagedPosition.season ? stagedPosition.count : 0
    : watchedCount(s);
  const poster = (node:HTMLElement,s:Season) => {
    const artwork=responsiveArtwork(s.poster,{size:'w92',sizes:'44px',route:'direct'});
    const key=artwork?.src ?? String(s.season_number);
    if (node.dataset.image===key) return;
    node.dataset.image=key;
    node.replaceChildren();
    if (artwork) {
      const image=new Image(); image.alt=''; image.decoding='async'; image.src=artwork.src;
      node.append(image);
    } else node.textContent=String(s.season_number);
  };
  const paintList = () => {
    for (const s of seasons()) {
      const number=s.season_number;
      let row=renderedRows.get(number);
      if (!row) {
        row=rowTemplate.content.firstElementChild!.cloneNode(true) as HTMLElement;
        row.dataset.seasonNumber=String(number);
        for (const name of ['start','finish','score']) {
          const input=row.querySelector<HTMLInputElement>(`#season-${name}`)!;
          input.id=`editor-season-${number}-${name}`;
          input.setAttribute('aria-label',`Season ${number} ${name==='score'?'score':name+' date'}`);
          row.querySelector<HTMLLabelElement>(`[data-season-date-label="${name}"],[data-season-${name}-label]`)!.htmlFor=input.id;
        }
        rows.append(row);
        renderedRows.set(number,row);
        rowControls.set(number,setupCalendarDateInputs(row,active!.signal));
        setupScoreSteppers(row,active!.signal);
      }
      row.hidden=!allSeasons && number!==selected;
      if (!allSeasons && number===selected) {
        if (selection.parentElement!==row) row.insertBefore(selection,row.querySelector('.series-editor-fields'));
        selection.hidden=false;
      }
      row.querySelector('[data-season-label]')!.textContent=`Season ${number}`;
      row.querySelector('[data-season-progress]')!.textContent=`${previewCount(s)}/${s.total} episodes`;
      if (allSeasons && !row.hidden) poster(row.querySelector('[data-season-poster]')!,s);
      const draft=drafts.get(number);
      const score=row.querySelector<HTMLInputElement>('[name=series_score]')!;
      const start=row.querySelector<HTMLInputElement>('[name=series_start]')!;
      const finish=row.querySelector<HTMLInputElement>('[name=series_finish]')!;
      if (document.activeElement!==score) score.value=String((draft && 'score' in draft?draft.score:s.score)??'');
      // Preserve visible invalid text; hidden controls cannot block the form.
      if (!row.contains(document.activeElement)) {
        start.value=(draft && 'start_date' in draft?draft.start_date:s.start_date)??'';
        finish.value=(draft && 'completion_date' in draft?draft.completion_date:s.completion_date)??'';
        rowControls.get(number)?.();
      }
      for (const input of row.querySelectorAll<HTMLInputElement|HTMLButtonElement>('input,button')) input.disabled=row.hidden;
      for (const input of finish.closest('[data-calendar-input]')!.querySelectorAll<HTMLInputElement|HTMLButtonElement>('input,button[data-date-picker]')) input.disabled=row.hidden||!canFinishSeason(s);
      row.querySelector<HTMLInputElement>('[data-season-date-label="finish"] + [data-calendar-input] [data-date-display]')!.title=canFinishSeason(s)?'':'Complete the season to set its finish date';
    }
  };
  const render = () => {
    if (!title) return;
    const focused=document.activeElement instanceof HTMLElement && (panel.contains(document.activeElement)||document.activeElement===expand) ? document.activeElement : undefined;
    const restoreFocus=()=>{
      if (focused && document.activeElement!==focused && focused.isConnected && !focused.closest('[hidden]') &&
          !('disabled' in focused && focused.disabled)) focused.focus({preventScroll:true});
    };
    layout();
    const current=season();
    if (panel.hidden || !current) {
      countInput.disabled=true;
      for (const input of rows.querySelectorAll<HTMLInputElement|HTMLButtonElement>('input,button')) input.disabled=true;
    }
    if (panel.hidden) {return;}
    find('loading').hidden=!!current;
    find('loading').textContent=loadFailed?'Progress is unavailable.':summary&&!summary.pending?'No seasons available.':'Loading progress…';
    find('retry').hidden=!loadFailed;
    find('content').hidden=!current;
    if (!current) return;
    const signature=seasons().map(s=>s.season_number).join(',');
    if (selector.dataset.signature!==signature) {
      selector.replaceChildren(...seasons().map(s=>new Option(`Season ${s.season_number}`,String(s.season_number))));
      selector.dataset.signature=signature;
    }
    selector.value=String(current.season_number);
    selectControl.sync();
    countInput.max=String(current.episodes.length);
    countInput.disabled=allSeasons||!current.episodes.length;
    if (document.activeElement!==countInput) countInput.value=String(previewCount(current));
    panel.toggleAttribute('data-all-seasons',allSeasons);
    if (allSeasons) {selection.hidden=true;if (selection.parentElement!==content) content.prepend(selection);}
    paintList();
    countInput.disabled=allSeasons||!current.episodes.length;
    selector.disabled=allSeasons;
    selectControl.sync();
    restoreFocus();
  };
  function read() {
    if (!title || !active || active.signal.aborted) return Promise.resolve();
    if (!title.id && !tracked() && status()!=='completed') return Promise.resolve();
    if (request) return request;
    const editing=title;
    const signal=active.signal;
    const version=generation;
    clearTimeout(timer);
    request=(async()=>{
      try {
        const id=editing.id ?? await editorStore.resolveId(editing);
        if (signal.aborted) return;
        const next=await editorStore.readSeries(id,signal);
        if (signal.aborted || version!==generation) return;
        loadFailed=false;
        if (next.ready || !summary?.ready) summary=next;
        if (!season() || (next.ready && !stagedPosition && !drafts.size)) selectCurrent();
        if (!next.ready && next.pending) timer=setTimeout(()=>void read(), Math.max(1,next.retry_after||2)*1000);
      } catch (cause) {
        if (!signal.aborted && version===generation) loadFailed=true;
      } finally {
        if (!signal.aborted && version===generation) {request=undefined;render();}
      }
    })();
    return request;
  }
  const setCount = (s:Season, count:number) => {
    if (!Number.isInteger(count) || count<0 || count>s.episodes.length) return;
    const previous = released().filter(item=>item.season_number<s.season_number).at(-1)?.episodes.at(-1);
    stagedPosition={id:count?s.episodes[count-1].id:previous?.id??null,count,season:s.season_number};
  };
  const step = (direction:number) => {
    const current=season();
    if (!current || countInput.disabled) return;
    const count=Number(countInput.value);
    let target=current;
    if (direction>0&&count===current.episodes.length || direction<0&&count===0) {
      const choices=released(), next=choices[choices.indexOf(current)+direction];
      if (!next) return;
      target=next;
      setCount(target,direction>0?1:Math.max(0,target.episodes.length-1));
      selected=target.season_number;
    } else setCount(current,count+direction);
    countInput.value=String(stagedPosition?.count??count);
    render();
  };
  expand.addEventListener('click',()=>{
    if(!title)return;
    if(!tracked()) {
      const first=seasons()[0],last=seasons().filter(s=>s.episodes.length).at(-1);
      // Dates typed in the default Completed form enter the same season drafts.
      if(first && field('start_date').value!==(title.entry?.start_date??''))membershipDraft(first).start_date=field('start_date').value||null;
      if(last && canFinishSeason(last) && field('finish_date').value!==(title.entry?.finish_date??''))membershipDraft(last).completion_date=field('finish_date').value||null;
    }
    allSeasons=!allSeasons;
    sharedDates();render();
    viewMotion?.cancel();
    if(!reduced())viewMotion=animate(allSeasons?rows:currentMode()?selection:overallStart,
      {opacity:[.75,1],transform:['translateY(2px)','translateY(0)']},{duration:.16,ease:'easeOut'});
  },{signal:page});
  panel.addEventListener('click',event=>{
    const button=(event.target as HTMLElement).closest<HTMLButtonElement>('button');
    if(!button)return;
    if(button.dataset.seriesStep)step(Number(button.dataset.seriesStep));
    else if(button.hasAttribute('data-series-retry')){loadFailed=false;render();void read();}
  },{signal:page});
  selector.addEventListener('change',()=>{selected=Number(selector.value);render();},{signal:page});
  countInput.addEventListener('keydown',event=>{
    if (!['ArrowUp','ArrowDown'].includes(event.key)) return;
    event.preventDefault(); step(event.key==='ArrowUp'?1:-1);
  },{signal:page});
  const captureDraft = (event:Event) => {
    const node=event.target as HTMLInputElement;
    if (!(node instanceof HTMLInputElement)||!node.validity.valid) return;
    if (node===countInput) {
      const current=season();
      if (current&&node.value!=='') setCount(current,Number(node.value));
      if (event.type==='change') render();
      return;
    }
    const s=fromRow(node); if (!s) return;
    if (node.name==='series_score') membershipDraft(s).score=Number(node.value)||null;
    else {
      if (event.type!=='change') return;
      const iso=node.closest('[data-calendar-input]')?.querySelector<HTMLInputElement>('[data-date-iso]');
      if (iso?.name==='series_start') membershipDraft(s).start_date=iso.value||null;
      if (iso?.name==='series_finish') membershipDraft(s).completion_date=iso.value||null;
      sharedDates();
    }
  };
  panel.addEventListener('input',captureDraft,{signal:page});
  panel.addEventListener('change',captureDraft,{signal:page});
  form.addEventListener('change',event=>{
    const input=event.target;
    if(!title || tracked() || !(input instanceof HTMLInputElement) || !input.validity.valid)return;
    const dateInput=input.closest('[data-calendar-input]')?.querySelector<HTMLInputElement>('[data-date-iso]');
    if(dateInput===field('start_date') && seasons()[0])membershipDraft(seasons()[0]).start_date=dateInput.value||null;
    const latest=seasons().filter(s=>s.episodes.length).at(-1);
    if(dateInput===field('finish_date') && latest && canFinishSeason(latest))membershipDraft(latest).completion_date=dateInput.value||null;
  },{signal:page});
  const chooseAiring = (airing:Airing) => new Promise<Choice|null>(resolve=>{
    const signal=active?.signal;
    if (!signal || signal.aborted) {resolve(null);return;}
    prompt.querySelector('#series-airing-info')!.textContent=[
      `Season ${airing.season_number}, Episode ${airing.latest_episode} is the latest release.`,
      airing.next_date ? `Next episode ${formatDateTime(airing.next_date)}.` : '',
      airing.final_date && airing.final_date!==airing.next_date ? `Final episode ${formatDateTime(airing.final_date)}.` : '',
    ].filter(Boolean).join(' ');
    const earlier=prompt.querySelector<HTMLButtonElement>('[value=complete_previous]')!;
    earlier.hidden=airing.watched||!airing.previous_season;
    earlier.textContent=`Complete until Season ${airing.previous_season}`;
    prompt.querySelector('[value=catch_up]')!.textContent=airing.watched?`Watched until Episode ${airing.latest_episode}`:'Set as Watching';
    const abort=()=>{if (prompt.open) prompt.close('cancel');};
    signal.addEventListener('abort',abort,{once:true});
    prompt.addEventListener('close',()=>{
      signal.removeEventListener('abort',abort);
      resolve(prompt.returnValue==='catch_up'||prompt.returnValue==='complete_previous'?prompt.returnValue:null);
    },{once:true});
    prompt.returnValue='cancel';
    showDialog(prompt);
  });
  prompt.addEventListener('click',event=>{if(event.target===prompt)void closeDialog(prompt,'cancel');},{signal:page});
  const reset = () => {
    const wasSeries=title!==null;
    active?.abort();
    active=undefined;
    clearTimeout(timer);
    viewMotion?.cancel();
    drafts.clear();
    stagedPosition=undefined;
    summary=undefined;
    selected=undefined;
    allSeasons=false;
    title=null;
    loadFailed=false;
    request=undefined;
    generation++;
    selection.hidden=true;content.prepend(selection);
    panel.removeAttribute('data-all-seasons');
    selector.disabled=true;selectControl.sync();
    rows.replaceChildren();
    countInput.disabled=true;
    countInput.value='';
    selector.replaceChildren(); delete selector.dataset.signature;
    renderedRows.clear();
    rowControls.clear();
    expand.hidden=true;expand.setAttribute('aria-expanded','false');
    if (wasSeries) layout();
  };
  page.addEventListener('abort',reset,{once:true});
  return {
    reset,
    async prepare(editing:EditorTitle, signal:AbortSignal) {
      if(editing.type!=='series') return undefined;
      const id=editing.id ?? await editorStore.resolveId(editing);
      const cancelled=()=>{if(signal.aborted||page.aborted)throw new DOMException('Editor closed','AbortError');};
      cancelled();
      let prepared=await editorStore.prepareSeries(id,page);
      while(!prepared.ready && prepared.pending) {
        cancelled();
        await new Promise<void>((resolve,reject)=>{
          const abort=()=>{clearTimeout(timer);reject(new DOMException('Editor closed','AbortError'));};
          const timer=setTimeout(()=>{signal.removeEventListener('abort',abort);resolve();},Math.min(10,Math.max(1,prepared.retry_after||2))*1000);
          signal.addEventListener('abort',abort,{once:true});
          if(signal.aborted)abort();
        });
        cancelled();
        prepared=await editorStore.prepareSeries(id,page);
      }
      cancelled();
      return prepared;
    },
    open(editing:EditorTitle, prepared?:SeriesEditorSummary) {
      reset();
      if (editing.type!=='series') return;
      title=editing;
      active=new AbortController();
      summary=prepared ?? editorStore.cachedSeries(editing.id);
      if (summary) selectCurrent();
      render();
      if (!prepared) void read();
    },
    statusChanged() { if (title) {render();if (!summary?.ready) void read();} },
    busy:()=>false,
    tracked,
    async patch(completing:boolean) {
      if ((completing || stagedPosition) && !summary?.ready) {
        failure('Episode progress is still loading. Try again shortly.');
        void read();
        return false;
      }
      const current=summary;
      let action:Choice|'position'|undefined;
      if (completing && current?.airing) {
        const choice=await chooseAiring(current.airing);
        if (!choice) return false;
        action=choice;
      } else if (!completing && stagedPosition) action='position';
      const edits=[...drafts.values()].map(edit=>{
        const season=seasons().find(item=>item.season_number===edit.season_number);
        if (action!=='position' || !season || canFinishSeason(season)) return edit;
        const {completion_date:_, ...remaining}=edit;
        return remaining;
      }).filter(edit=>Object.keys(edit).length>2);
      return {revision:current?.revision,order:current?.order,action,watch_date:localToday(),
        ...(action==='position'?{episode_id:stagedPosition?.id}:{}),seasons:edits};
    },
    rollbackNeeded(patch:{action?:string;episode_id?:number|null}) {
      if (patch.action!=='position') return false;
      const episodes=seasons().flatMap(s=>s.episodes);
      const end=episodes.findIndex(ep=>ep.id===patch.episode_id);
      return episodes.slice(end+1).some(ep=>ep.watched);
    },
    optimisticProgress(patch:{action?:string;episode_id?:number|null}) {
      const airing=summary?.airing;
      const target=patch.action==='catch_up'?airing?.latest_episode_id
        :patch.action==='complete_previous'?airing?.previous_episode_id
        :patch.action==='position'?patch.episode_id:undefined;
      if (target===undefined) return undefined;
      const episodes=seasons().flatMap(s=>s.episodes);
      const end=episodes.findIndex(ep=>ep.id===target);
      return {progress:episodes.filter((ep,index)=>index<=end || (patch.action!=='position' && ep.watched)).length};
    },
    async refresh() {await read();},
  };
}
