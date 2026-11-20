import {detailHints,detailDestination,fillDetailLoading} from './detail-loading.ts';

export function mountDetailResolution(host:HTMLElement,resolve:(signal:AbortSignal)=>Promise<string>,navigate:(url:string)=>Promise<unknown>,signal:AbortSignal){
  const source=new URL(location.href);
  detailHints.scope(document.body.dataset.cacheUser||'anonymous');
  const hint=detailHints.get(source)||{kind:detailDestination(source)?.kind||'unknown'};
  const frame=host.querySelector<HTMLElement>('[data-detail-loading]')!;
  if(!frame.dataset.loadingSince)fillDetailLoading(frame,hint);
  if(hint.title)document.title=`${hint.title} · AnyList`;
  const message=frame.querySelector<HTMLElement>('[data-loading-status]')!,retry=frame.querySelector<HTMLButtonElement>('[data-loading-retry]')!;
  let reading=false;
  const load=async()=>{
    if(reading||signal.aborted)return;reading=true;
    delete frame.dataset.loadingError;retry.hidden=true;message.textContent='Loading details…';
    document.dispatchEvent(new Event('anylist:route-progress-start'));
    try{
      const target=new URL(await resolve(signal),location.origin);
      if(signal.aborted)return;
      detailHints.set(target,hint);
      frame.dataset.loadingDestination=target.href;
      await navigate(target.pathname+target.search);
    }catch(error){
      if(signal.aborted)return;
      frame.dataset.loadingError='';message.textContent=(error as Error).message;retry.hidden=false;
      document.dispatchEvent(new Event('anylist:route-progress-end'));
    }finally{delete frame.dataset.loadingDestination;reading=false;}
  };
  retry.addEventListener('click',()=>void load(),{signal});
  void load();
}
