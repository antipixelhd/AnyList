export interface GamePersonStatus {
  source?:'igdb-company';
  id:string; pending:boolean; complete:boolean; retry_after?:number; error?:string|null; fetched_at?:string|null; revision?:string|null;
}

// New evidence is offered explicitly: never replace a scrolled filmography or
// reload an open editor merely because a background component became ready.
export function contributorSourceRefresh(signal:AbortSignal, ready:(available:boolean)=>void) {
  let generation=0,request:AbortController|undefined,timer:ReturnType<typeof setTimeout>|undefined;
  const clear=()=>{if(timer!==undefined)clearTimeout(timer);timer=undefined;request?.abort();request=undefined;};
  const stop=()=>{++generation;clear();ready(false);};
  signal.addEventListener('abort',stop,{once:true});
  return {update(initial?:GamePersonStatus|null){
    stop();
    const company=initial?.source==='igdb-company';
    if(signal.aborted||!initial?.pending||!(company?/^[1-9][0-9]*$/:/^nm[0-9]+$/).test(initial.id))return;
    const current=generation;
    let attempts=0;
    const retryable=(error?:string|null)=>!error||['rate_limited','timeout','unavailable','network_error','incomplete_cache','stale_mapping','retry_pending','refreshing'].includes(error);
    const schedule=(delay:number)=>{timer=setTimeout(()=>void poll(),Math.max(3000,Math.min(3600000,delay*1000)));};
    const poll=async()=>{
      if(signal.aborted||generation!==current)return;
      request=new AbortController();
      try{
        const path=company?`companies/${initial.id}/works/status`:`people/${initial.id}/imdb`;
        const response=await fetch(`/api/proxy/tracking/games/${path}`,{signal:request.signal,cache:'no-store'});
        if(!response.ok)return;
        const next:GamePersonStatus=await response.json();
        if(signal.aborted||generation!==current||next.id!==initial.id)return;
        if(!next.pending&&next.complete){ready(next.fetched_at!==initial.fetched_at||next.revision!==initial.revision);return;}
        if(next.pending&&retryable(next.error)&&++attempts<20)schedule(next.retry_after||3);
      }catch{ /* Saved works remain usable during failures or navigation. */ }
    };
    if(retryable(initial.error))schedule(initial.retry_after||3);
  },stop};
}
