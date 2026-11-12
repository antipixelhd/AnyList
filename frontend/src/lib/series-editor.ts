import {animate} from 'motion/mini';
import {formatDateTime, localToday} from './date-time';
import {editorStore, type EditorTitle} from './editor-store';
import {closeDialog, hideMenu, showDialog, showMenu} from './ui-motion';
import {responsiveArtwork} from './responsive-artwork';

type Episode = {id:number; number:number; watched:boolean};
type Season = {season_number:number; membership:string; name?:string|null; total:number; episodes:Episode[];
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
  refreshDates:()=>void, failure:(message:string)=>void) {
  const panel = form.querySelector<HTMLElement>('[data-series-editor]')!;
  const details = form.querySelector<HTMLDetailsElement>('[data-series-details]')!;
  const overallStart = form.querySelector<HTMLElement>('.editor-start-date')!;
  const overallFinish = form.querySelector<HTMLElement>('.editor-finish-date')!;
  const startPosition = document.createComment('series start date position');
  overallStart.before(startPosition);
  const find = <T extends HTMLElement = HTMLElement>(name:string) => panel.querySelector<T>(`[data-series-${name}]`)!;
  const field = (name:string) => form.elements.namedItem(name) as HTMLInputElement;
  const input = field('series_count');
  const list = panel.querySelector<HTMLElement>('#editor-season-list')!;
  const card = find('card');
  const art = find('art');
  const placeholder = find('image-placeholder');
  const watch = find<HTMLButtonElement>('watch');
  const prompt = document.querySelector<HTMLDialogElement>('[data-series-airing]')!;
  const drafts = new Map<number,Draft>();
  let title:EditorTitle|null = null;
  let summary:SeriesEditorSummary|undefined;
  let selected:number|undefined;
  let mode:'episode'|'season' = 'season';
  let choseMode = false;
  let choseSeason = false;
  let stagedPosition:{id:number|null; count:number; season:number}|undefined;
  let active:AbortController|undefined;
  let mediaRead:AbortController|undefined;
  let timer:ReturnType<typeof setTimeout>|undefined;
  let imageTimer:ReturnType<typeof setTimeout>|undefined;
  let request:Promise<void>|undefined;
  let busy = false;
  let loadFailed = false;
  let cardKey = '';
  let imageKey = '';
  let imageMotion:ReturnType<typeof animate>|undefined;
  let cardMotion:ReturnType<typeof animate>|undefined;
  let generation = 0;
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const seasons = () => summary?.seasons ?? [];
  const season = () => seasons().find(item => item.season_number === selected);
  const released = () => seasons().filter(item=>item.episodes.length);
  const watchedCount = (s:Season) => s.episodes.filter(ep=>ep.watched).length;
  const selectedEpisode = (s:Season) => s.episodes.find(ep=>!ep.watched) ?? s.episodes.at(-1);
  const status = () => field('status').value;
  const tracked = () => Boolean(title && (status()==='watching' || (Number(title.entry?.progress)>0)
    || summary?.seasons.some(s=>watchedCount(s)>0)));
  const errorMessage = (cause:unknown) => (cause as Error)?.message || 'Unable to save progress.';
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
    if (!choseMode) mode = current && watchedCount(current)>0 ? 'episode' : 'season';
  };
  const resetImage = () => {
    mediaRead?.abort();
    mediaRead = undefined;
    clearTimeout(imageTimer);
    imageMotion?.cancel();
    art.querySelector('img')?.remove();
    placeholder.hidden = true;
    imageKey = '';
  };
  const paintImage = (path:string|null|undefined, key:string) => {
    const artwork = responsiveArtwork(path,{size:'w300',sizes:'(max-width:650px) 76px, 92px',route:'direct'});
    if (!artwork || imageKey!==key) return;
    const img = new Image();
    img.alt = '';
    img.decoding = 'async';
    if (artwork.sizes) img.sizes = artwork.sizes;
    if (artwork.srcset) img.srcset = artwork.srcset;
    img.src = artwork.src;
    void img.decode().then(()=>{
      if (key!==imageKey || mediaRead?.signal.aborted || !active || active.signal.aborted) return;
      clearTimeout(imageTimer);
      placeholder.hidden = true;
      art.append(img);
      if (!reduced()) imageMotion = animate(img,{opacity:[0,1]},{duration:.18,ease:'easeOut'});
    }).catch(()=>{
      if (key===imageKey) {clearTimeout(imageTimer);placeholder.hidden=true;}
    });
  };
  const episodeDetails = (episode:Episode, key:string) => {
    if (!title?.id || imageKey===key) return;
    resetImage();
    imageKey = key;
    const controller = new AbortController();
    mediaRead = controller;
    const cached = title.seasons?.find(s=>s.episodes?.some(ep=>ep.id===episode.id))?.episodes?.find(ep=>ep.id===episode.id);
    const metadata = cached ? Promise.resolve(cached) : editorStore.readSeriesEpisode(title.id,episode.id,controller.signal);
    imageTimer = setTimeout(()=>{if (key===imageKey) placeholder.hidden=false;},160);
    void metadata.then(data=>{
      if (controller.signal.aborted || key!==imageKey) return;
      find('title').textContent = data.title || '';
      find('meta').textContent = [data.runtime ? `${data.runtime} min` : '', data.air_date ? formatDateTime(data.air_date) : ''].filter(Boolean).join(' · ');
      if (data.image) paintImage(data.image,key);
      else {clearTimeout(imageTimer);placeholder.hidden=true;}
    }).catch(()=>{
      if (!controller.signal.aborted && key===imageKey) {clearTimeout(imageTimer);placeholder.hidden=true;}
    });
  };
  const layout = () => {
    const isTracked = tracked();
    panel.hidden = !isTracked;
    details.hidden = !isTracked;
    overallFinish.hidden = isTracked;
    panel.parentElement?.toggleAttribute('data-series-tracked',isTracked);
    if (isTracked && overallStart.parentElement!==details) details.append(overallStart);
    if (!isTracked && overallStart.parentElement===details) startPosition.after(overallStart);
    if (!isTracked) details.open=false;
  };
  const paintList = () => {
    if (list.hidden) return;
    const buttons = seasons().map(s=>{
      const button = document.createElement('button');
      button.type='button';
      button.dataset.seasonNumber=String(s.season_number);
      button.textContent=`Season ${s.season_number}`;
      button.setAttribute('aria-current',String(s.season_number===selected));
      return button;
    });
    const focused = (document.activeElement as HTMLElement)?.dataset.seasonNumber;
    list.replaceChildren(...buttons);
    if (focused) list.querySelector<HTMLElement>(`[data-season-number="${Number(focused)}"]`)?.focus({preventScroll:true});
  };
  const render = () => {
    if (!title) return;
    layout();
    if (panel.hidden) {resetImage();cardKey='';return;}
    const current = season();
    const episode = current ? selectedEpisode(current) : undefined;
    find('loading').hidden = !!current;
    find('loading').textContent = loadFailed ? 'Progress is unavailable.' : summary && !summary.pending && !summary.ready
      ? 'No released episodes are available.' : title.entry?.season_position
        ? `${title.entry.season_position} · Loading progress…` : 'Loading progress…';
    find('retry').hidden = !loadFailed;
    card.hidden = !current;
    find('nav').hidden = !current;
    find('fields').hidden = !current;
    find<HTMLButtonElement>('expand').disabled = seasons().length<2;
    for (const button of panel.querySelectorAll<HTMLButtonElement>('[data-series-mode]')) {
      button.setAttribute('aria-pressed',String(button.dataset.seriesMode===mode));
    }
    paintList();
    if (!current) return;
    const key = `${mode}:${current.season_number}:${mode==='episode'?episode?.id:''}`;
    const changed = cardKey!==key;
    card.dataset.mode=mode;
    if (changed) {
      cardKey=key;
      resetImage();
      find('title').textContent='';
      find('meta').textContent='';
      cardMotion?.cancel();
      if (!reduced() && dialog.open) cardMotion=animate(card,{opacity:[.55,1],transform:['translateY(3px)','translateY(0)']},{duration:.19,ease:'easeOut'});
    }
    art.hidden=mode!=='episode'||!episode;
    const count = watchedCount(current);
    find('position').textContent=mode==='episode' && episode
      ? `Season ${current.season_number}, Episode ${episode.number}` : `Season ${current.season_number}`;
    if (mode==='episode' && episode) episodeDetails(episode,key);
    if (mode==='season' || !episode) {
      find('title').textContent=current.name && current.name!==`Season ${current.season_number}` ? current.name : '';
      find('meta').textContent=`${count} / ${current.total} watched`;
    }
    find('count-label').textContent=`Season ${current.season_number} · ${count} / ${current.total}`;
    const earlierUnwatched=mode==='season' && seasons().some(s=>s.season_number<current.season_number
      && s.episodes.some(ep=>!ep.watched));
    const watched=mode==='episode'?episode?.watched:count===current.episodes.length && !earlierUnwatched;
    watch.disabled=!busy && (!current.episodes.length || (mode==='episode' && !episode) || Boolean(watched));
    watch.setAttribute('aria-busy',String(busy));
    watch.setAttribute('aria-disabled',String(busy || watch.disabled));
    watch.textContent=watched && !busy && current.episodes.length
      ? (mode==='season' && !current.completed?'Caught up':'Watched')
      : earlierUnwatched ? `Mark through Season ${current.season_number}` : 'Mark watched';
    watch.setAttribute('aria-label',mode==='season'?`Mark watched through season ${current.season_number}`:'Mark episode watched');
    find('score').hidden=mode!=='season';
    const draft=drafts.get(current.season_number);
    input.disabled=!current.episodes.length;
    input.max=String(current.episodes.length);
    input.value=String(stagedPosition?.season===current.season_number?stagedPosition.count:count);
    field('series_score').value=String((draft && 'score' in draft ? draft.score : current.score) ?? 0);
    const start=(draft && 'start_date' in draft ? draft.start_date : current.start_date) ?? '';
    const finish=(draft && 'completion_date' in draft ? draft.completion_date : current.completion_date) ?? '';
    const datesChanged=changed || field('series_start').value!==start || field('series_finish').value!==finish;
    field('series_start').value=start;
    field('series_finish').value=finish;
    for (const [name, source] of [['start',current.start_source],['finish',current.date_source]]) {
      const display=panel.querySelector<HTMLElement>(`#editor-season-${name}`)!;
      display.title=source==='inferred'?'Estimated date':'';
    }
    find('finish').hidden=!current.completed;
    for (const button of panel.querySelectorAll<HTMLButtonElement>('[data-series-navigate]')) {
      button.disabled=!seasons()[seasons().indexOf(current)+Number(button.dataset.seriesNavigate)];
    }
    if (datesChanged) refreshDates();
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
        if (!season() || (next.ready && !choseSeason && !stagedPosition && !drafts.size)) selectCurrent();
        if (!next.ready && next.pending) timer=setTimeout(()=>void read(), Math.max(1,next.retry_after||2)*1000);
      } catch (cause) {
        if (!signal.aborted && version===generation) loadFailed=true;
      } finally {
        if (!signal.aborted && version===generation) {request=undefined;render();}
      }
    })();
    return request;
  }
  const selectSeason = (number:number) => { selected=number;choseSeason=true;render(); };
  const setCount = (count:number, target=season()) => {
    if (!target || !Number.isInteger(count) || count<0 || count>target.episodes.length) return;
    const previous = released().filter(s=>s.season_number<target.season_number).at(-1)?.episodes.at(-1);
    stagedPosition={id:count?target.episodes[count-1].id:previous?.id??null,count,season:target.season_number};
  };
  const step = (direction:number) => {
    const current=season();
    if (!current) return;
    const count=Number(input.value);
    if (direction>0 && count===current.episodes.length || direction<0 && count===0) {
      const choices=released();
      const next=choices[choices.indexOf(current)+direction];
      if (!next) return;
      selected=next.season_number;
      choseSeason=true;
      setCount(direction>0?1:Math.max(0,next.episodes.length-1),next);
    } else setCount(count+direction);
    render();
    input.focus({preventScroll:true});
  };
  const markWatched = async () => {
    const current=season();
    if (!current || !summary || !title || busy || watch.disabled) return;
    const editing=title;
    const opened=active;
    const previous=summary;
    const previousSelection=selected;
    const episode=mode==='episode'?selectedEpisode(current):current.episodes.at(-1);
    if (!episode) return;
    const action=mode==='episode'?'episode':'through_season';
    const all=previous.seasons.flatMap(s=>s.episodes);
    const additions=new Set(action==='episode'?[episode.id]:all.slice(0,all.indexOf(episode)+1).map(ep=>ep.id));
    const next=structuredClone(previous);
    for (const s of next.seasons) for (const ep of s.episodes) if (additions.has(ep.id)) ep.watched=true;
    summary=next;
    if (action==='episode') {
      const following=next.seasons.find(s=>s.season_number>=current.season_number && s.episodes.some(ep=>!ep.watched));
      if (following) selected=following.season_number;
    }
    clearTimeout(timer);
    generation++;
    request=undefined;
    busy=true;
    render();
    try {
      const progress=next.seasons.reduce((sum,s)=>sum+watchedCount(s),0);
      const status=editing.entry?.status;
      const optimisticStatus=status==='completed'?'completed':'watching';
      const saved=await editorStore.write(editing,{series_editor:{revision:previous.revision,action,episode_id:episode.id,
        watch_date:localToday()}},{progress,status:optimisticStatus});
      if (opened!==active || opened?.signal.aborted) return;
      summary=saved.series_editor;
      find('announcement').textContent=action==='episode'?'Episode marked watched.':'Season marked watched.';
      document.dispatchEvent(new CustomEvent('anylist:entry-saved',{detail:saved}));
    } catch (cause) {
      if (opened!==active || opened?.signal.aborted) return;
      summary=previous;
      selected=previousSelection;
      failure(errorMessage(cause));
      void read();
    } finally {
      if (opened===active) {busy=false;render();}
    }
  };
  panel.addEventListener('click',event=>{
    const button=(event.target as HTMLElement).closest<HTMLButtonElement>('button');
    if (!button) return;
    if (button.dataset.seriesMode==='episode'||button.dataset.seriesMode==='season') {
      choseMode=true;mode=button.dataset.seriesMode;render();
    } else if (button.hasAttribute('data-series-watch')) {
      void markWatched();
    } else if (button.hasAttribute('data-series-expand')) {
      const open=button.getAttribute('aria-expanded')!=='true';
      button.setAttribute('aria-expanded',String(open));
      if (open) {showMenu(list,reduced());paintList();}
      else void hideMenu(list,reduced());
    } else if (button.dataset.seasonNumber) {
      selectSeason(Number(button.dataset.seasonNumber));
      find('expand').setAttribute('aria-expanded','false');
      find('expand').focus({preventScroll:true});
      void hideMenu(list,reduced());
    } else if (button.dataset.seriesNavigate) {
      const current=season();
      if (current) {
        const next=seasons()[seasons().indexOf(current)+Number(button.dataset.seriesNavigate)];
        if (next) selectSeason(next.season_number);
      }
    } else if (button.dataset.seriesStep) step(Number(button.dataset.seriesStep));
    else if (button.hasAttribute('data-series-retry')) {loadFailed=false;render();void read();}
  },{signal:page});
  input.addEventListener('keydown',event=>{
    if (event.key==='ArrowUp'||event.key==='ArrowDown') {
      event.preventDefault();
      step(event.key==='ArrowUp'?1:-1);
    }
  },{signal:page});
  panel.addEventListener('input',event=>{
    if (event.target===input && input.value!=='') setCount(Number(input.value));
    const node=event.target as HTMLInputElement;
    const current=season();
    if (!current || !node.validity?.valid) return;
    if (node.id==='editor-season-start') membershipDraft(current).start_date=field('series_start').value||null;
    if (node.id==='editor-season-finish') membershipDraft(current).completion_date=field('series_finish').value||null;
  },{signal:page});
  panel.addEventListener('change',event=>{
    const current=season();
    if (!current) return;
    const node=event.target as HTMLInputElement;
    if (node===input) {render();return;}
    if (node.name==='series_score') membershipDraft(current).score=Number(node.value)||null;
    if (node.name==='series_start') membershipDraft(current).start_date=node.value||null;
    if (node.name==='series_finish') membershipDraft(current).completion_date=node.value||null;
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
    cardMotion?.cancel();
    resetImage();
    drafts.clear();
    stagedPosition=undefined;
    summary=undefined;
    selected=undefined;
    choseSeason=false;
    title=null;
    choseMode=false;
    busy=false;
    loadFailed=false;
    cardKey='';
    request=undefined;
    generation++;
    list.hidden=true;
    list.inert=false;
    find('expand').setAttribute('aria-expanded','false');
    find('announcement').textContent='';
    if (wasSeries) layout();
  };
  page.addEventListener('abort',reset,{once:true});
  return {
    reset,
    open(editing:EditorTitle) {
      reset();
      if (editing.type!=='series') return;
      title=editing;
      active=new AbortController();
      summary=editorStore.cachedSeries(editing.id);
      if (summary) selectCurrent();
      render();
      void read();
    },
    statusChanged() { if (title) {render();if (!summary?.ready) void read();} },
    busy:()=>busy,
    tracked,
    async patch(completing:boolean) {
      if (busy) return false;
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
      return {revision:current?.revision,order:current?.order,action,watch_date:localToday(),
        ...(action==='position'?{episode_id:stagedPosition?.id}:{}),seasons:[...drafts.values()]};
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
