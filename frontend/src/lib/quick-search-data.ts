export type SearchSection = 'all' | 'actor' | 'staff' | 'studio';
export type ResultSection = SearchSection | 'movie' | 'series';
export type ContributorScope = 'people'|'actor'|'staff'|'studio';
export type SearchRequest = {kind:'movie'|'series'|'contributors';url:string;scope?:ContributorScope};

function verificationPause(signal:AbortSignal) {
  return new Promise<void>((resolve,reject)=>{
    if(signal.aborted){reject(signal.reason);return;}
    const aborted=()=>{clearTimeout(timer);reject(signal.reason);};
    const timer=setTimeout(()=>{signal.removeEventListener('abort',aborted);resolve();},1500);
    signal.addEventListener('abort',aborted,{once:true});
  });
}

/** Render the first response immediately; only pending contributor evidence polls.
 * Changing query/navigation cancels both the delay and the request. */
export async function readQuickSearch(request:SearchRequest,signal:AbortSignal,onResult:(data:any)=>void,
  fetcher:typeof fetch=fetch,pause=verificationPause) {
  let previous:string|undefined;
  for(let attempt=0;attempt<5;attempt++) {
    const response=await fetcher(request.url,{signal});
    if(!response.ok)throw new Error('Search unavailable');
    const data=await response.json();
    if(signal.aborted)return;
    const signature=JSON.stringify([data.results,data.partial]);
    if(signature!==previous){onResult(data);previous=signature;}
    if(signal.aborted)return;
    if(request.kind!=='contributors'||!data.verification_pending||attempt===4)return;
    await pause(signal);
  }
}
export function quickSearchRequests(query:string,region?:string): SearchRequest[] {
  const requests:SearchRequest[] = (['movie','series'] as const).map(kind=>({kind,url:`/api/proxy/tracking/catalog?${new URLSearchParams({media_type:kind,q:query,...(region?{region}:{})})}`}));
  for(const scope of ['people','studio'] as const)requests.push({kind:'contributors',scope,url:`/api/proxy/tracking/quick-search/contributors?${new URLSearchParams({q:query,kind:scope})}`});
  return requests;
}
export function rankCombinedMedia<T extends {search_rank?:number}>(movies:T[],series:T[]):T[] {
  return [...movies,...series].sort((left,right)=>(right.search_rank??0)-(left.search_rank??0));
}
export function mergeContributorResults<T>(current:Record<'actor'|'staff'|'studio',T[]>, incoming:Partial<Record<'actor'|'staff'|'studio',T[]>>, scope?:ContributorScope) {
  return {actor:scope==='studio'||scope==='staff'?current.actor:incoming.actor||[],
    staff:scope==='studio'||scope==='actor'?current.staff:incoming.staff||[],
    studio:scope==='people'||scope==='actor'||scope==='staff'?current.studio:incoming.studio||[]};
}
export function visibleQuickSearchSections(mobile:boolean, selected:SearchSection, combined:boolean, counts:Record<ResultSection,number>): ResultSection[] {
  const sections:ResultSection[] = mobile ? [selected] : [...(combined ? ['all' as const] : ['movie' as const,'series' as const]),'actor','staff','studio'];
  return sections.filter(section=>counts[section]>0);
}

const mobileTabOrder:SearchSection[]=['all','actor','staff','studio'];
export function availableQuickSearchTabs(counts:Record<ResultSection,number>, searching:boolean, mediaPending=false):SearchSection[] {
  return searching?mobileTabOrder.filter(kind=>counts[kind]>0||(kind==='all'&&mediaPending)):[...mobileTabOrder];
}
export function resolvedQuickSearchTab(selected:SearchSection, available:SearchSection[]):SearchSection {
  return available.includes(selected)?selected:available[0]??'all';
}
export function steppedQuickSearchTab(selected:SearchSection, available:SearchSection[], direction:number):SearchSection|null {
  if(available.length<2)return null;
  const index=available.indexOf(selected);
  if(index<0)return available[0];
  return available[(index+direction+available.length)%available.length];
}
