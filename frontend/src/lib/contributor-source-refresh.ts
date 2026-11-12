export interface GamePersonStatus {
  source?:'igdb-company';
  works_current?:number;works_total?:number|null;works_pending?:boolean;characters_pending?:boolean;
  id:string; pending:boolean; complete:boolean; identity_pending?:boolean; retry_after?:number; error?:string|null; fetched_at?:string|null; revision?:string|null;
  roles?:{id:number;roles:string[];characters:string[];characters_pending?:boolean}[];
}

// Report each public revision. The page reconciles its mounted window while
// preserving editor writes, focus and the visible scroll anchor.
export function contributorSourceRefresh(signal:AbortSignal, ready:(available:boolean)=>void,
  captions?:{kind:'actor'|'staff';ids:()=>number[];apply:(rows:NonNullable<GamePersonStatus['roles']>)=>void},
  progress?:(state:GamePersonStatus|null)=>void) {
  let generation=0,request:AbortController|undefined,timer:ReturnType<typeof setTimeout>|undefined;
  const clear=()=>{if(timer!==undefined)clearTimeout(timer);timer=undefined;request?.abort();request=undefined;};
  const stop=()=>{++generation;clear();ready(false);};
  signal.addEventListener('abort',stop,{once:true});
  return {update(initial?:GamePersonStatus|null){
    stop();
    progress?.(initial||null);
    const company=initial?.source==='igdb-company';
    if(signal.aborted||!initial||!(initial.pending||initial.identity_pending||initial.characters_pending)||!(company?/^[1-9][0-9]*$/:/^nm[0-9]+$/).test(initial.id))return;
    const current=generation;
    let failures=0,captionRevision:string|null|undefined,captionPending=!!initial.characters_pending;
    let observed=initial;
    const retryable=(error?:string|null)=>!error||['rate_limited','timeout','unavailable','network_error','incomplete_cache','stale_mapping','retry_pending','refreshing'].includes(error);
    const schedule=(delay:number)=>{timer=setTimeout(()=>void poll(),Math.max(3000,Math.min(3600000,delay*1000)));};
    const poll=async()=>{
      if(signal.aborted||generation!==current)return;
      request=new AbortController();
      try{
        const path=company?`companies/${initial.id}/works/status`:`people/${initial.id}/imdb`;
        const ids=[...new Set(captions?.ids()||[])];
        const query=new URLSearchParams();
        if(!company&&captions){
          query.set('kind',captions.kind);
          ids.slice(0,100).forEach(id=>query.append('work_id',String(id)));
        }
        const response=await fetch(`/api/proxy/tracking/games/${path}${query.size?`?${query}`:''}`,{signal:request.signal,cache:'no-store'});
        if(!response.ok)throw new Error('Status unavailable');
        const next:GamePersonStatus=await response.json();
        if(signal.aborted||generation!==current||next.id!==initial.id)return;
        progress?.(next);
        if(next.roles)captions?.apply(next.roles);
        // A display budget is not a collection limit. Refresh further mounted
        // cards sequentially only when public evidence changes, with the same
        // route-generation cancellation and at most one request in flight.
        if(!company&&captions&&(captionPending||next.characters_pending||next.revision!==captionRevision)){
          let pending=!!next.characters_pending;
          for(let offset=100;offset<ids.length;offset+=100){
            if(signal.aborted||generation!==current)return;
            const batch=new URLSearchParams({kind:captions.kind});
            ids.slice(offset,offset+100).forEach(id=>batch.append('work_id',String(id)));
            const response=await fetch(`/api/proxy/tracking/games/${path}?${batch}`,{signal:request.signal,cache:'no-store'});
            if(!response.ok)throw new Error('Caption status unavailable');
            const data:GamePersonStatus=await response.json();
            if(signal.aborted||generation!==current||data.id!==initial.id)return;
            if(data.roles)captions.apply(data.roles);
            pending=pending||!!data.characters_pending;
          }
          captionRevision=next.revision;captionPending=pending;next.characters_pending=pending;
        }
        failures=0;
        // A usable projection can grow before every remote identity has been
        // attempted. Offer it without waiting for a rate-limited tail, and
        // never replace the user's scrolled list or open editor automatically.
        if(next.fetched_at&&next.fetched_at!==observed.fetched_at||next.revision&&next.revision!==observed.revision){
          observed=next;ready(true);
        }
        if(!next.pending&&!next.identity_pending&&!next.characters_pending&&next.complete)return;
        if((next.pending||next.identity_pending||next.characters_pending)&&retryable(next.error))schedule(next.retry_after||3);
      }catch{
        // A healthy cold portfolio can legitimately take over twenty polls.
        // Bound consecutive transport failures, rather than successful reads;
        // navigation still owns cancellation and provider cooldowns still win.
        if(!signal.aborted&&generation===current&&++failures<=3)schedule(3*failures);
      }
    };
    if(retryable(initial.error))schedule(initial.retry_after||3);
  },stop};
}
