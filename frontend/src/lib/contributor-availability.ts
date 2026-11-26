export type DomainState='unknown'|'available'|'empty'|'unavailable';
export type Domains=Record<'movie'|'series'|'game',DomainState>;
type PersonRole='actor'|'staff';
export type PersonTabRoles=Record<PersonRole,Domains>;
export interface ContributorAvailability {
  key:string;domains?:Domains;roles?:PersonTabRoles;pending:boolean;retry_after?:number;
}

export function disabledContributorOption(name:string,value:string,domains:Domains):boolean {
  if(name==='domain')return value==='game'?unavailableChoice(domains.game):domains.movie==='empty'&&domains.series==='empty';
  return value==='all'?domains.movie==='empty'&&domains.series==='empty'
    :value==='movie'||value==='series'?domains[value]==='empty':false;
}

export function contributorMediaOptionDisabled(name:string,value:string,media:string,hasWorks:boolean,domains?:Domains):boolean {
  const games=media==='game';
  const showing=name==='domain'?value===(games?'game':'movies-series'):!games&&(value==='all'||value===media);
  return !!domains&&!(showing&&hasWorks)&&disabledContributorOption(name,value,domains);
}

const personRoles=['actor','staff'] as const;
const domainNames=['movie','series','game'] as const;
const validDomains=(domains?:Domains):domains is Domains=>!!domains&&domainNames.every(
  name=>['unknown','available','empty','unavailable'].includes(domains[name]));
export const unavailableChoice=(state:DomainState)=>state==='empty'||state==='unavailable';

export function contributorRoleState(domains:Domains, media:string):DomainState {
  if(media==='movie'||media==='series'||media==='game')return domains[media];
  if(domains.movie==='available'||domains.series==='available')return 'available';
  return domains.movie==='empty'&&domains.series==='empty'?'empty':'unknown';
}

export function mergeContributorDomains(previous:Domains|undefined,next:Domains):Domains {
  const updated={...next};
  for(const name of domainNames)if(updated[name]==='unknown'&&previous)updated[name]=previous[name];
  return updated;
}

export function mergePersonTabRoles(previous:PersonTabRoles|undefined,next:PersonTabRoles):PersonTabRoles {
  return {actor:mergeContributorDomains(previous?.actor,next.actor),staff:mergeContributorDomains(previous?.staff,next.staff)};
}

// Newly confirmed negatives can disable controls now and collapse their space
// on the next render. Fresh positives can restore a previously hidden choice.
function layoutDomains(domains:Domains,saved:Domains):Domains {
  const layout={...domains};
  for(const name of domainNames)if(saved[name]==='unknown'&&layout[name]==='empty')layout[name]='unknown';
  return layout;
}

export function contributorMediaSwitchVisibility(domains:Domains|undefined,media:string,hasWorks:boolean,saved=domains) {
  if(!domains||!saved)return {domain:true,screen:true};
  const own=layoutDomains(domains,saved),games=media==='game';
  const screen=contributorRoleState(own,'all'),ownDomain=games?own.game:screen;
  const singleScreen=(own.movie==='available'&&own.series==='empty')||(own.series==='available'&&own.movie==='empty');
  return {
    domain: !((hasWorks||ownDomain==='available')&&unavailableChoice(games?screen:own.game)),
    screen: !(singleScreen&&(media==='all'||media==='game'||hasWorks||(media==='movie'?own.movie:own.series)==='available')),
  };
}

export function personSwitchVisibility(roles:PersonTabRoles|undefined,role:PersonRole,media:string,hasWorks:boolean,
  saved:PersonTabRoles|undefined=roles) {
  if(!roles||!saved)return {role:true,domain:true,screen:true};
  const otherRole=role==='actor'?'staff':'actor';
  const ownDomain=contributorRoleState(layoutDomains(roles[role],saved[role]),media==='game'?'game':'all');
  const other=layoutDomains(roles[otherRole],saved[otherRole]);
  const hasOtherCredits=Object.values(roles[otherRole]).includes('available');
  return {
    role: hasOtherCredits||!((hasWorks||ownDomain==='available')&&unavailableChoice(contributorRoleState(other,media))),
    ...contributorMediaSwitchVisibility(roles[role],media,hasWorks,saved[role]),
  };
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
      if(next.pending&&choices.some(domains=>domainNames.some(name=>domains[name]==='unknown'||domains[name]==='unavailable'))&&++attempts<20)
        timer=setTimeout(()=>void poll(),Math.max(3000,Math.min(60000,(next.retry_after||3)*1000)));
    }catch{/* Unknown/failed evidence must leave options usable. */}
  };
  if(!signal.aborted)timer=setTimeout(()=>void poll(),0);
}
