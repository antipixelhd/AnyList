/** Page-local private reads: share a warming tab with its click, never persist it. */
export function contributorPages<T>(read:(path:string,signal:AbortSignal)=>Promise<T>,signal:AbortSignal) {
  const pages=new Map<string,Promise<T>>();
  const clear=()=>pages.clear();
  signal.addEventListener('abort',clear,{once:true});
  return {
    clear,
    seed(path:string,value:T){pages.set(path,Promise.resolve(value));},
    forget(test:(path:string)=>boolean){for(const path of pages.keys())if(test(path))pages.delete(path);},
    get(path:string){
      const previous=pages.get(path);if(previous)return previous;
      const pending=read(path,signal).catch(error=>{if(pages.get(path)===pending)pages.delete(path);throw error;});
      if(pages.size>=6)pages.delete(pages.keys().next().value!);
      pages.set(path,pending);return pending;
    },
  };
}
