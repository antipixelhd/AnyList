import {activityRating,scoreLabel} from './tracker';

/** Delegation also covers cards inserted by lazy details tabs and refreshes. */
export function mountSeasonRatings(signal:AbortSignal){
  const breakdown='[data-rating-breakdown]';
  const toggle=`${breakdown} [data-season-rating-toggle]`;
  const close=()=>document.querySelectorAll('[data-season-rating-toggle][aria-expanded="true"]').forEach(button=>button.setAttribute('aria-expanded','false'));
  document.addEventListener('click',event=>{
    const target=event.target as Element;
    const button=target.closest(toggle);
    if(!button){if(!target.closest(breakdown))close();return;}
    const wrapper=button.closest<HTMLElement>(breakdown)!;
    const open=button.getAttribute('aria-expanded')!=='true';close();button.setAttribute('aria-expanded',String(open));
    wrapper.toggleAttribute('data-rating-dismissed',!open);
  },{signal});
  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape')return;
    const target=event.target as Element;
    if(target.closest('[data-season-rating-scores]'))target.closest(breakdown)?.querySelector<HTMLElement>(toggle)?.focus();
    close();
    document.querySelectorAll(`${breakdown}:hover`).forEach(wrapper=>wrapper.setAttribute('data-rating-dismissed',''));
  },{signal});
  document.addEventListener('pointerout',event=>{
    const wrapper=(event.target as Element).closest<HTMLElement>(breakdown);
    if(wrapper&&!wrapper.contains(event.relatedTarget as Node))delete wrapper.dataset.ratingDismissed;
  },{signal});
  document.addEventListener('focusin',event=>{
    const target=event.target as Element;
    if(target.closest('[data-season-rating-scores]')) return;
    const button=target.closest(toggle);
    close();if(button?.matches(':focus-visible')){
      delete (button.closest('.activity-rating') as HTMLElement).dataset.ratingDismissed;
      button.setAttribute('aria-expanded','true');
    }
  },{signal});
  document.addEventListener('focusout',event=>{
    const wrapper=(event.target as Element).closest(breakdown);
    if(!wrapper||wrapper.contains(event.relatedTarget as Node))return;
    // A feed refresh can move a focused node and restore it in the same turn.
    queueMicrotask(()=>{
      if(!signal.aborted&&!wrapper.contains(document.activeElement))
        wrapper.querySelector(toggle)?.setAttribute('aria-expanded','false');
    });
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
  const hasBreakdown=rating.breakdown.length>0;
  wrapper.toggleAttribute('data-rating-breakdown',hasBreakdown);
  button.tabIndex=hasBreakdown?0:-1;
  button.disabled=!hasBreakdown;
  if(hasBreakdown)button.setAttribute('aria-expanded','false');else button.removeAttribute('aria-expanded');
  const scores=footer.querySelector<HTMLElement>('[data-season-rating-scores]')!;scores.replaceChildren();
  if(rating.breakdownId){
    scores.id=rating.breakdownId;button.setAttribute('aria-controls',rating.breakdownId);
  }else{
    scores.removeAttribute('id');button.removeAttribute('aria-controls');
  }
  for(const item of rating.breakdown){
    const row=document.createElement('div'),label=document.createElement('span'),score=document.createElement('strong'),scale=document.createElement('small');
    label.textContent=item.label;score.textContent=scoreLabel(item.score);scale.textContent='/10';
    score.append(scale);row.append(label,score);scores.append(row);
  }
}
