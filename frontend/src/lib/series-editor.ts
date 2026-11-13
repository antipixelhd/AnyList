import {animate} from 'motion/mini';
import {formatDateTime, localToday} from './date-time';
import {editorStore, type EditorTitle} from './editor-store';
import {closeDialog, showDialog} from './ui-motion';
import {setupCalendarDateInputs} from './calendar-date-input';
import {setupScoreSteppers} from './scoreStepper';
import {responsiveArtwork} from './responsive-artwork';

type Episode = {id:number; number:number; watched:boolean};
type Season = {season_number:number; membership:string; name?:string|null; total:number; episodes:Episode[];
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
  const list = find('list');
  const rows = find('rows');
  const rowTemplate = find<HTMLTemplateElement>('row-template');
  const fieldsTemplate = find<HTMLTemplateElement>('fields-template');
  const episodeList = find('episodes');
  const episodeTemplate = find<HTMLTemplateElement>('episode-template');
  const expand = find<HTMLButtonElement>('expand');
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
  let allSeasons = false;
  let allEpisodes = false;
  let episodeListSeason:number|undefined;
  let shownView:HTMLElement|undefined;
  let viewMotion:ReturnType<typeof animate>|undefined;
  const renderedRows = new Map<number,HTMLElement>();
  const episodeRows = new Map<number,HTMLElement>();
  const rowControls = new Map<number, () => void>();
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
  const full = (s:Season) => s.completed || (s.episodes.length > 0 && s.episodes.length === s.total &&
    s.episodes.every(ep=>ep.watched));
  const canMarkSeason = (s:Season) => s.episodes.length>0 &&
    seasons().some(candidate=>candidate.season_number<=s.season_number && candidate.episodes.some(ep=>!ep.watched));
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
    overallStart.hidden = isTracked;
    overallFinish.hidden = isTracked;
    panel.parentElement?.toggleAttribute('data-series-tracked',isTracked);
  };
  const updateRowFields = (row:HTMLElement, s:Season, resetInvalid=false) => {
    const count=row.querySelector<HTMLInputElement>('[data-season-count]');
    if (!count) return;
    const number=s.season_number;
    const draft=drafts.get(number);
    const score=row.querySelector<HTMLInputElement>('[name=series_score]')!;
    const start=row.querySelector<HTMLInputElement>('[name=series_start]')!;
    const finish=row.querySelector<HTMLInputElement>('[name=series_finish]')!;
    count.disabled=!s.episodes.length;
    count.max=String(s.episodes.length);
    if (resetInvalid || document.activeElement!==count) {
      count.value=String(stagedPosition?.season===number?stagedPosition.count:watchedCount(s));
    }
    if (resetInvalid || document.activeElement!==score) {
      score.value=String((draft && 'score' in draft ? draft.score : s.score) ?? '');
    }
    const startChanged=start.value!==((draft && 'start_date' in draft ? draft.start_date : s.start_date) ?? '');
    const finishChanged=finish.value!==((draft && 'completion_date' in draft ? draft.completion_date : s.completion_date) ?? '');
    if (startChanged || finishChanged || resetInvalid) {
      start.value=(draft && 'start_date' in draft ? draft.start_date : s.start_date) ?? '';
      finish.value=(draft && 'completion_date' in draft ? draft.completion_date : s.completion_date) ?? '';
      rowControls.get(number)?.();
    }
    const canFinish=canFinishSeason(s);
    for (const input of finish.closest('[data-calendar-input]')!.querySelectorAll<HTMLInputElement|HTMLButtonElement>('input,button[data-date-picker]')) {
      input.disabled=!canFinish;
    }
    for (const [name, source] of [['start',s.start_source],['finish',s.date_source]]) {
      const display=row.querySelector<HTMLInputElement>(`#editor-season-${number}-${name}`)!;
      display.title=name==='finish'&&!canFinish?'Complete the season to set its finish date':source==='inferred'?'Estimated date':'';
    }
  };
  const setRowOpen = (row:HTMLElement, open:boolean) => {
    const s=fromRow(row);
    if (!s) return;
    const reveal=row.querySelector<HTMLElement>('[data-season-reveal]')!;
    const toggle=row.querySelector<HTMLButtonElement>('[data-season-toggle]')!;
    const clip=reveal.firstElementChild!;
    if (open && !clip.firstElementChild) {
      clip.append(fieldsTemplate.content.cloneNode(true));
      const number=s.season_number;
      for (const name of ['start','finish','score','count']) {
        const input=clip.querySelector<HTMLInputElement>(`#season-${name}`)!;
        input.id=`editor-season-${number}-${name}`;
        clip.querySelector<HTMLLabelElement>(`[data-season-date-label="${name}"],[data-season-${name}-label]`)!.htmlFor=input.id;
        if (name==='count') input.dataset.seasonCount='';
      }
      rowControls.set(number,setupCalendarDateInputs(clip,active!.signal));
      setupScoreSteppers(clip,active!.signal);
      updateRowFields(row,s);
    }
    if (!open && reveal.contains(document.activeElement)) toggle.focus({preventScroll:true});
    if (!open) updateRowFields(row,s,true);
    toggle.setAttribute('aria-expanded',String(open));
    row.toggleAttribute('data-open',open);
    reveal.inert=!open;
  };
  const paintList = () => {
    const current=seasons();
    for (const [index,s] of current.entries()) {
      let row=renderedRows.get(s.season_number);
      if (!row) {
        row=rowTemplate.content.firstElementChild!.cloneNode(true) as HTMLElement;
        row.dataset.seasonNumber=String(s.season_number);
        const reveal=row.querySelector<HTMLElement>('[data-season-reveal]')!;
        reveal.id=`editor-season-${s.season_number}-details`;
        row.querySelector('[data-season-toggle]')!.setAttribute('aria-controls',reveal.id);
        renderedRows.set(s.season_number,row);
        const following=current.slice(index+1).find(item=>renderedRows.has(item.season_number));
        rows.insertBefore(row,following?renderedRows.get(following.season_number)!:null);
      }
      row.hidden=!allSeasons && s.season_number!==selected;
      const toggle=row.querySelector<HTMLButtonElement>('[data-season-toggle]')!;
      toggle.hidden=!allSeasons;
      if (!allSeasons && !row.hidden && !row.hasAttribute('data-open')) setRowOpen(row,true);
      const title=`Season ${s.season_number}`;
      const label=s.name && s.name!==title ? `${title} · ${s.name}` : title;
      row.querySelector('[data-season-label]')!.textContent=label;
      const heading=row.querySelector<HTMLElement>('[data-season-title]')!;
      heading.textContent=label;
      heading.hidden=allSeasons;
      const count=stagedPosition?.season===s.season_number?stagedPosition.count:watchedCount(s);
      const progress=row.querySelector<HTMLElement>('[data-season-progress]')!;
      progress.textContent=s.total?`${count}/${s.total}`:'';
      if (s.episodes.length) progress.setAttribute('aria-label',`${count} of ${s.total} episodes watched`);
      else progress.removeAttribute('aria-label');
      const button=row.querySelector<HTMLButtonElement>('[data-season-watch]')!;
      const canMark=canMarkSeason(s);
      const moveFocus=!canMark && document.activeElement===button;
      button.hidden=!canMark;
      button.setAttribute('aria-disabled',String(busy));
      button.setAttribute('aria-label',`Mark watched through season ${s.season_number}`);
      const state=row.querySelector<HTMLElement>('[data-season-state]')!;
      state.hidden=canMark;
      state.textContent=!s.episodes.length?'Upcoming':full(s)?'Watched':'Caught up';
      if (moveFocus) {
        (allSeasons?toggle:panel.querySelector<HTMLElement>('[data-series-mode="season"]'))?.focus({preventScroll:true});
      }
      updateRowFields(row,s);
    }
    for (const [number,row] of renderedRows) {
      if (current.some(s=>s.season_number===number)) continue;
      row.remove();
      renderedRows.delete(number);
      rowControls.delete(number);
    }
  };
  const paintEpisodes = (s:Season) => {
    if (episodeListSeason!==s.season_number) {
      episodeList.replaceChildren();
      episodeRows.clear();
      episodeListSeason=s.season_number;
    }
    episodeList.setAttribute('aria-label',`Season ${s.season_number} episodes`);
    const episodes=[...s.episodes.map(episode=>({...episode,released:true})),
      ...(s.upcoming??[]).map(episode=>({...episode,watched:false,released:false}))].sort((a,b)=>a.number-b.number);
    const visibleIds=new Set(episodes.map(episode=>episode.id));
    for (const [index,episode] of episodes.entries()) {
      let row=episodeRows.get(episode.id);
      if (!row) {
        row=episodeTemplate.content.firstElementChild!.cloneNode(true) as HTMLElement;
        row.dataset.episodeId=String(episode.id);
        row.querySelector('[data-episode-label]')!.textContent=`Episode ${episode.number}`;
        episodeRows.set(episode.id,row);
        const following=episodes.slice(index+1).find(item=>episodeRows.has(item.id));
        episodeList.insertBefore(row,following?episodeRows.get(following.id)!:null);
      }
      const button=row.querySelector<HTMLButtonElement>('[data-episode-watch]')!;
      button.setAttribute('aria-label',`Mark Season ${s.season_number}, Episode ${episode.number} watched`);
      button.setAttribute('aria-disabled',String(busy));
      if (episode.watched && document.activeElement===button) {
        const next=s.episodes.find(item=>!item.watched && item.number>episode.number);
        const nextButton=next && episodeRows.get(next.id)?.querySelector<HTMLElement>('[data-episode-watch]');
        (nextButton ?? expand).focus({preventScroll:true});
      }
      button.hidden=episode.watched||!episode.released;
      row.querySelector<HTMLElement>('[data-episode-watched]')!.hidden=!episode.watched;
      row.querySelector<HTMLElement>('[data-episode-upcoming]')!.hidden=episode.released;
    }
    for (const [id,row] of episodeRows) {
      if (!visibleIds.has(id)) {
        row.remove();
        episodeRows.delete(id);
      }
    }
  };
  const render = () => {
    if (!title) return;
    layout();
    if (panel.hidden) {resetImage();cardKey='';return;}
    const current=season();
    const episode=current?selectedEpisode(current):undefined;
    const showEpisodes=mode==='episode' && allEpisodes && !!episode;
    const showEpisode=mode==='episode' && !allEpisodes && !!episode;
    const showList=mode==='season' && seasons().length>0;
    const loading=find('loading');
    loading.hidden=showEpisode||showEpisodes||showList;
    loading.textContent=loadFailed?'Progress is unavailable.':summary && !summary.pending
      ? (mode==='episode'?'No released episodes available.':'No seasons available.'):'Loading progress…';
    find('retry').hidden=!loadFailed;
    card.hidden=!showEpisode;
    list.hidden=!showList;
    episodeList.hidden=!showEpisodes;
    expand.hidden=mode==='episode' ? (current ? current.episodes.length+(current.upcoming?.length??0) : 0)<2 : seasons().length<2;
    const expanded=mode==='episode'?allEpisodes:allSeasons;
    expand.setAttribute('aria-expanded',String(expanded));
    expand.setAttribute('aria-controls',mode==='episode'?'editor-episode-list':'editor-season-list');
    expand.textContent=mode==='episode'
      ? (expanded?'Current episode':'All episodes')
      : (expanded?'Current season':'All seasons');
    list.toggleAttribute('data-current-only',!allSeasons);
    for (const button of panel.querySelectorAll<HTMLButtonElement>('[data-series-mode]')) {
      button.setAttribute('aria-pressed',String(button.dataset.seriesMode===mode));
    }
    const view=showEpisode?card:showEpisodes?episodeList:showList?list:undefined;
    if (shownView!==view) {
      shownView=view;
      viewMotion?.cancel();
      if (!reduced() && dialog.open && view) {
        viewMotion=animate(view,{opacity:[.65,1],transform:['translateY(3px)','translateY(0)']},{duration:.18,ease:'easeOut'});
      }
    }
    if (showList) paintList();
    if (showEpisodes && current) paintEpisodes(current);
    if (!showEpisode || !episode || !current) {resetImage();cardKey='';return;}
    const key=String(episode.id);
    if (cardKey!==key) {
      const advance=Boolean(cardKey);
      cardKey=key;
      find('title').textContent='';
      find('meta').textContent='';
      cardMotion?.cancel();
      if (advance && !reduced() && dialog.open) cardMotion=animate(card.querySelector('.series-editor-card-info')!,
        {opacity:[.6,1],transform:['translateX(3px)','translateX(0)']},{duration:.18,ease:'easeOut'});
    }
    find('position').textContent=`Season ${current.season_number}, Episode ${episode.number}`;
    episodeDetails(episode,key);
    watch.disabled=!busy && episode.watched;
    watch.setAttribute('aria-busy',String(busy));
    watch.setAttribute('aria-disabled',String(busy||watch.disabled));
    watch.textContent=episode.watched&&!busy?'Watched':'Mark watched';
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
        if (!season() || (next.ready && !busy && !stagedPosition && !drafts.size)) selectCurrent();
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
  const step = (input:HTMLInputElement, direction:number) => {
    const s=fromRow(input);
    if (!s || input.disabled) return;
    const count=Number(input.value);
    let target=s;
    if (direction>0 && count===s.episodes.length || direction<0 && count===0) {
      const choices=released();
      const next=choices[choices.indexOf(s)+direction];
      if (!next) return;
      target=next;
      setCount(target,direction>0?1:Math.max(0,target.episodes.length-1));
    } else {
      setCount(s,count+direction);
      input.value=String(stagedPosition?.count??count);
    }
    render();
    if (target!==s) {
      allSeasons=true;
      render();
      const row=renderedRows.get(target.season_number)!;
      setRowOpen(row,true);
      row.querySelector<HTMLDetailsElement>('[data-season-adjust]')!.open=true;
      row.querySelector<HTMLInputElement>('[data-season-count]')?.focus();
    }
  };
  const markWatched = async (current:Season, action:'episode'|'through_season', picked?:Episode) => {
    if (!summary || !title || busy || (action==='through_season' && !canMarkSeason(current))) return;
    const editing=title;
    const opened=active;
    const previous=summary;
    const previousSelection=selected;
    const episode=action==='episode'?(picked??selectedEpisode(current)):current.episodes.at(-1);
    if (!episode || (action==='episode' && episode.watched)) return;
    const all=previous.seasons.flatMap(s=>s.episodes);
    const additions=new Set(action==='episode'?[episode.id]:all.slice(0,all.indexOf(episode)+1).map(ep=>ep.id));
    const next=structuredClone(previous);
    for (const s of next.seasons) for (const ep of s.episodes) if (additions.has(ep.id)) ep.watched=true;
    summary=next;
    if (action==='episode' && !allEpisodes) {
      const following=next.seasons.find(s=>s.season_number>=current.season_number && s.episodes.some(ep=>!ep.watched));
      selected=(following ?? next.seasons.filter(s=>s.episodes.length).at(-1))?.season_number;
    } else {
      selected=current.season_number;
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
      if (button.dataset.seriesMode===mode) return;
      if (mode==='season') {
        for (const [number,row] of renderedRows) {
          const item=seasons().find(s=>s.season_number===number);
          if (item) updateRowFields(row,item,true);
        }
      }
      choseMode=true;
      mode=button.dataset.seriesMode;
      render();
    } else if (button===watch) {
      const current=season();
      if (current) void markWatched(current,'episode');
    } else if (button.hasAttribute('data-episode-watch')) {
      const current=season();
      const id=Number(button.closest<HTMLElement>('[data-episode-id]')?.dataset.episodeId);
      const episode=current?.episodes.find(item=>item.id===id);
      if (current && episode) void markWatched(current,'episode',episode);
    } else if (button.hasAttribute('data-season-watch')) {
      const current=fromRow(button);
      if (current) void markWatched(current,'through_season');
    } else if (button.hasAttribute('data-season-toggle')) {
      const row=button.closest<HTMLElement>('[data-season-number]')!;
      setRowOpen(row,button.getAttribute('aria-expanded')!=='true');
    } else if (button===expand) {
      if (mode==='episode') {
        allEpisodes=!allEpisodes;
        if (!allEpisodes) selectCurrent();
      } else {
        allSeasons=!allSeasons;
        if (!allSeasons) selectCurrent();
      }
      render();
      if (!reduced() && mode==='season') {
        viewMotion?.cancel();
        viewMotion=animate(list,{opacity:[.8,1]},{duration:.16,ease:'easeOut'});
      }
    } else if (button.dataset.seriesStep) {
      const input=button.closest('.score-stepper')?.querySelector<HTMLInputElement>('[data-season-count]');
      if (input) step(input,Number(button.dataset.seriesStep));
    } else if (button.hasAttribute('data-series-retry')) {
      loadFailed=false;
      render();
      void read();
    }
  },{signal:page});
  panel.addEventListener('keydown',event=>{
    const node=event.target as HTMLInputElement;
    if (!node.hasAttribute('data-season-count') || !['ArrowUp','ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    step(node,event.key==='ArrowUp'?1:-1);
  },{signal:page});
  const captureDraft = (event:Event) => {
    const node=event.target as HTMLInputElement;
    const s=fromRow(node);
    if (!s || !(node instanceof HTMLInputElement) || !node.validity.valid) return;
    if (node.hasAttribute('data-season-count') && node.value!=='') {
      setCount(s,Number(node.value));
      if (event.type==='change') paintList();
    } else if (node.name==='series_score') {
      membershipDraft(s).score=Number(node.value)||null;
    } else {
      const iso=node.closest('[data-calendar-input]')?.querySelector<HTMLInputElement>('[data-date-iso]');
      if (iso?.name==='series_start') membershipDraft(s).start_date=iso.value||null;
      if (iso?.name==='series_finish') membershipDraft(s).completion_date=iso.value||null;
    }
  };
  panel.addEventListener('input',captureDraft,{signal:page});
  panel.addEventListener('change',captureDraft,{signal:page});
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
    viewMotion?.cancel();
    resetImage();
    drafts.clear();
    stagedPosition=undefined;
    summary=undefined;
    selected=undefined;
    allSeasons=false;
    allEpisodes=false;
    episodeListSeason=undefined;
    shownView=undefined;
    title=null;
    choseMode=false;
    busy=false;
    loadFailed=false;
    cardKey='';
    request=undefined;
    generation++;
    list.hidden=true;
    rows.replaceChildren();
    episodeList.replaceChildren();
    renderedRows.clear();
    episodeRows.clear();
    rowControls.clear();
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
