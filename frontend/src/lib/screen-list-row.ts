import type {EditorTitle} from './editor-store';
import {entryId,type ListState} from './profile-list-index';
import {applyResponsiveArtwork} from './responsive-artwork';
import {releaseAnnotation} from './release-annotation';
import {compactListProgress,scoreLabel} from './tracker';
import {setFeedbackText} from './state-feedback';

const coverOptions={size:'w342',sizes:'(max-width:650px) 32px, 42px',route:'direct' as const};

export function canIncrementScreenList(entry:ListState){
  if(entry.type==='movie')return entry.progress<1&&(!entry.release_date||entry.release_date.slice(0,10)<=new Date().toISOString().slice(0,10));
  if(entry.completed_seasons_only&&entry.entry?.status!=='completed')return false;
  if(entry.list_season)return entry.list_season.can_increment??(entry.released_episodes!=null&&entry.entry?.progress<entry.released_episodes);
  return entry.released_episodes!=null&&entry.progress<entry.released_episodes;
}

export function screenListProgress(state:ListState,mobile=false) {
  if(state.list_season)return state.list_season.total?`${state.progress} / ${state.list_season.total}`:String(state.progress);
  if(mobile)return compactListProgress(state as Parameters<typeof compactListProgress>[0]);
  return String(state.type==='movie'||state.released_episodes===undefined?state.progress??0:state.season_position||'—');
}

export function screenListEditorTitle(state:ListState):EditorTitle{
  const {id,type,title,original_title,tagline,poster,backdrop,backdrop_fallbacks,background_status,year,genres,release_date,progress_positions,editor_library,favorite}=state;
  const entry=state.listed===false?null:{...state.entry, status:state.status,progress:state.progress,
    released_episodes:state.released_episodes,season_position:state.season_position,score:state.score,
    rating_mode:state.rating_mode,updated_at:state.updated_at,availability_dot:state.availability_dot,
    availability_reason:state.availability_reason,new_seasons:state.new_seasons,new_episodes:state.new_episodes};
  if(entry&&type==='series'&&state.entry)Object.assign(entry,{
    status:state.entry.status,progress:state.entry.progress,season_position:state.entry.season_position,score:state.entry.score,
  });
  return {id,type,title:state.series_title??title,original_title,tagline,poster:state.series_poster??poster,
    backdrop,backdrop_fallbacks,background_status,year,genres,release_date,progress_positions,editor_library,favorite,entry};
}

export function paintScreenListRow(container:HTMLElement,state:ListState,template:HTMLTemplateElement,acknowledge=false){
  const {id,type,title}=state;
  const season=state.list_season;
  const expandable=type==='series'&&!season;
  container.dataset.listEntry=String(entryId(state));
  container.toggleAttribute('data-completed-seasons-only',state.completed_seasons_only===true);
  container.dataset.detachedSeason=String(state.detached_season??'');
  const row=container.firstElementChild as HTMLElement;
  Object.assign(row.dataset,{title:title.toLowerCase(),score:String(state.score??-1),
    progress:String(state.progress??0),mediaType:type,updated:state.updated_at||''});
  const toggle=row.querySelector<HTMLButtonElement>('[data-season-toggle]');
  if(toggle){toggle.hidden=!expandable;toggle.setAttribute('aria-label',`${title} seasons`);}
  row.querySelector<HTMLAnchorElement>('.entry-title-link')!.href=`/title/${id}`;
  row.querySelectorAll<HTMLElement>('[data-list-title-label]').forEach(label=>{if(label.textContent!==title)label.textContent=title;});
  row.querySelectorAll<HTMLElement>('[data-list-favorite]').forEach(heart=>heart.hidden=!state.favorite);
  const cover=row.querySelector<HTMLElement>('.entry-thumbnail')!;
  const path=state.poster||'';
  if(cover.dataset.artworkPath!==path){
    let image=cover.querySelector<HTMLImageElement>('img.entry-cover');
    if(path){
      if(!image){image=document.createElement('img');image.className='entry-cover';image.loading='lazy';image.alt='';cover.querySelector('.entry-cover')!.replaceWith(image);}
      applyResponsiveArtwork(image,path,coverOptions);
    }else if(image){const empty=document.createElement('span');empty.className='entry-cover';image.replaceWith(empty);}
    cover.dataset.artworkPath=cover.dataset.previewSrc=path;
  }
  const dot=cover.querySelector<HTMLElement>('.entry-availability')!;
  const availability=state.availability_reason==='airing'?'Currently airing':'New season available';
  dot.hidden=state.availability_dot!==true;dot.title=availability;dot.setAttribute('aria-label',availability);
  const edit=cover.querySelector<HTMLButtonElement>('[data-edit-entry]');
  if(edit){
    edit.dataset.editEntry=String(id);edit.setAttribute('aria-label',`Edit ${title}`);
    if(season)edit.dataset.editorSeason=String(season.number);else delete edit.dataset.editorSeason;
  }
  const score=row.querySelector('[data-score-label]')||row.querySelector('.entry-score');
  setFeedbackText(score,scoreLabel(state.score),acknowledge);
  const rate=row.querySelector<HTMLButtonElement>('[data-quick-rate-id]');
  if(rate){
    Object.assign(rate.dataset,{quickRateId:String(id),quickRateTitle:state.series_title??title,
      quickRatePoster:cover.querySelector<HTMLImageElement>('img')?.currentSrc||state.poster||'',
      quickRateScore:String(state.score??''),quickRateMode:state.rating_mode||'manual'});
    if(season){rate.dataset.quickRateSeason=String(season.number);rate.dataset.quickRateOrder=season.order;}
    else{delete rate.dataset.quickRateSeason;delete rate.dataset.quickRateOrder;}
    rate.setAttribute('aria-label',`Rate ${title}`);
  }
  row.querySelector('[data-progress-label]')!.textContent=screenListProgress(state);
  row.querySelector('[data-mobile-progress-label]')!.textContent=screenListProgress(state,true);
  const increment=row.querySelector<HTMLButtonElement>('[data-progress-increment]');
  if(increment){
    increment.dataset.progressIncrement=String(id);increment.dataset.nextProgress=String(type==='movie'?1:(state.progress??0)+1);
    increment.hidden=increment.disabled=!canIncrementScreenList(state);
    increment.setAttribute('aria-label',type==='movie'?`Mark ${title} watched`:`Mark the next episode of ${title} watched`);
    increment.title=type==='movie'?'Mark movie watched':'Mark next episode watched';
  }
  const typeLabel=row.querySelector('.entry-type');if(typeLabel)typeLabel.textContent=type==='movie'?'Movie':'Series';
  paintAnnotations(row,state,template);
}

function paintAnnotations(row:HTMLElement,state:ListState,template:HTMLTemplateElement){
  const release=state.status==='completed'?releaseAnnotation(state.new_seasons,state.new_episodes):null;
  const rewatch=state.rewatch_count>0?`${state.rewatch_count} ${state.rewatch_count===1?'rewatch':'rewatches'}`:null;
  const note=state.notes?.trim()?state.notes:null;
  let annotations=row.querySelector<HTMLElement>('.entry-annotations');
  if(!release&&!rewatch&&!note){annotations?.remove();return;}
  if(!annotations){
    annotations=document.createElement('span');annotations.className='entry-annotations';
    row.querySelector('.entry-title')!.append(annotations);
  }
  for(const [selector,value] of [['.unwatched-indicator',release],['.entry-rewatch',rewatch],['.entry-note',note]]){
    let node=annotations.querySelector<HTMLElement>(selector);
    if(!value){node?.remove();continue;}
    if(!node){node=template.content.querySelector<HTMLElement>(selector)!.cloneNode(true) as HTMLElement;annotations.append(node);}
    if(selector==='.unwatched-indicator')node.textContent=value;
    else if(selector==='.entry-rewatch'){node.setAttribute('aria-label',value);node.dataset.rewatchLabel=value;}
    else{node.setAttribute('aria-label',`Note: ${value}`);node.querySelector('.entry-note-tooltip')!.textContent=value;}
  }
}
