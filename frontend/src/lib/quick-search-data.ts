import {createBrowseCache} from './browse-cache.ts';
import {verifiedEmptySearch, broadSearchParams, type SearchPage} from './search-fallback.ts';
export type SearchSection = 'all' | 'movie' | 'series' | 'game' | 'people' | 'studio';
export type ContributorScope = 'people' | 'studio';
export type SearchRequest = {kind:'movie'|'series'|'game'|'contributors';url:string;scope?:ContributorScope};

function verificationPause(signal:AbortSignal, delay=1500) {
  return new Promise<void>((resolve,reject)=>{
    if(signal.aborted){reject(signal.reason);return;}
    const aborted=()=>{clearTimeout(timer);reject(signal.reason);};
    const timer=setTimeout(()=>{signal.removeEventListener('abort',aborted);resolve();},delay);
    signal.addEventListener('abort',aborted,{once:true});
  });
}

/** Render the first response immediately; only pending contributor evidence polls.
 * Changing query/navigation cancels both the delay and the request. */
export async function readQuickSearch(request:SearchRequest,signal:AbortSignal,onResult:(data:any)=>void,
  fetcher:typeof fetch=fetch,pause=verificationPause) {
  let previous:string|undefined;
  let verifying = false, retries = 0;
  while(!signal.aborted) {
    const response=await fetcher(request.url,{signal}).catch(error => {
      if (!verifying || !(error instanceof TypeError) || signal.aborted) throw error;
      return null;
    });
    if (verifying && retries < 2 && (!response || [408, 502, 503, 504].includes(response.status))) {
      await pause(signal, 1500 * 2 ** retries++);
      continue;
    }
    if(!response?.ok)throw new Error('Search unavailable');
    const data=await response.json();
    if(signal.aborted)return;
    // A healthy cold career can take several worker turns. Pending evidence
    // is not a provider failure, and must not become one after a poll count.
    retries = 0;
    const signature=JSON.stringify([data.results,data.partial,data.unavailable,data.verification_pending,data.section_pending]);
    if(signature!==previous){onResult(data);previous=signature;}
    if(signal.aborted)return;
    if(request.kind!=='contributors'||!data.verification_pending)return;
    verifying = true;
    const delay=typeof data.retry_after==='number'&&Number.isFinite(data.retry_after)
      ? Math.max(1500,Math.min(3600,data.retry_after)*1000) : 1500;
    await pause(signal,delay);
  }
}

/** Construct per mounted dialog/viewer; private editor state never crosses mounts. */
export function createQuickSearchReader(lifetime: AbortSignal, fetcher: typeof fetch = fetch, now = () => Date.now()) {
  const cache = createBrowseCache<any>(now, 60_000, 40);
  let generation=0;
  const searchId = crypto.randomUUID();
  let searchGeneration = 0, searchQuery = '';
  const subscribed = new WeakSet<AbortSignal>();
  const clear=()=>{generation++;cache.clear();};
  lifetime.addEventListener('abort', clear, {once:true});
  return {
    clear,
    async read(request: SearchRequest, signal: AbortSignal, onResult: (data:any)=>void) {
      if (signal.aborted || lifetime.aborted) return;
      const epoch=generation;
      const cached = cache.get(request.url);
      if (cached) { onResult(cached); return; }
      let activeRequest = request;
      if (request.kind === 'contributors') {
        const url = new URL(request.url, 'https://search.invalid');
        const query = JSON.stringify([url.searchParams.get('q'), url.searchParams.get('work_domain')]);
        if (query !== searchQuery || !subscribed.has(signal)) { searchQuery = query; searchGeneration++; }
        const ownGeneration = searchGeneration;
        url.searchParams.set('search_id', searchId);
        url.searchParams.set('search_generation', String(ownGeneration));
        activeRequest = {...request, url: url.pathname + url.search};
        if (!subscribed.has(signal)) {
          subscribed.add(signal);
          signal.addEventListener('abort', () => {
            // Release this subscription only. A late cancellation cannot
            // demote the next generation or another search window/viewer.
            void fetcher(`/api/proxy/tracking/quick-search/demand/${searchId}?generation=${ownGeneration}`,
              {method:'DELETE',keepalive:true}).catch(()=>{});
          }, {once:true});
        }
      }
      await readQuickSearch(activeRequest, signal, data => {
        if (signal.aborted || lifetime.aborted) return;
        // Degraded local-only results must not conceal a recovered remote source.
        if (generation === epoch && !data.verification_pending && !data.notice && !data.partial) cache.set(request.url, data);
        onResult(data);
      }, fetcher);
    },
  };
}
export function quickSearchRequests(query:string,region?:string,showMoviesSeries=true,showGames=true): SearchRequest[] {
  const requests:SearchRequest[] = (showMoviesSeries?['movie','series'] as const:[]).map(kind=>({kind,url:`/api/proxy/tracking/catalog?${new URLSearchParams({media_type:kind,q:query,scan_empty:'true',...(region?{region}:{})})}`}));
  if(showGames)requests.push({kind:'game',url:`/api/proxy/tracking/games/search?${new URLSearchParams({q:query})}`});
  const work_domain=showGames?(showMoviesSeries?'all':'game'):'movies_series';
  for(const scope of ['people','studio'] as const) {
    const params=new URLSearchParams({q:query,kind:scope,work_domain});
    if(scope==='people')params.set('group_people','true');
    requests.push({kind:'contributors',scope,url:`/api/proxy/tracking/quick-search/contributors?${params}`});
  }
  return requests;
}

/** Keep the curated pair atomic. Only two verified empty title scopes broaden. */
export async function readQuickTitles<T>(requests: SearchRequest[], signal: AbortSignal,
  read: (request: SearchRequest, signal: AbortSignal, onResult: (data: any) => void) => Promise<void>, combined = true) {
  const titles = requests.filter(request => request.kind === 'movie' || request.kind === 'series');
  async function batch(values: SearchRequest[]) {
    let failed = 0;
    const pages = await Promise.all(values.map(async request => {
      let page: SearchPage | undefined;
      try { await read(request, signal, data => { page = data; }); }
      catch (error) { if (signal.aborted) throw error; failed++; }
      signal.throwIfAborted();
      return page;
    }));
    return {pages, failed};
  }
  let result = await batch(titles);
  const expandable = result.pages.map(page => !!page && verifiedEmptySearch(page));
  const expand = titles.filter((_, index) => combined
    ? !result.failed && titles.length === 2 && expandable.every(Boolean)
    : expandable[index]);
  if (expand.length) {
    const broad = await batch(expand.map(request => {
      const [path, query] = request.url.split('?');
      return {...request, url: `${path}?${broadSearchParams(new URLSearchParams(query))}`};
    }));
    result.failed += broad.failed;
    expand.forEach((request, index) => { result.pages[titles.indexOf(request)] = broad.pages[index]; });
  }
  const incomplete = result.pages.some(page => page && (page.notice || page.verification_pending || (!page.results?.length && page.has_more)));
  const rows = {movie: [] as T[], series: [] as T[], failed: result.failed, incomplete};
  titles.forEach((request, index) => { rows[request.kind as 'movie'|'series'] = result.pages[index]?.results as T[] || []; });
  return rows;
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
export function mergeContributorResults<T>(current:Record<ContributorScope,T[]>, incoming:Partial<Record<ContributorScope,T[]>>, scope?:ContributorScope) {
  return {
    people:scope==='studio'?current.people:incoming.people||[],
    studio:scope==='people'?current.studio:incoming.studio||[],
  };
}
export function quickSearchTabOrder(combined:boolean,showMoviesSeries=true,showGames=true):SearchSection[] {
  return [...(showMoviesSeries?(combined ? ['all' as const] : ['movie' as const,'series' as const]):[]),...(showGames?['game' as const]:[]),'people','studio'];
}
export function availableQuickSearchTabs(order: SearchSection[], counts: Partial<Record<SearchSection, number>>, pending: ReadonlySet<SearchSection>): SearchSection[] {
  return order.filter(kind => (counts[kind] ?? 0) > 0 || pending.has(kind));
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
