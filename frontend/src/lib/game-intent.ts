import {predictionAllowed} from './predictive-loading.ts';

/** Public jobs only: never retain private HTML, editor state or authorization. */
export function gameIntentTarget(url:URL,origin:string){
  if(url.origin!==origin||url.hash)return;
  const local=url.pathname.match(/^\/games\/([1-9]\d*)\/?$/);
  const native=url.pathname==='/discover-game'?url.searchParams.get('id'):undefined;
  const id=local?.[1]||native;
  if(!id||!/^\d+$/.test(id)||Number(id)<1||!Number.isSafeInteger(Number(id))||Number(id)>2147483647)return;
  return `${local?'work':'igdb'}/${id}`;
}

/** One outstanding admission and three distinct starts per page visit. */
export function createGameIntent(fetcher:typeof fetch,allowed=predictionAllowed){
  let active=false,starts=0;
  const seen=new Set<string>();
  return {
    reset(){starts=0;seen.clear();},
    warm(target:string){
      if(!allowed()||active||starts>=3||seen.has(target))return;
      starts++;seen.add(target);active=true;
      // Admission is short. Server-owned canonical work survives route changes.
      void fetcher(`/api/proxy/tracking/games/warm/${target}`,{method:'POST',
        credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(4000)})
        .catch(()=>undefined).finally(()=>{active=false;});
    },
  };
}
