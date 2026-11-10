import {applyResponsiveArtwork} from './responsive-artwork';
import {gameProgress} from './game-progress';
import {scoreLabel} from './tracker';
import type {GameListState} from './game-list-index';

/** Share the SSR row template, icons and table styling with appended rows. */
export function paintGameListRow(row:HTMLElement,state:GameListState,template:HTMLTemplateElement){
  const id=state.subject.catalogue_id;
  Object.assign(row.dataset,{gameEntry:String(id),entryStatus:state.status||'',title:state.title,
    score:String(state.score??-1),hours:String(state.total_seconds??-1),updated:state.updated_at||'',
    year:state.year||'',genres:JSON.stringify(state.genres||[]),favorite:String(!!state.favorite)});
  if(state.base_work_id)row.dataset.gameBase=String(state.base_work_id);else delete row.dataset.gameBase;
  const link=row.querySelector<HTMLAnchorElement>('.entry-title-link')!,heart=link.querySelector<HTMLElement>('[data-game-list-favorite]')!;
  if(link.firstChild?.nodeType!==Node.TEXT_NODE||link.firstChild.textContent!==state.title)link.replaceChildren(document.createTextNode(state.title),heart);
  link.href=`/games/${id}`;heart.hidden=!state.favorite;
  const edit=row.querySelector<HTMLButtonElement>('[data-edit-game]');
  if(edit){edit.dataset.editGame=String(id);edit.setAttribute('aria-label',`Edit ${state.title}`);}
  const rate=row.querySelector<HTMLButtonElement>('[data-game-list-rate]');
  if(rate){rate.dataset.gameListRate=String(id);rate.setAttribute('aria-label',`Rate ${state.title}`);}
  const score=row.querySelector('[data-score-label]')||row.querySelector('.entry-score');if(score)score.textContent=scoreLabel(state.score);
  const progress=gameProgress(state);row.querySelector('[data-game-list-hours]')!.textContent=progress.hours||'—';
  row.querySelectorAll<HTMLElement>('[data-game-list-achievements]').forEach(node=>{
    node.textContent=progress.achievement||'—';node.dataset.known=String(progress.achievement!=null);
    if(node.classList.contains('game-inline-achievements'))node.setAttribute('aria-label',`Achievements ${progress.achievement||'unknown'}`);
  });
  const holder=row.querySelector<HTMLElement>('.entry-thumbnail')!;
  let artwork=holder.querySelector<HTMLElement>('.entry-cover')!;
  if(holder.dataset.artworkPath!==(state.image_url||'')||artwork.tagName!==(state.image_url?'IMG':'SPAN')){
    if(state.image_url){
      if(artwork.tagName!=='IMG'){const image=document.createElement('img');image.className='entry-cover';image.alt='';image.loading='lazy';artwork.replaceWith(image);artwork=image;}
      applyResponsiveArtwork(artwork as HTMLImageElement,state.image_url,{size:'w185',sizes:'44px',route:'direct'});
    }else if(artwork.tagName!=='SPAN'){const empty=document.createElement('span');empty.className='entry-cover';artwork.replaceWith(empty);}
    holder.dataset.artworkPath=holder.dataset.previewSrc=state.image_url||'';
  }
  let annotations=row.querySelector<HTMLElement>('.entry-annotations');
  const hasNote=!!state.notes?.trim(),hasReplay=state.replay_count>0;
  // Absence is significant: the original mobile CSS uses :has() selectors.
  if(!hasNote&&!hasReplay){annotations?.remove();return;}
  if(!annotations){
    annotations=template.content.querySelector<HTMLElement>('.entry-annotations')!.cloneNode(true) as HTMLElement;
    row.querySelector('.entry-title')!.append(annotations);
  }
  if(annotations){
    for(const [selector,visible] of [['.entry-note',hasNote],['.entry-rewatch',hasReplay]] as const){
      const node=annotations.querySelector(selector);
      if(!visible)node?.remove();else if(!node)annotations.append(template.content.querySelector(selector)!.cloneNode(true));
    }
    const note=annotations.querySelector<HTMLElement>('.entry-note');
    if(note){note.setAttribute('aria-label',`Note: ${state.notes}`);note.querySelector('.entry-note-tooltip')!.textContent=state.notes;}
    const replay=annotations.querySelector<HTMLElement>('.entry-rewatch'),label=`${state.replay_count} ${state.replay_count===1?'replay':'replays'}`;
    if(replay){replay.dataset.rewatchLabel=label;replay.setAttribute('aria-label',label);}
  }
}

export function createGameListRow(template:HTMLTemplateElement,state:GameListState){
  const row=template.content.querySelector<HTMLElement>('[data-game-entry]')!.cloneNode(true) as HTMLElement;
  paintGameListRow(row,state,template);return row;
}
