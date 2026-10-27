import {detailBackground,type DetailLoadState} from './detail-background';
import {publishGamePrimary} from './game-primary';
export function mountGameMetadata(root:HTMLElement,id:number,signal:AbortSignal) {
  const section=root.querySelector<HTMLElement>('[data-game-metadata]');
  if(!section||section.dataset.gameMetadataReady==='true'&&section.dataset.gameMetadataPending!=='true')return;
  const initiallyReady=section.dataset.gameMetadataReady==='true',revision=section.dataset.gameMetadataRevision||null;
  const message=section.querySelector<HTMLElement>('[data-game-metadata-message]')!;
  const retry=section.querySelector<HTMLButtonElement>('[data-game-metadata-retry]')!;
  const resource=detailBackground<DetailLoadState&{primary_ready:boolean;primary_revision?:string|null}>(`/api/proxy/tracking/games/works/${id}/metadata`,signal,async state=>{
    message.textContent=state.error?'Some game details are unavailable.':'Loading game details…';
    retry.hidden=!state.error;
    if(!state.primary_ready)return state;
    if(initiallyReady&&state.primary_revision===revision){
      if(state.pending)return state;
      section.hidden=true;return {state:'ready',pending:false};
    }
    const response=await fetch(`/api/proxy/tracking/games/works/${id}/primary`,{signal,cache:'no-store'});
    if(!response.ok)throw new Error('Some game details are unavailable.');
    if(!publishGamePrimary(root,await response.json(),id,signal))throw new Error('Game metadata identity changed.');
    section.hidden=true;return {state:'ready',pending:false};
  },state=>{if(state==='retry'){message.textContent='Some game details are unavailable.';retry.hidden=false;}});
  retry.addEventListener('click',()=>void resource.retry(),{signal});
  resource.start();
}
