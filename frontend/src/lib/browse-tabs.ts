export type BrowseTab='movie'|'series'|'game';

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
  root.querySelectorAll<HTMLElement>('[for],[aria-controls],[aria-labelledby],[aria-describedby]').forEach(node=>{
    for(const name of ['for','aria-controls','aria-labelledby','aria-describedby']){
      const value=node.getAttribute(name);if(value)node.setAttribute(name,value.split(/\s+/).map(id=>ids.get(id)||id).join(' '));
    }
  });
}
