/** Page-local private reads: share a warming tab with its click, never persist it. */
export function contributorPages<T>(read:(path:string,signal:AbortSignal)=>Promise<T>,signal:AbortSignal) {
  type Page={promise:Promise<T>;controller:AbortController;consumers:Set<symbol>;background:boolean;settled:boolean};
  const pages=new Map<string,Page>();
  const forget=(test:(path:string)=>boolean)=>{
    for(const [path,page] of pages)if(test(path)){pages.delete(path);page.controller.abort();}
  };
  const clear=()=>forget(()=>true);
  signal.addEventListener('abort',clear,{once:true});
  return {
    clear,
    seed(path:string,value:T){pages.set(path,{promise:Promise.resolve(value),controller:new AbortController(),consumers:new Set(),background:false,settled:true});},
    forget,
    get(path:string,consumer=signal):Promise<T>{
      if(signal.aborted||consumer.aborted)return Promise.reject(new DOMException('Aborted','AbortError'));
      let page=pages.get(path);
      if(!page){
        if(pages.size>=6){const oldest=pages.keys().next().value!;forget(path=>path===oldest);}
        const entry:Page={promise:Promise.resolve(undefined as T),controller:new AbortController(),consumers:new Set(),background:false,settled:false};
        entry.promise=read(path,entry.controller.signal).then(value=>{
          if(entry.controller.signal.aborted)throw new DOMException('Aborted','AbortError');
          entry.settled=true;return value;
        }).catch(error=>{if(pages.get(path)===entry)pages.delete(path);throw error;});
        pages.set(path,entry);page=entry;
      }
      const current=page;
      // Intent warming owns its read until invalidation, eviction or navigation.
      if(consumer===signal){current.background=true;return current.promise;}
      return new Promise<T>((resolve,reject)=>{
        const id=Symbol();current.consumers.add(id);
        const cleanup=()=>{consumer.removeEventListener('abort',abort);current.consumers.delete(id);};
        const abort=()=>{
          cleanup();reject(new DOMException('Aborted','AbortError'));
          if(!current.settled&&!current.background&&!current.consumers.size){
            if(pages.get(path)===current)pages.delete(path);
            current.controller.abort();
          }
        };
        consumer.addEventListener('abort',abort,{once:true});
        void current.promise.then(value=>{cleanup();if(!consumer.aborted)resolve(value);},error=>{cleanup();if(!consumer.aborted)reject(error);});
      });
    },
  };
}
