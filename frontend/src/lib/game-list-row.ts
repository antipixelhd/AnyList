import {gameListTitlePending} from './game-list-title.ts';
import {applyResponsiveArtwork} from './responsive-artwork';
import {gameProgress} from './game-progress';
import {scoreLabel} from './tracker';
import {setFeedbackText} from './state-feedback';
import type {ListState} from './profile-list-index';

/** Share the SSR row template, icons and table styling with appended rows. */
export function paintGameListRow(row:HTMLElement,state:ListState,template:HTMLTemplateElement,acknowledge=false){
  const id=state.subject.catalogue_id;
  Object.assign(row.dataset,{listEntry:String(id),entryStatus:state.status||'',title:state.title,
    score:String(state.score??-1),hours:String(state.total_seconds??-1),updated:state.updated_at||'',
    year:state.year||'',genres:JSON.stringify(state.genres||[]),favorite:String(!!state.favorite)});
  if(state.base_work_id)row.dataset.gameBase=String(state.base_work_id);else delete row.dataset.gameBase;
  const link=row.querySelector<HTMLAnchorElement>('.entry-title-link')!,heart=link.querySelector<HTMLElement>('[data-game-list-favorite]')!;
  const pendingTitle=gameListTitlePending(state),title=link.querySelector<HTMLElement>('[data-game-list-title]')!;
  title.textContent=pendingTitle?'':state.title;
  title.classList.toggle('game-title-pending',pendingTitle);title.classList.toggle('loading-surface',pendingTitle);
  if(pendingTitle){link.setAttribute('aria-busy','true');link.setAttribute('aria-label','Loading game title');}
  else {link.removeAttribute('aria-busy');link.removeAttribute('aria-label');}
  link.href=`/games/${id}`;heart.hidden=!state.favorite;
  const edit=row.querySelector<HTMLButtonElement>('[data-edit-game]');
  if(edit){edit.dataset.editGame=String(id);edit.setAttribute('aria-label',`Edit ${state.title}`);}
  const rate=row.querySelector<HTMLButtonElement>('[data-game-list-rate]');
  if(rate){rate.dataset.gameListRate=String(id);rate.setAttribute('aria-label',`Rate ${state.title}`);}
  const score=row.querySelector('[data-score-label]')||row.querySelector('.entry-score');setFeedbackText(score,scoreLabel(state.score),acknowledge);
  const progress=gameProgress(state);row.querySelector('[data-game-list-hours]')!.textContent=progress.hours||'—';
  const achievements=row.querySelector('[data-game-list-achievements]');
  if(achievements)achievements.textContent=progress.achievement||'—';
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
