import {detailBackground,type DetailLoadState} from './detail-background';
export function mountGameMetadata(root:HTMLElement,id:number,signal:AbortSignal) {
  const section=root.querySelector<HTMLElement>('[data-game-metadata]');
  if(!section||section.dataset.gameMetadataReady==='true')return;
  const message=section.querySelector<HTMLElement>('[data-game-metadata-message]')!;
  const retry=section.querySelector<HTMLButtonElement>('[data-game-metadata-retry]')!;
  const refresh=section.querySelector<HTMLButtonElement>('[data-game-metadata-refresh]')!;
  const resource=detailBackground<DetailLoadState&{primary_ready:boolean}>(`/api/proxy/tracking/games/works/${id}/metadata`,signal,state=>{
    message.textContent=state.primary_ready?'More details are ready.':state.error?'Some game details are unavailable.':'Loading game details…';
    refresh.hidden=!state.primary_ready;retry.hidden=state.primary_ready||!state.error;
    return state.primary_ready?{state:'ready',pending:false}:state;
  },state=>{if(state==='retry'){message.textContent='Some game details are unavailable.';retry.hidden=false;}});
  retry.addEventListener('click',()=>void resource.retry(),{signal});
  // Refresh is an explicit action; background metadata never navigates away
  // from the user's editor, pending write or note draft.
  refresh.addEventListener('click',()=>location.reload(),{signal});
  resource.start();
}
