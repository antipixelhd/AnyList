import {localToday} from './date-time';
import {setupEditorSelect} from './editor-select';
import {editorStore, type EditorTitle} from './editor-store';
import {bindProgressRanges, releaseDateLabel, setupSeriesProgressPicker, syncProgressRange} from './series-progress-picker';
import {createProgressMotion} from './series-progress-motion';
import {capNumberInput} from './scoreStepper';

type Episode = {id:number; number:number; watched:boolean};
type Season = {season_number:number; membership:string; name?:string|null; poster?:string|null; total:number;
  progress_total?:number; release_date?:string|null;
  episodes:Episode[]; upcoming?:Pick<Episode,'id'|'number'>[]; score?:number|null;
  start_date?:string|null; completion_date?:string|null; start_source?:string|null;
  date_source?:string|null; start_manual?:boolean; date_manual?:boolean; completed?:boolean; manual_completion?:boolean;
  status?:'planning'|'watching'|'paused'|'dropped'|null};
export type SeriesEditorSummary = {ready:boolean; pending?:boolean; retry_after?:number; revision?:string;
  next_progress_season?:number|null;
  selected_position?:{season_number:number;episode_count:number;episode_id:number|null}|null;
  order?:string; seasons:Season[]};
export type EditorEpisodeMetadata = {id:number; title:string; runtime?:number|null; air_date?:string|null; image?:string|null};
type Position = {season_number:number; episode_count:number; episode_id:number|null};
type SeriesPatch = Partial<Position> & {action?:'position'|'complete_season'|'resume_season';
  progress_season?:number; season_progress?:Record<number,number>;
  revision?:string; order?:string; watch_date:string; season_statuses?:Record<number,string>;
  seasons:{season_number:number;membership:string;score?:number|null;completion_date?:string|null}[]};
const scopeFields = ['manual_score','start_date','finish_date','rewatch_count'] as const;
type ScopeFields = Record<(typeof scopeFields)[number],string>;

export function createSeriesEditor(dialog:HTMLDialogElement, form:HTMLFormElement, page:AbortSignal,
  failure:(message:string)=>void) {
  const panel = form.querySelector<HTMLElement>('[data-series-editor]')!;
  const find = <T extends HTMLElement = HTMLElement>(name:string) => panel.querySelector<T>(`[data-series-${name}]`)!;
  const field = (name:string) => form.elements.namedItem(name) as HTMLInputElement;
  const scopeRoot = dialog.querySelector<HTMLElement>('[data-editor-scope]')!;
  const scopeSelect = scopeRoot.querySelector<HTMLSelectElement>('select')!;
  const scopeControl = setupEditorSelect(scopeRoot.querySelector<HTMLElement>('[data-editor-select]')!,page);
  const input = find<HTMLInputElement>('count');
  const bar = find<HTMLInputElement>('bar');
  const steps = panel.querySelectorAll<HTMLButtonElement>('[data-series-step]');
  const scopeMessage = form.querySelector<HTMLElement>('[data-season-field-message]')!;
  const drafts = new Map<number,ScopeFields>();
  const statusDrafts = new Map<number,string>();
  let title:EditorTitle|null = null;
  let summary:SeriesEditorSummary|undefined;
  let scope = 0;
  const progressDrafts = new Map<number,number>();
  const previousCounts = new Map<number,number>();
  const watchedCounts = new Map<number,number>();
  const progressMotion = createProgressMotion(page);
  const progressPicker = setupSeriesProgressPicker(panel,dialog,page,editProgress,progressMotion);
  bindProgressRanges(panel,page,progressMotion);
  let writtenStatus:string|undefined;
  let openingStatus:string|undefined;
  let initialStatus = '';
  let active:AbortController|undefined;
  let timer:ReturnType<typeof setTimeout>|undefined;
  let request:Promise<void>|undefined;
  let loadFailed = false;

  const seasons = () => summary?.seasons ?? [];
  const released = () => seasons().filter(season=>season.episodes.length);
  const status = () => field('status').value;
  const scopedSeason = () => seasons().find(season=>season.season_number===scope);
  const scopeBaseline = () => {
    const season = scopedSeason();
    if (!season) return initialStatus;
    if (season.status) return season.status;
    if (season.completed) return 'completed';
    const next = initialStatus==='watching'||initialStatus==='planning' ? summary?.next_progress_season : undefined;
    if (season.season_number===(next??savedPosition()?.season_number) && initialStatus!=='completed') return initialStatus;
    return watched(season)>0?'watching':'planning';
  };
  const name = (season:Season) => season.name?.trim() || `Season ${season.season_number}`;
  const watched = (season:Season) => watchedCounts.get(season.season_number) ?? 0;
  const total = (season:Season) => season.progress_total ?? (season.total || season.episodes.length);
  const seasonFields = (season:Season):ScopeFields => ({
    manual_score:String(season.score ?? 0),start_date:season.start_date??'',finish_date:season.completion_date??'',rewatch_count:'0',
  });
  const values = ():ScopeFields => Object.fromEntries(scopeFields.map(key=>[key,field(key).value])) as ScopeFields;
  const draft = (number:number) => number===scope ? values() : drafts.get(number);
  const savedPosition = () => {
    if (summary?.selected_position) return summary.selected_position;
    const season = released().findLast(item=>watched(item)>0) ?? released()[0];
    return season && position(season,season.episodes.findLastIndex(episode=>episode.watched)+1);
  };
  function position(season:Season,count:number):Position {
    return {season_number:season.season_number,episode_count:count,
      episode_id:season.episodes[count-1]?.id??null};
  }
  function setSummary(next:SeriesEditorSummary|undefined) {
    summary=next;
    watchedCounts.clear();
    for (const season of seasons()) {
      const count=season.episodes.filter(episode=>episode.watched).length;
      watchedCounts.set(season.season_number,count);
    }
  }
  const completingScope = () => status()==='completed' && (scope ? scopeBaseline() : openingStatus)!=='completed';
  const baselineCount = (season:Season) => completingScope() && (!scope || scope===season.season_number)
    ? season.episodes.length : watched(season);
  function previewCount(season:Season) {
    return progressDrafts.get(season.season_number) ?? baselineCount(season);
  }
  function syncOptions(select:HTMLSelectElement,options:[string,string][],value:string) {
    const signature = JSON.stringify(options);
    if (select.dataset.signature!==signature) {
      select.replaceChildren(...options.map(([number,label])=>new Option(label,number)));
      select.dataset.signature=signature;
    }
    select.value=value;
  }
  function render(keepInput=false) {
    panel.hidden=scopeRoot.hidden=!title;
    input.disabled=bar.disabled=true;
    if (!title) {scopeControl.close(true);progressPicker.close();scopeMessage.hidden=true;return;}
    const current = scopedSeason();
    const hasProgress = scope ? Boolean(current) : Boolean(seasons().length);
    const options:[string,string][] = [['0','All Seasons'],...seasons().map(season=>[String(season.season_number),name(season)] as [string,string])];
    if (scope && !current) options.push([String(scope),`Season ${scope}`]);
    syncOptions(scopeSelect,options,String(scope));
    scopeControl.sync();
    find('loading').hidden=hasProgress && !loadFailed;
    const unavailable=loadFailed||Boolean(scope&&!current&&summary&&!summary.pending);
    find('loading').textContent=unavailable?'Progress is unavailable.':summary&&!summary.pending?'No released episodes.':'Loading progress…';
    find('retry').hidden=!unavailable;
    const available=current?.episodes.length??0;
    find('content').hidden=!hasProgress||Boolean(current&&!available);
    find('label').textContent=scope?(current&&!available?'Release date':'Episode progress'):'Progress';
    find('trigger').hidden=Boolean(scope);
    find('stepper').hidden=!scope||!available;
    find('release').hidden=!current||Boolean(available);
    if (current&&!available) find('release').textContent=releaseDateLabel(current.release_date);
    if (!scope) renderPicker();
    else progressPicker.close();
    input.disabled=bar.disabled=!available || !summary?.ready;
    const count=current ? previewCount(current) : 0;
    input.max=String(available);
    if (!keepInput) input.value=String(count);
    const maximum=current ? total(current) : 0;
    find('total').textContent=current ? `/ ${maximum}` : '';
    find('remaining').textContent=current && maximum>count ? `${maximum-count} left` : '';
    syncProgressRange(bar,count,available,maximum,progressMotion);
    steps[0].disabled=input.disabled||count>=Number(input.max);
    steps[1].disabled=input.disabled||count===0;
    refreshScopeMessage();
  }
  function renderPicker() {
    progressPicker.render(seasons().map(season=>{
      const number=season.season_number;
      const count=previewCount(season);
      const maximum=total(season);
      const released=season.episodes.length;
      const savedComplete=Boolean(season.completed && !progressDrafts.has(number));
      const canRestore=previousCounts.has(number);
      return {number,name:name(season),count,total:maximum,released,releaseDate:season.release_date,
        complete:savedComplete||Boolean(maximum && count>=maximum),
        canToggle:canRestore||(!savedComplete&&count<released),canRestore};
    }),Boolean(summary?.ready));
  }
  function refreshScopeMessage() {
    const season=scopedSeason();
    const original=season && seasonFields(season);
    scopeMessage.textContent=season?.manual_completion
      ?'Season start date and rewatches are preview fields.'
      :'Season dates and rewatches are preview fields.';
    const previewFields = season?.manual_completion
      ? (['start_date','rewatch_count'] as const) : (['start_date','finish_date','rewatch_count'] as const);
    scopeMessage.hidden=!original||!previewFields.some(key=>field(key).value!==original[key]);
  }
  function labelRewatches(label:string) {
    const container=form.querySelector('.editor-rewatches')!;
    container.querySelector('#editor-rewatches-label')!.textContent=label;
    container.querySelector('[data-score-step=up]')!.setAttribute('aria-label',`Increase ${label.toLowerCase()}`);
    container.querySelector('[data-score-step=down]')!.setAttribute('aria-label',`Decrease ${label.toLowerCase()}`);
  }
  function switchScope(next:number, keepDraft=true) {
    failure('');
    if (keepDraft && (!scope || scopedSeason())) {
      drafts.set(scope,values());
      statusDrafts.set(scope,status());
    }
    scope=next;
    const season=scopedSeason();
    field('status').value = statusDrafts.get(scope) ?? scopeBaseline();
    writtenStatus = status();
    form.dispatchEvent(new CustomEvent('anylist:series-status-sync'));
    const fields=drafts.get(scope)??(season&&seasonFields(season));
    if(fields)for(const key of scopeFields)field(key).value=fields[key];
    const scoreLabel=scope?'Season score':'Series score';
    form.querySelector('#editor-score-label')!.textContent=scoreLabel;
    labelRewatches(scope?'Rewatches':'Total rewatches');
    form.dispatchEvent(new CustomEvent('anylist:series-dates-sync',{bubbles:true}));
    render();
    form.dispatchEvent(new Event('anylist:series-artwork'));
  }
  function read() {
    if (!title?.id || !active || active.signal.aborted) return Promise.resolve();
    if (request) return request;
    const {signal}=active;
    const id=title.id;
    clearTimeout(timer);
    request=(async()=>{
      try {
        const next=await editorStore.readSeries(id,signal);
        if (signal.aborted) return;
        const wasWaiting = scope && !scopedSeason();
        if (next.ready || !summary?.ready) setSummary(next);
        if (wasWaiting && scopedSeason()) switchScope(scope,false);
        loadFailed=false;
        for (const [number,count] of progressDrafts) {
          const season=seasons().find(item=>item.season_number===number);
          if (!season?.episodes.length || count>season.episodes.length) {
            progressDrafts.delete(number);
            previousCounts.delete(number);
          }
        }
        for (const season of seasons()) {
          if ((previousCounts.get(season.season_number)??0)>season.episodes.length) previousCounts.delete(season.season_number);
        }
        if (!next.ready && next.pending) timer=setTimeout(()=>void read(),Math.max(1,next.retry_after||2)*1000);
      } catch {
        if (!signal.aborted) loadFailed=true;
      } finally {
        if (!signal.aborted) {request=undefined;render();form.dispatchEvent(new Event('anylist:series-artwork'));}
      }
    })();
    return request;
  }
  function setCount(season:Season,count:number) {
    if (scope && completingScope() && count<season.episodes.length) {
      field('status').value=count?'watching':'planning';
      writtenStatus=status();
      form.dispatchEvent(new CustomEvent('anylist:series-status-sync'));
    }
    if (count===baselineCount(season)) progressDrafts.delete(season.season_number);
    else progressDrafts.set(season.season_number,count);
    failure('');
  }
  function editProgress(number:number,value:number|'toggle') {
    const season=seasons().find(item=>item.season_number===number);
    if (scope || !summary?.ready || !season?.episodes.length) return;
    const count=previewCount(season);
    if (value==='toggle') {
      const previous=previousCounts.get(number);
      if (previous!==undefined) {
        previousCounts.delete(number);
        setCount(season,previous);
      } else if (count<season.episodes.length && !(season.completed && !progressDrafts.has(number))) {
        previousCounts.set(number,count);
        setCount(season,season.episodes.length);
      }
    } else if (value!==count && value>=0 && value<=season.episodes.length) {
      previousCounts.delete(number);
      setCount(season,value);
    }
    renderPicker();
  }
  function step(direction:number) {
    const current=scopedSeason();
    if (!current || input.disabled) return;
    const next=previewCount(current)+direction;
    if (next>=0&&next<=current.episodes.length) {
      previousCounts.delete(current.season_number);
      setCount(current,next);
    }
    render();
  }
  panel.addEventListener('click',event=>{
    const target=event.target as Element;
    const button=target.closest<HTMLButtonElement>('button');
    if(button?.dataset.seriesStep)step(Number(button.dataset.seriesStep));
    if(button?.hasAttribute('data-series-retry')){loadFailed=false;render();void read();}
    if(!button&&target!==input&&!target.closest('[data-series-range]')&&target.closest('.series-episode-stepper'))input.focus();
  },{signal:page});
  input.addEventListener('keydown',event=>{
    if (!['ArrowUp','ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    step(event.key==='ArrowUp'?1:-1);
  },{signal:page});
  input.addEventListener('input',()=>{
    capNumberInput(input);
    const season=scopedSeason();
    if (!season || !input.validity.valid) return;
    previousCounts.delete(season.season_number);
    setCount(season,input.valueAsNumber);
    render(true);
  },{signal:page});
  input.addEventListener('blur',()=>{
    if (!input.validity.valid) render();
  },{signal:page});
  bar.addEventListener('input',()=>{
    const season=scopedSeason();
    if (!season||bar.disabled) return;
    previousCounts.delete(season.season_number);
    setCount(season,bar.valueAsNumber);
    render();
  },{signal:page});
  scopeSelect.addEventListener('change',()=>switchScope(Number(scopeSelect.value)),{signal:page});
  for(const event of ['input','change'])form.addEventListener(event,()=>{
    if (scope) refreshScopeMessage();
  },{signal:page});
  function reset() {
    progressMotion.reset();
    if(title) {
      form.querySelector('#editor-score-label')!.textContent='Score';
      labelRewatches('Total rewatches');
    }
    active?.abort();
    clearTimeout(timer);
    request=undefined;
    title=null;
    setSummary(undefined);
    scope=0;
    progressDrafts.clear();
    previousCounts.clear();
    writtenStatus=undefined;
    openingStatus=undefined;
    drafts.clear();
    statusDrafts.clear();
    loadFailed=false;
    input.value='';
    render();
  }
  page.addEventListener('abort',reset,{once:true});
  return {
    reset,
    async prepare(editing:EditorTitle, signal:AbortSignal) {
      if (editing.type!=='series') return undefined;
      const id=editing.id??await editorStore.resolveId(editing);
      signal.throwIfAborted();
      try {return await editorStore.prepareSeries(id,page);}
      catch {signal.throwIfAborted();return undefined;}
    },
    open(editing:EditorTitle, prepared?:SeriesEditorSummary, season?:number) {
      reset();
      if(editing.type!=='series')return;
      title=editing;
      active=new AbortController();
      setSummary(prepared??editorStore.cachedSeries(editing.id));
      writtenStatus=status();
      openingStatus=editing.entry?.status;
      initialStatus=status();
      switchScope(season??0);
      if (!summary?.ready || (scope&&!scopedSeason())) void read();
    },
    receiveDates(entry:Record<string,unknown>,previous:Record<string,unknown>) {
      const overall=draft(0);
      if(!overall)return;
      for(const key of ['start_date','finish_date'] as const) {
        if(key in entry&&overall[key]===String(previous[key]??''))overall[key]=String(entry[key]??'');
        if(!scope)field(key).value=overall[key];
      }
    },
    fields:()=>draft(0)??values(),
    artworkScope:()=>({number:scope,summary}),
    scopedStatusChange:()=>scope ? status()!==scopeBaseline() : undefined,
    deleteTarget:()=>scope ? {season_number:scope,revision:summary?.revision} : null,
    statusChanged() {
      if (!title) return;
      if (writtenStatus!==status()) {
        if (status()==='completed') {
          if (scope) {progressDrafts.delete(scope);previousCounts.delete(scope);}
          else {progressDrafts.clear();previousCounts.clear();}
        }
        writtenStatus=status();failure('');
      }
      render();
    },
    async patch(explicitStatus:boolean):Promise<SeriesPatch|false> {
      const completing=explicitStatus&&status()==='completed';
      if (scope && (!scopedSeason() || (completing&&!scopedSeason()?.episodes.length))) {
        failure('Episode progress is unavailable for this season.');
        return false;
      }
      const seasonAction = scope && completing ? 'complete_season'
        : scope && !progressDrafts.has(scope) && explicitStatus && status()==='watching' && scopedSeason()?.manual_completion ? 'resume_season' : undefined;
      const last=!scope && completing ? released().at(-1) : undefined;
      const next=last && position(last,last.episodes.length);
      const counts=Object.fromEntries(seasons().flatMap(season=>{
        const number=season.season_number;
        const count=progressDrafts.get(number);
        return count===undefined || count===baselineCount(season) || (number===scope && seasonAction==='complete_season')
          ? [] : [[number,count]];
      }));
      const hasProgress=Object.keys(counts).length>0;
      const editedSeason=scope && !seasonAction && Object.keys(counts).length===1 && scope in counts ? scopedSeason() : undefined;
      if ((completing||hasProgress) && !summary?.ready) {
        failure('Episode progress is unavailable. Try again shortly.');
        void read();
        return false;
      }
      const seasonStatuses=scope&&explicitStatus&&!completing?{[scope]:status()}:undefined;
      const edits=seasons().flatMap(season=>{
        const fields=draft(season.season_number);
        if (!fields) return [];
        const scoreChanged=Number(fields.manual_score)!==Number(season.score??0);
        const finishChanged=season.manual_completion && fields.finish_date!==(season.completion_date??'');
        if (!scoreChanged && !finishChanged) return [];
        return [{season_number:season.season_number,membership:season.membership,
          ...(scoreChanged?{score:Number(fields.manual_score)||null}:{}),
          ...(finishChanged?{completion_date:fields.finish_date||null}:{})}];
      });
      return {revision:summary?.revision,order:summary?.order,watch_date:localToday(),seasons:edits,
        ...(hasProgress&&!editedSeason?{season_progress:counts}:{}),
        season_statuses:seasonStatuses,...(seasonAction?{action:seasonAction,season_number:scope}
          :editedSeason?{action:'position' as const,...position(editedSeason,counts[scope]),progress_season:scope}
          :next?{action:'position' as const,...next}:{})};
    },
    rollbackNeeded(patch:SeriesPatch) {
      if (patch.season_progress && seasons().some(season=>
        (patch.season_progress![season.season_number] ?? watched(season))<watched(season))) return true;
      if (patch.action!=='position') return false;
      if (patch.progress_season) {
        const season=seasons().find(item=>item.season_number===patch.progress_season);
        return Boolean(season && (patch.episode_count??0)<watched(season));
      }
      const episodes=seasons().flatMap(season=>season.episodes);
      return episodes.slice(episodes.findIndex(episode=>episode.id===patch.episode_id)+1).some(episode=>episode.watched);
    },
    optimisticProgress(patch:SeriesPatch) {
      if (patch.season_progress && !patch.action && patch.order==='tmdb:aired') return {progress:seasons().reduce((sum,season)=>
        sum+(patch.season_progress![season.season_number]??watched(season)),0)};
      if (patch.action!=='position'||patch.season_progress||patch.progress_season) return undefined;
      return {progress:seasons().flatMap(season=>season.episodes).findIndex(episode=>episode.id===patch.episode_id)+1};
    },
    refresh:read,
  };
}
