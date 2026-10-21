import {createBrowseCache} from './browse-cache.ts';
export type SearchSection = 'all' | 'movie' | 'series' | 'game' | 'actor' | 'staff' | 'studio';
export type ResultSection = SearchSection;
export type ContributorScope = 'people'|'actor'|'staff'|'studio';
export type SearchRequest = {kind:'movie'|'series'|'game'|'contributors';url:string;scope?:ContributorScope};

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
    const signature=JSON.stringify([data.results,data.partial,data.verification_pending]);
    if(signature!==previous){onResult(data);previous=signature;}
    if(signal.aborted)return;
    if(request.kind!=='contributors'||!data.verification_pending||attempt===4)return;
    await pause(signal);
  }
}

/** Construct per mounted dialog/viewer; private editor state never crosses mounts. */
export function createQuickSearchReader(lifetime: AbortSignal, fetcher: typeof fetch = fetch, now = () => Date.now()) {
  const cache = createBrowseCache<any>(now, 60_000, 40);
  let generation=0;
  const clear=()=>{generation++;cache.clear();};
  lifetime.addEventListener('abort', clear, {once:true});
  return {
    clear,
    async read(request: SearchRequest, signal: AbortSignal, onResult: (data:any)=>void) {
      if (signal.aborted || lifetime.aborted) return;
      const epoch=generation;
      const cached = cache.get(request.url);
      if (cached) { onResult(cached); return; }
      await readQuickSearch(request, signal, data => {
        if (signal.aborted || lifetime.aborted) return;
        // Degraded local-only results must not conceal a recovered remote source.
        if (generation === epoch && !data.verification_pending && !data.notice && !data.partial) cache.set(request.url, data);
        onResult(data);
      }, fetcher);
    },
  };
}
export function quickSearchRequests(query:string,region?:string,showMoviesSeries=true,showGames=true): SearchRequest[] {
  const requests:SearchRequest[] = (showMoviesSeries?['movie','series'] as const:[]).map(kind=>({kind,url:`/api/proxy/tracking/catalog?${new URLSearchParams({media_type:kind,q:query,...(region?{region}:{})})}`}));
  if(showGames)requests.push({kind:'game',url:`/api/proxy/tracking/games/search?${new URLSearchParams({q:query})}`});
  for(const scope of ['people','studio'] as const)requests.push({kind:'contributors',scope,url:`/api/proxy/tracking/quick-search/contributors?${new URLSearchParams({q:query,kind:scope})}`});
  return requests;
}
export function rankCombinedMedia<T extends {search_rank?:number}>(movies:T[],series:T[]):T[] {
  return [...movies,...series].sort((left,right)=>(right.search_rank??0)-(left.search_rank??0));
}

/** Publish the title groups once both sources settle, including partial failures. */
export function createQuickTitleBatch<T>() {
  const settled = new Set<'movie'|'series'>();
  const results: Record<'movie'|'series', T[]> = {movie: [], series: []};
  return (kind: 'movie'|'series', rows: T[] = []) => {
    if (settled.has(kind)) return;
    settled.add(kind);
    results[kind] = rows;
    return settled.size === 2 ? results : undefined;
  };
}
export function mergeContributorResults<T>(current:Record<'actor'|'staff'|'studio',T[]>, incoming:Partial<Record<'actor'|'staff'|'studio',T[]>>, scope?:ContributorScope) {
  return {actor:scope==='studio'||scope==='staff'?current.actor:incoming.actor||[],
    staff:scope==='studio'||scope==='actor'?current.staff:incoming.staff||[],
    studio:scope==='people'||scope==='actor'||scope==='staff'?current.studio:incoming.studio||[]};
}
type SectionCounts=Partial<Record<ResultSection,number>>;
export function visibleQuickSearchSections(mobile:boolean, selected:SearchSection, combined:boolean, counts:SectionCounts,showMoviesSeries=true,showGames=true): ResultSection[] {
  const order=quickSearchTabOrder(combined,showMoviesSeries,showGames);
  const sections:ResultSection[] = mobile ? [resolvedQuickSearchTab(selected,order)] : order;
  return sections.filter(section=>(counts[section]||0)>0);
}

function quickSearchTabOrder(combined:boolean,showMoviesSeries=true,showGames=true):SearchSection[] {
  return [...(showMoviesSeries?(combined ? ['all' as const] : ['movie' as const,'series' as const]):[]),...(showGames?['game' as const]:[]),'actor','staff','studio'];
}
export function availableQuickSearchTabs(counts:SectionCounts, searching:boolean, mediaPending=false, combined=true,showMoviesSeries=true,showGames=true,gamePending=false):SearchSection[] {
  const order=quickSearchTabOrder(combined,showMoviesSeries,showGames);
  return searching?order.filter(kind=>(counts[kind]||0)>0||(['all','movie','series'].includes(kind)&&mediaPending)||(kind==='game'&&gamePending)):order;
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
