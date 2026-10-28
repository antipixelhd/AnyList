import type {DetailLoadState} from './detail-background';

export const gameSectionPending=(state?:DetailLoadState|null,primaryPending=false)=>primaryPending||!state||
  !!state.pending||['pending','loading','partial','deferred'].includes(state.state||'');

/** Retain usable SSR content while a provider refreshes; reserve only missing content. */
export function paintGameSection(section:HTMLElement,hasContent:boolean,state?:DetailLoadState|null,primaryPending=false) {
  const loading=!hasContent&&gameSectionPending(state,primaryPending);
  section.hidden=!hasContent&&!loading;
  section.setAttribute('aria-busy',String(loading));
  section.querySelectorAll<HTMLElement>('[data-game-section-loading]').forEach(node=>node.hidden=!loading);
  section.querySelectorAll<HTMLElement>('[data-game-section-content]').forEach(node=>node.hidden=!hasContent);
  const error=section.querySelector<HTMLElement>('[data-game-section-error]');if(error)error.hidden=true;
}

export function bindGameSection(root:HTMLElement,section:HTMLElement,signal:AbortSignal,
  resource:{retry:(afterCurrent?:boolean)=>Promise<void>;start:()=>unknown},initial?:DetailLoadState|null) {
  section.querySelector('[data-game-section-retry]')?.addEventListener('click',()=>void resource.retry(),{signal});
  // A cold primary can establish Steam/IGDB identities after an earlier optional
  // read returned unavailable. Retry then, rather than stranding the empty slot.
  const needsPrimary=gameSectionPending(initial,root.dataset.gamePrimaryPending==='true')||initial?.state==='unavailable';
  root.addEventListener('anylist:game-primary',()=>{if(needsPrimary)void resource.retry(true);},{signal});
  root.addEventListener('anylist:game-primary-unavailable',()=>void resource.retry(true),{signal});
  if(gameSectionPending(initial,root.dataset.gamePrimaryPending==='true')||initial?.error)resource.start();
}

export function gameSectionFailure(section:HTMLElement,state:string,error?:string) {
  if(state!=='retry')return;
  const content=[...section.querySelectorAll<HTMLElement>('[data-game-section-content]')].some(node=>!node.hidden);
  if(content)return;
  paintGameSection(section,false,{state:'unavailable'});
  const message=section.querySelector<HTMLElement>('[data-game-section-error]');
  if(message){section.hidden=false;message.hidden=false;message.querySelector('[data-game-section-message]')!.textContent=error||'Temporarily unavailable.';}
}
