export type BrowseTab='movie'|'series'|'game';

const commonFields = ['q', 'view'];
const screenFields = ['genres', 'start', 'end', 'provider', 'region', 'min_score'];
const sharedGenres = new Set(['16','35','80','99','18','10751','9648','37']);
const movieGenres = new Set(['28','12','14','36','27','10402','10749','878','10770','53','10752']);
const seriesGenres = new Set(['10759','10762','10763','10764','10765','10766','10767','10768']);
const toSeries: Record<string,string> = {'28':'10759','12':'10759','14':'10765','878':'10765','10752':'10768'};
const toMovie: Record<string,string[]> = {'10759':['28','12'],'10765':['878','14'],'10768':['10752']};

function screenGenres(value: string, tab: 'movie'|'series') {
  const ids = value.split(',').filter(Boolean).flatMap(id => {
    if (id.startsWith('name:') || sharedGenres.has(id) || (tab==='movie'?movieGenres:seriesGenres).has(id)) return [id];
    return tab==='series' ? (toSeries[id] ? [toSeries[id]] : []) : toMovie[id] || [];
  });
  return [...new Set(ids)].slice(0,12).join(',');
}

/** One search/layout across all media, and compatible facets across screen media. */
export function createBrowseTabContext(region = '') {
  const common = new URLSearchParams(), screen = new URLSearchParams();
  if (region) screen.set('region', region);
  let moreOpen: boolean | undefined;
  const copy = (names: string[], input: URLSearchParams, output: URLSearchParams) => {
    for (const name of names) output.set(name, input.get(name) || '');
  };
  return {
    capture(tab: BrowseTab, values: URLSearchParams, open?: boolean) {
      copy(commonFields, values, common);
      common.set('q',values.get('q')?.trim() || '');
      if (tab==='game') return;
      const previous = screen.get('genres') || '';
      copy(screenFields, values, screen);
      const selected = (screen.get('genres') || '').split(',').filter(Boolean);
      // Keep original IDs for carried selections, and keep genres that only
      // exist on the other tab available when returning there.
      const retained = previous.split(',').filter(id => id &&
        screenGenres(id,tab).split(',').filter(Boolean).every(part => selected.includes(part)));
      const projected = new Set(screenGenres(retained.join(','),tab).split(','));
      screen.set('genres',[...retained,...selected.filter(id=>!projected.has(id))].join(','));
      moreOpen = open;
    },
    forTab(tab: BrowseTab) {
      const values = new URLSearchParams(common);
      if (tab!=='game') {
        for (const [name,value] of screen) values.set(name,value);
        if (values.has('genres')) values.set('genres',screenGenres(values.get('genres') || '',tab));
      }
      return {values, moreOpen:tab==='game'?undefined:moreOpen};
    },
  };
}

export function browseTabHref(tab: BrowseTab, values: URLSearchParams, current?: string) {
  const old = new URLSearchParams(current?.split('?').slice(1).join('?'));
  old.delete('static');
  for (const [name,value] of values) {
    if (value && !(name==='view' && value==='grid') && !(name==='min_score' && value==='0')) old.set(name,value);
    else old.delete(name);
  }
  if (tab==='series') old.set('type','series');
  return `${tab==='game'?'/browse/games':'/browse'}${old.size?'?'+old:''}`;
}

export type ApplyBrowseTabContext = (values: URLSearchParams, moreOpen?: boolean) => void;

export function browseTab(url:URL):BrowseTab|undefined {
  const path=url.pathname.replace(/\/$/,'');
  if(path==='/browse/games')return 'game';
  if(path!=='/browse')return;
  return url.searchParams.get('type')==='series'||url.searchParams.get('media_type')==='series'?'series':'movie';
}

/** One owned load per tab; switching subscribers never cancels useful warming. */
export function createBrowseTabs<T>(load:(tab:BrowseTab,signal:AbortSignal)=>Promise<T>,signal:AbortSignal){
  const values=new Map<BrowseTab,T>(),flights=new Map<BrowseTab,Promise<T>>();
  signal.addEventListener('abort',()=>{values.clear();flights.clear();},{once:true});
  return {
    set(tab:BrowseTab,value:T){if(!signal.aborted)values.set(tab,value);},
    peek(tab:BrowseTab){return values.get(tab);},
    get(tab:BrowseTab):Promise<T>{
      if(signal.aborted)return Promise.reject(new DOMException('Aborted','AbortError'));
      if(values.has(tab))return Promise.resolve(values.get(tab)!);
      const pending=flights.get(tab);if(pending)return pending;
      const task=load(tab,signal).then(value=>{
        if(signal.aborted)throw new DOMException('Aborted','AbortError');
        values.set(tab,value);return value;
      }).finally(()=>{if(flights.get(tab)===task)flights.delete(tab);});
      flights.set(tab,task);return task;
    },
  };
}

export const browsePanelActive=(root:HTMLElement)=>root.isConnected&&!root.hidden&&!root.inert&&!root.hasAttribute('data-browse-waiting');
export function setBrowsePanelUrl(root:HTMLElement,url:string){
  root.dataset.browseUrl=url;
  root.dispatchEvent(new Event('anylist:browse-url'));
  if(browsePanelActive(root))history.replaceState(history.state,'',url);
}
export function browseElement<T extends HTMLElement>(root:HTMLElement,selector:string){
  return (root.querySelector<T>(selector.replace(/#([\w-]+)/g,'[data-browse-id="$1"]'))||root.querySelector<T>(selector))!;
}

/** Retained panels coexist, so label/ARIA targets must have distinct identities. */
export function scopeBrowseIds(root:HTMLElement,tab:BrowseTab){
  const ids=new Map<string,string>();
  root.querySelectorAll<HTMLElement>('[id]').forEach(node=>{
    const original=node.dataset.browseId||node.id;
    ids.set(node.id,`browse-${tab}-${original}`);node.dataset.browseId=original;node.id=`browse-${tab}-${original}`;
  });
  root.querySelectorAll<HTMLElement>('[for],[form],[aria-controls],[aria-labelledby],[aria-describedby]').forEach(node=>{
    for(const name of ['for','form','aria-controls','aria-labelledby','aria-describedby']){
      const value=node.getAttribute(name);if(value)node.setAttribute(name,value.split(/\s+/).map(id=>ids.get(id)||id).join(' '));
    }
  });
}
