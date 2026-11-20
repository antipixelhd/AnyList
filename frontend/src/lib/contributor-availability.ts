export type DomainState='unknown'|'available'|'empty';
export interface ContributorAvailability {
  key:string;domains:{movie:DomainState;series:DomainState;game:DomainState};pending:boolean;retry_after?:number;
}

export function disabledContributorOption(name:string,value:string,domains:ContributorAvailability['domains']):boolean {
  if(name==='domain')return value==='game'?domains.game==='empty':domains.movie==='empty'&&domains.series==='empty';
  return value==='all'?domains.movie==='empty'&&domains.series==='empty'
    :value==='movie'||value==='series'?domains[value]==='empty':false;
}

/** Verify sibling options after paint without replacing works, focus or scroll. */
export function contributorAvailability(endpoint:string,key:string,signal:AbortSignal,paint:(value:ContributorAvailability['domains'])=>void) {
  if(!/^contributors\/(actor|staff|studio)\/(catalogue|tmdb)(:|%3A)[1-9][0-9]*$/i.test(endpoint))return;
  let timer:ReturnType<typeof setTimeout>|undefined,request:AbortController|undefined,attempts=0;
  const stop=()=>{if(timer!==undefined)clearTimeout(timer);timer=undefined;request?.abort();};
  signal.addEventListener('abort',stop,{once:true});
  const poll=async()=>{
    timer=undefined;
    if(signal.aborted)return;
    request=new AbortController();
    try{
      const response=await fetch(`/api/proxy/tracking/${endpoint}/availability`,{signal:request.signal,cache:'no-store'});
      if(!response.ok)return;
      const next:ContributorAvailability=await response.json();
      if(signal.aborted||next.key!==key||!next.domains||!['movie','series','game'].every(
        name=>['unknown','available','empty'].includes(next.domains[name as keyof typeof next.domains])))return;
      paint(next.domains);
      if(next.pending&&Object.values(next.domains).includes('unknown')&&++attempts<20)
        timer=setTimeout(()=>void poll(),Math.max(3000,Math.min(60000,(next.retry_after||3)*1000)));
    }catch{/* Unknown/failed evidence must leave options usable. */}
  };
  if(!signal.aborted)timer=setTimeout(()=>void poll(),0);
}
