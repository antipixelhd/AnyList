import {localToday} from './date-time';
import {setupEditorSelect} from './editor-select';
import {editorStore, type EditorTitle} from './editor-store';
import {statuses} from './tracking-statuses';
import {closeDialog, showDialog} from './ui-motion';

type Episode = {id:number; number:number; watched:boolean};
type Season = {season_number:number; membership:string; name?:string|null; poster?:string|null; total:number;
  episodes:Episode[]; upcoming?:Pick<Episode,'id'|'number'>[]; score?:number|null;
  start_date?:string|null; completion_date?:string|null; start_source?:string|null;
  date_source?:string|null; start_manual?:boolean; date_manual?:boolean; completed?:boolean};
export type SeriesEditorSummary = {ready:boolean; pending?:boolean; retry_after?:number; revision?:string;
  selected_position?:{season_number:number;episode_count:number;episode_id:number|null}|null;
  order?:string; seasons:Season[]};
export type EditorEpisodeMetadata = {id:number; title:string; runtime?:number|null; air_date?:string|null; image?:string|null};
type Position = {season_number:number; episode_count:number; episode_id:number|null};
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
  const seasonSelect = panel.querySelector<HTMLSelectElement>('#series-season')!;
  const seasonRoot = seasonSelect.closest<HTMLElement>('[data-editor-select]')!;
  const seasonControl = setupEditorSelect(seasonRoot,page);
  const input = find<HTMLInputElement>('count');
  const steps = panel.querySelectorAll<HTMLButtonElement>('[data-series-step]');
  const scopeMessage = form.querySelector<HTMLElement>('[data-season-field-message]')!;
  const placement = document.querySelector<HTMLDialogElement>('[data-series-placement]')!;
  const drafts = new Map<number,ScopeFields>();
  let title:EditorTitle|null = null;
  let summary:SeriesEditorSummary|undefined;
  let scope = 0;
  let staged:Position|undefined;
  let progressScope:number|undefined;
  let writtenStatus:string|undefined;
  let openingStatus:string|undefined;
  let active:AbortController|undefined;
  let timer:ReturnType<typeof setTimeout>|undefined;
  let request:Promise<void>|undefined;
  let loadFailed = false;

  const seasons = () => summary?.seasons ?? [];
  const released = () => seasons().filter(season=>season.episodes.length);
  const status = () => field('status').value;
  const name = (season:Season) => season.name?.trim() || `Season ${season.season_number}`;
  const watched = (season:Season) => season.episodes.filter(episode=>episode.watched).length;
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
    const earlier = released().findLast(item=>item.season_number<season.season_number)?.episodes.at(-1);
    return {season_number:season.season_number,episode_count:count,
      episode_id:count?season.episodes[count-1].id:earlier?.id??null};
  }
  function defaultPosition() {
    const saved = savedPosition();
    if (status()==='completed' && openingStatus!=='completed') {
      const last = released().at(-1);
      if (last) return position(last,last.episodes.length);
    }
    return saved;
  }
  const currentPosition = () => staged ?? defaultPosition();
  function previewCount(season:Season) {
    const preview=staged??(status()==='completed'&&openingStatus!=='completed'?defaultPosition():undefined);
    if (!preview) return summary?.selected_position?.season_number===season.season_number
      ? summary.selected_position.episode_count : watched(season);
    if (season.season_number<preview.season_number) return season.episodes.length;
    return season.season_number===preview.season_number ? preview.episode_count : 0;
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
    input.disabled=true;
    if (!title) {scopeControl.close(true);seasonControl.close(true);scopeMessage.hidden=true;return;}
    const pos = currentPosition();
    const current = seasons().find(season=>season.season_number===(scope||pos?.season_number));
    syncOptions(scopeSelect,[['0','All Seasons'],...seasons().map(season=>[String(season.season_number),name(season)] as [string,string])],String(scope));
    scopeControl.sync();
    syncOptions(seasonSelect,released().map(season=>[String(season.season_number),name(season)]),String(pos?.season_number??''));
    seasonRoot.hidden=Boolean(scope);
    seasonControl.sync();
    find('loading').hidden=Boolean(current);
    find('loading').textContent=loadFailed?'Progress is unavailable.':summary&&!summary.pending?'No released episodes.':'Loading progress…';
    find('retry').hidden=!loadFailed;
    find('content').hidden=!current;
    panel.querySelector('label')!.textContent=scope?'Episode Progress':'Series Progress';
    input.disabled=!current?.episodes.length || !summary?.ready;
    const count=current ? scope ? previewCount(current) : Math.max(1,pos?.episode_count??1) : 0;
    input.min=scope?'0':'1';
    input.max=String(current?.episodes.length??0);
    if (!keepInput) input.value=String(count);
    find('total').textContent=current ? `/ ${current.total||current.episodes.length}` : '';
    const choices=released(), index=current?choices.indexOf(current):-1;
    steps[0].disabled=input.disabled||(count>=Number(input.max)&&(!!scope||index===choices.length-1));
    steps[1].disabled=input.disabled||(count<=Number(input.min)&&(!!scope||index<=0));
    const seasonStatus=find('status');
    seasonStatus.hidden=!scope||!current;
    if (scope&&current) {
      const full=current.total>0&&count>=current.total;
      const seriesStatus=status();
      const activeSeason=seasons().find(season=>season.season_number===pos?.season_number);
      const nextPlanned=seriesStatus==='planning'&&activeSeason&&activeSeason.total>0&&previewCount(activeSeason)>=activeSeason.total
        ? seasons().find(season=>season.season_number>activeSeason.season_number&&previewCount(season)<season.total) : undefined;
      const active=(nextPlanned?.season_number??pos?.season_number)===scope;
      const inherits=active&&['planning','paused','dropped'].includes(seriesStatus);
      const activeStatus=inherits?seriesStatus:full?'completed':count>0||active&&seriesStatus==='watching'?'watching':'';
      seasonStatus.textContent=statuses.find(([key])=>key===activeStatus)?.[1]??'';
      seasonStatus.hidden=!seasonStatus.textContent;
    }
    refreshScopeMessage();
  }
  function refreshScopeMessage() {
    const season=seasons().find(item=>item.season_number===scope);
    const original=season && seasonFields(season);
    scopeMessage.hidden=!original||!(['start_date','finish_date','rewatch_count'] as const).some(key=>field(key).value!==original[key]);
  }
  function labelRewatches(label:string) {
    const container=form.querySelector('.editor-rewatches')!;
    container.querySelector('label')!.textContent=label;
    field('rewatch_count').setAttribute('aria-label',label);
    container.querySelector('[data-score-step=up]')!.setAttribute('aria-label',`Increase ${label.toLowerCase()}`);
    container.querySelector('[data-score-step=down]')!.setAttribute('aria-label',`Decrease ${label.toLowerCase()}`);
  }
  function switchScope(next:number) {
    failure('');
    drafts.set(scope,values());
    const season=seasons().find(item=>item.season_number===next);
    scope=season?.season_number??0;
    const fields=drafts.get(scope)??(season&&seasonFields(season));
    if(fields)for(const key of scopeFields)field(key).value=fields[key];
    const scoreLabel=scope?'Season score':'Series score';
    form.querySelector('.editor-score-field label')!.textContent=scoreLabel;
    field('manual_score').setAttribute('aria-label',scoreLabel);
    labelRewatches(scope?'Rewatches':'Total rewatches');
    form.dispatchEvent(new CustomEvent('anylist:series-dates-sync',{bubbles:true}));
    render();
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
        if (next.ready || !summary?.ready) summary=next;
        loadFailed=false;
        if (staged) {
          const season=released().find(item=>item.season_number===staged?.season_number);
          staged=season && staged.episode_count<=season.episodes.length ? position(season,staged.episode_count) : undefined;
        }
        if (!next.ready && next.pending) timer=setTimeout(()=>void read(),Math.max(1,next.retry_after||2)*1000);
      } catch {
        if (!signal.aborted) loadFailed=true;
      } finally {
        if (!signal.aborted) {request=undefined;render();}
      }
    })();
    return request;
  }
  function setPosition(season:Season,count:number) {
    if (scope && scope!==savedPosition()?.season_number) {
      failure('You can only change episode progress for your current season.');
      render();
      return;
    }
    staged=position(season,count);
    progressScope=scope||undefined;
    failure('');
  }
  function step(direction:number) {
    const choices=released();
    const index=choices.findIndex(season=>season.season_number===(scope||currentPosition()?.season_number));
    const current=choices[index];
    if (!current || input.disabled) return;
    const next=Number(input.value||input.min)+direction;
    if (next>=Number(input.min)&&next<=current.episodes.length) setPosition(current,next);
    else if (!scope && choices[index+direction]) {
      const target=choices[index+direction];
      setPosition(target,direction>0?1:target.episodes.length);
    }
    render();
  }
  panel.addEventListener('click',event=>{
    const target=event.target as Element;
    const button=target.closest<HTMLButtonElement>('button');
    if(button?.dataset.seriesStep)step(Number(button.dataset.seriesStep));
    if(button?.hasAttribute('data-series-retry')){loadFailed=false;render();void read();}
    if(!button&&target!==input&&target.closest('.series-episode-stepper'))input.focus();
  },{signal:page});
  input.addEventListener('keydown',event=>{
    if (!['ArrowUp','ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    step(event.key==='ArrowUp'?1:-1);
  },{signal:page});
  input.addEventListener('input',()=>{
    const season=released().find(item=>item.season_number===(scope||currentPosition()?.season_number));
    if (!season || !input.validity.valid) return;
    setPosition(season,input.valueAsNumber);
    render(true);
  },{signal:page});
  scopeSelect.addEventListener('change',()=>switchScope(Number(scopeSelect.value)),{signal:page});
  seasonSelect.addEventListener('change',()=>{
    const season=released().find(item=>item.season_number===Number(seasonSelect.value));
    if (season) setPosition(season,Math.max(1,previewCount(season)));
    render();
  },{signal:page});
  for(const event of ['input','change'])form.addEventListener(event,()=>{
    if (scope) refreshScopeMessage();
  },{signal:page});
  placement.addEventListener('click',event=>{
    if(event.target===placement)void closeDialog(placement,'');
  },{signal:page});
  function choosePlacement(value:string) {
    const label=statuses.find(([key])=>key===value)?.[1]??value;
    placement.querySelector('h2')!.textContent=`Move to ${label}?`;
    return new Promise<'season'|'series'|null>(resolve=>{
      const signal=active!.signal;
      const abort=()=>placement.close('');
      placement.returnValue='';
      signal.addEventListener('abort',abort,{once:true});
      placement.addEventListener('close',()=>{
        signal.removeEventListener('abort',abort);
        const result=placement.returnValue;
        placement.returnValue='';
        resolve(result==='series'||result==='season'?result:null);
      },{once:true});
      showDialog(placement);
    });
  }
  function reset() {
    if(title) {
      form.querySelector('.editor-score-field label')!.textContent='Score';
      field('manual_score').setAttribute('aria-label','Score');
      labelRewatches('Total rewatches');
    }
    active?.abort();
    clearTimeout(timer);
    request=undefined;
    title=null;
    summary=undefined;
    scope=0;
    staged=undefined;
    progressScope=undefined;
    writtenStatus=undefined;
    openingStatus=undefined;
    drafts.clear();
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
      summary=prepared??editorStore.cachedSeries(editing.id);
      writtenStatus=status();
      openingStatus=editing.entry?.status;
      switchScope(season??0);
      if (!summary?.ready) void read().then(()=>{
        if (scope===0 && season && title===editing && seasons().some(item=>item.season_number===season))switchScope(season);
      });
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
    statusChanged() {
      if (!title) return;
      if (writtenStatus!==status()) {
        staged=undefined;progressScope=undefined;writtenStatus=status();failure('');
      }
      render();
    },
    async patch(explicitStatus:boolean) {
      const completing=explicitStatus&&status()==='completed';
      const first=released()[0];
      const next=staged??(completing?defaultPosition():!openingStatus&&first?position(first,1):undefined);
      if ((completing||next) && !summary?.ready) {
        failure('Episode progress is unavailable. Try again shortly.');
        void read();
        return false;
      }
      const pausedOrDropped=status()==='paused'||status()==='dropped';
      const completed=openingStatus==='completed'||seasons().some(season=>season.completed||
        (season.total>0&&Math.max(watched(season),previewCount(season))>=season.total));
      let listPlacement:'season'|'series'|undefined;
      if (explicitStatus&&pausedOrDropped) {
        const choice=!scope&&completed?await choosePlacement(status()):'season';
        if (!choice) return false;
        listPlacement=choice;
      }
      const edits=seasons().flatMap(season=>{
        const score=draft(season.season_number)?.manual_score;
        if (score===undefined || Number(score)===Number(season.score??0)) return [];
        return [{season_number:season.season_number,membership:season.membership,score:Number(score)||null}];
      });
      return {revision:summary?.revision,order:summary?.order,watch_date:localToday(),seasons:edits,
        list_placement:listPlacement,...(next?{action:'position' as const,...next,progress_season:progressScope}:{})};
    },
    rollbackNeeded(patch:{action?:string;episode_id?:number|null}) {
      if (patch.action!=='position') return false;
      const episodes=seasons().flatMap(season=>season.episodes);
      return episodes.slice(episodes.findIndex(episode=>episode.id===patch.episode_id)+1).some(episode=>episode.watched);
    },
    optimisticProgress(patch:{action?:string;episode_id?:number|null}) {
      if (patch.action!=='position') return undefined;
      return {progress:seasons().flatMap(season=>season.episodes).findIndex(episode=>episode.id===patch.episode_id)+1};
    },
    refresh:read,
  };
}
