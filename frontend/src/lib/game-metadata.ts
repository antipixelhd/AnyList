import {detailBackground,type DetailLoadState} from './detail-background';
import {publishGamePrimary} from './game-primary';
export function mountGameMetadata(root:HTMLElement,id:number,signal:AbortSignal) {
  const section=root.querySelector<HTMLElement>('[data-game-metadata]');
  if(!section||section.dataset.gameMetadataReady==='true'&&section.dataset.gameMetadataPending!=='true')return;
  const initiallyReady=section.dataset.gameMetadataReady==='true',revision=section.dataset.gameMetadataRevision||null;
  const message=section.querySelector<HTMLElement>('[data-game-metadata-message]')!;
  const retry=section.querySelector<HTMLButtonElement>('[data-game-metadata-retry]')!;
  const query=new URLSearchParams({primary:'true',...(revision?{revision}:{})});
  const resource=detailBackground<DetailLoadState&{primary_ready:boolean;primary_revision?:string|null;primary?:Record<string,any>}>(`/api/proxy/tracking/games/works/${id}/metadata?${query}`,signal,async state=>{
    message.textContent=state.error?'Some game details are unavailable.':'Loading game details…';
    retry.hidden=!state.error;
    if(!state.primary_ready){
      if(!state.pending&&state.state==='unavailable'){
        root.dataset.gamePrimaryPending='false';
        root.querySelectorAll('.detail-image-skeleton,.detail-synopsis-skeleton').forEach(node=>{
          node.classList.remove('detail-image-skeleton','detail-synopsis-skeleton');node.removeAttribute('aria-busy');
          if(node.matches('[data-biography-text]'))node.textContent='Overview is temporarily unavailable.';
        });
        root.querySelectorAll('[data-game-primary-facts]').forEach(node=>node.remove());
        root.querySelector('[data-game-genres]')?.removeAttribute('aria-busy');
        root.dispatchEvent(new CustomEvent('anylist:game-primary-unavailable'));
        message.textContent='Some game details are unavailable.';retry.hidden=false;
      }
      return state;
    }
    if(initiallyReady&&state.primary_revision===revision){
      if(state.pending)return state;
      section.hidden=true;return {state:'ready',pending:false};
    }
    if(!state.primary||!publishGamePrimary(root,state.primary,id,signal))throw new Error('Game metadata identity changed.');
    section.hidden=true;return {state:'ready',pending:false};
  },state=>{if(state==='retry'){message.textContent='Some game details are unavailable.';retry.hidden=false;}});
  retry.addEventListener('click',()=>void resource.retry(),{signal});
  resource.start();
}
