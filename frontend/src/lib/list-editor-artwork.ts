import {editorStore,type EditorTitle} from './editor-store';

/** Admit only exposed/nearby rows, independently of the much larger DOM window. */
export function listEditorArtwork(signal:AbortSignal,titleFor:(row:HTMLElement)=>EditorTitle|undefined,active:()=>boolean){
  const waiting=new Set<HTMLElement>(),observed=new WeakSet<HTMLElement>();
  let running=0;
  const connection=(navigator as Navigator&{connection?:{saveData?:boolean;effectiveType?:string}}).connection;
  const allowed=!connection?.saveData&&!/^(slow-)?2g$/.test(connection?.effectiveType||'');
  const drain=()=>{
    while(!signal.aborted&&active()&&running<2&&waiting.size){
      const row=waiting.values().next().value!;waiting.delete(row);
      if(!row.isConnected)continue;
      const title=titleFor(row);if(!title)continue;
      running++;
      void editorStore.warmListArtwork(title,signal)
        .finally(()=>{running--;drain();});
    }
  };
  const observer=new IntersectionObserver(entries=>{
    for(const entry of entries){
      if(entry.isIntersecting)waiting.add(entry.target as HTMLElement);else waiting.delete(entry.target as HTMLElement);
    }
    drain();
  },{rootMargin:'180px'});
  signal.addEventListener('abort',()=>{observer.disconnect();waiting.clear();},{once:true});
  return {
    observe(row:HTMLElement){if(allowed&&!observed.has(row)){observed.add(row);observer.observe(row);}},
    remove(row:HTMLElement){observer.unobserve(row);observed.delete(row);waiting.delete(row);},
    resume:drain,
  };
}
