export type DomainState='unknown'|'available'|'empty';
type Domains=Record<'movie'|'series'|'game',DomainState>;
type PersonRole='actor'|'staff';
export interface ContributorAvailability {
  key:string;domains?:Domains;roles?:Record<PersonRole,Domains>;pending:boolean;retry_after?:number;
}

export function disabledContributorOption(name:string,value:string,domains:Domains):boolean {
  if(name==='domain')return value==='game'?domains.game==='empty':domains.movie==='empty'&&domains.series==='empty';
  return value==='all'?domains.movie==='empty'&&domains.series==='empty'
    :value==='movie'||value==='series'?domains[value]==='empty':false;
}

const personRoles=['actor','staff'] as const;
const domainNames=['movie','series','game'] as const;
const validDomains=(domains?:Domains):domains is Domains=>!!domains&&domainNames.every(
  name=>['unknown','available','empty'].includes(domains[name]));

export function contributorRoleState(domains:Domains, games:boolean):DomainState {
  if(games)return domains.game;
  if(domains.movie==='available'||domains.series==='available')return 'available';
  return domains.movie==='empty'&&domains.series==='empty'?'empty':'unknown';
}

/** Verify sibling options after paint without replacing works, focus or scroll. */
export function contributorAvailability(endpoint:string,key:string,signal:AbortSignal,paint:(value:ContributorAvailability)=>void) {
  if(!/^contributors\/(person|actor|staff|studio)\/(catalogue|tmdb)(:|%3A)[1-9][0-9]*$/i.test(endpoint))return;
  const person=endpoint.startsWith('contributors/person/');
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
      if(signal.aborted||next.key!==key)return;
      const choices=person?personRoles.map(role=>next.roles?.[role]):[next.domains];
      if(!choices.every(validDomains))return;
      paint(next);
      if(next.pending&&choices.some(domains=>domainNames.some(name=>domains[name]==='unknown'))&&++attempts<20)
        timer=setTimeout(()=>void poll(),Math.max(3000,Math.min(60000,(next.retry_after||3)*1000)));
    }catch{/* Unknown/failed evidence must leave options usable. */}
  };
  if(!signal.aborted)timer=setTimeout(()=>void poll(),0);
}
