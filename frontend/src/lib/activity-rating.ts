import {activityRating,scoreLabel} from './tracker';

/** Delegation also covers cards inserted by lazy details tabs and refreshes. */
export function mountSeasonRatings(signal:AbortSignal){
  const close=()=>document.querySelectorAll('[data-season-rating-toggle][aria-expanded="true"]').forEach(button=>button.setAttribute('aria-expanded','false'));
  document.addEventListener('click',event=>{
    const button=(event.target as Element).closest('[data-season-breakdown="multiple"] [data-season-rating-toggle]');
    if(!button){close();return;}
    delete (button.closest('.activity-rating') as HTMLElement).dataset.ratingDismissed;
    const open=button.getAttribute('aria-expanded')!=='true';close();button.setAttribute('aria-expanded',String(open));
  },{signal});
  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape')return;
    close();document.querySelectorAll<HTMLElement>('[data-season-breakdown="multiple"]').forEach(wrapper=>wrapper.dataset.ratingDismissed='true');
  },{signal});
  document.addEventListener('pointerout',event=>{
    const wrapper=(event.target as Element).closest<HTMLElement>('[data-season-breakdown="multiple"]');
    if(wrapper&&!wrapper.contains(event.relatedTarget as Node))delete wrapper.dataset.ratingDismissed;
  },{signal});
  document.addEventListener('focusin',event=>{
    const button=(event.target as Element).closest('[data-season-breakdown="multiple"] [data-season-rating-toggle]');
    close();if(button?.matches(':focus-visible')){
      delete (button.closest('.activity-rating') as HTMLElement).dataset.ratingDismissed;
      button.setAttribute('aria-expanded','true');
    }
  },{signal});
  document.addEventListener('focusout',event=>{
    const wrapper=(event.target as Element).closest('[data-season-breakdown="multiple"]');
    if(wrapper&&!wrapper.contains(event.relatedTarget as Node))close();
  },{signal});
}

/** Populate the same rating control in refreshed home cards as SSR cards. */
export function renderActivityRating(footer:HTMLElement,activity:any){
  const rating=activityRating(activity);footer.hidden=!rating;
  if(!rating)return;
  const button=footer.querySelector<HTMLButtonElement>('.score-pill')!;
  button.setAttribute('aria-label',rating.label);
  const previous=footer.querySelector<HTMLElement>('[data-rating-previous]')!;previous.hidden=rating.previous==null;
  previous.querySelector('[data-rating-previous-value]')!.textContent=scoreLabel(rating.previous);
  footer.querySelector('[data-rating-current]')!.textContent=scoreLabel(rating.current);
  const wrapper=footer.querySelector<HTMLElement>('.activity-rating')!;
  const multiple=rating.seasons.length>1;
  wrapper.dataset.seasonBreakdown=multiple?'multiple':'';button.tabIndex=multiple?0:-1;
  button.disabled=!multiple;
  if(multiple)button.setAttribute('aria-expanded','false');else button.removeAttribute('aria-expanded');
  const scores=footer.querySelector<HTMLElement>('[data-season-rating-scores]')!;scores.replaceChildren();
  for(const season of rating.seasons){
    const row=document.createElement('div'),label=document.createElement('span'),score=document.createElement('strong'),scale=document.createElement('small');
    label.textContent=`Season ${season.number}`;score.textContent=scoreLabel(season.score);scale.textContent='/10';
    score.append(scale);row.append(label,score);scores.append(row);
  }
}
