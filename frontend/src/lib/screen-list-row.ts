import type {EditorTitle} from './editor-store';
import type {ListState} from './profile-list-index';
import {applyResponsiveArtwork} from './responsive-artwork';
import {releaseAnnotation} from './release-annotation';
import {compactListProgress,scoreLabel} from './tracker';

const coverOptions={size:'w342',sizes:'(max-width:650px) 32px, 42px',route:'direct' as const};

export function canIncrementScreenList(entry:ListState){
  if(entry.type==='movie')return entry.progress<1&&(!entry.release_date||entry.release_date.slice(0,10)<=new Date().toISOString().slice(0,10));
  return entry.released_episodes!=null&&entry.progress<entry.released_episodes;
}

export function screenListEditorTitle(state:ListState):EditorTitle{
  const {id,type,title,poster,backdrop,backdrop_fallbacks,background_status,year,genres,release_date,progress_positions,editor_library,favorite}=state;
  const entry=state.listed===false?null:{...state.entry, status:state.status,progress:state.progress,
    released_episodes:state.released_episodes,season_position:state.season_position,score:state.score,
    rating_mode:state.rating_mode,updated_at:state.updated_at,availability_dot:state.availability_dot,
    availability_reason:state.availability_reason,new_seasons:state.new_seasons,new_episodes:state.new_episodes};
  return {id,type,title,poster,backdrop,backdrop_fallbacks,background_status,year,genres,release_date,progress_positions,editor_library,favorite,entry};
}

export function paintScreenListRow(row:HTMLElement,state:ListState,template:HTMLTemplateElement){
  const {id,type,title}=state;
  Object.assign(row.dataset,{listEntry:String(id),title:title.toLowerCase(),score:String(state.score??-1),
    progress:String(state.progress??0),mediaType:type,updated:state.updated_at||''});
  const link=row.querySelector<HTMLAnchorElement>('.entry-title-link')!;
  const heart=link.querySelector<HTMLElement>('[data-list-favorite]')!;
  if(link.firstChild?.nodeType!==Node.TEXT_NODE||link.firstChild.textContent!==title)link.replaceChildren(document.createTextNode(title),heart);
  link.href=`/title/${id}`;heart.hidden=!state.favorite;
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
  if(edit){edit.dataset.editEntry=String(id);edit.setAttribute('aria-label',`Edit ${title}`);}
  const score=row.querySelector('[data-score-label]')||row.querySelector('.entry-score');
  if(score)score.textContent=scoreLabel(state.score);
  const rate=row.querySelector<HTMLButtonElement>('[data-quick-rate-id]');
  if(rate){
    Object.assign(rate.dataset,{quickRateId:String(id),quickRateTitle:title,
      quickRatePoster:cover.querySelector<HTMLImageElement>('img')?.currentSrc||state.poster||'',
      quickRateScore:String(state.score??''),quickRateMode:state.rating_mode||'manual'});
    rate.setAttribute('aria-label',`Rate ${title}`);
  }
  row.querySelector('[data-progress-label]')!.textContent=String(type==='movie'||state.released_episodes===undefined?state.progress??0:state.season_position||'—');
  row.querySelector('[data-mobile-progress-label]')!.textContent=compactListProgress(state as Parameters<typeof compactListProgress>[0]);
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
