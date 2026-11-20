type SeasonState={ready:boolean;pending:boolean;waitable?:boolean;error?:string|null;retry_after?:number};
export type SeasonResponse={id:number;type:string;season_status:SeasonState;seasons:any[];catalogue_revision?:string|null};

/** Keep page-owned reads cancellable while the server owns shared acquisition. */
export function createSeasonLoader<T extends {data:SeasonResponse}>(read:(revision?:string)=>Promise<T>,apply:(value:T)=>boolean,
  signal:AbortSignal,status:(error?:string)=>void,revision=()=>0) {
  let pending:Promise<void>|undefined,timer:ReturnType<typeof setTimeout>|undefined,done=false,failures=0;
  let applied:string|undefined;
  let retained:{result:T;version:number}|undefined;
  let catalogue:string|undefined;
  const schedule=(seconds:number,minimum=250)=>{
    clearTimeout(timer);
    timer=setTimeout(()=>{timer=undefined;void load();},Math.max(minimum,seconds*1000));
  };
  const load=():Promise<void>=>{
    if(signal.aborted||done)return Promise.resolve();
    if(pending)return pending;
    clearTimeout(timer);
    const version=revision();
    const request=(async()=>{
      try{
        const result=retained?.version===version?retained.result:await read(catalogue);
        if(signal.aborted)return;
        const state=result.data.season_status;
        if(version!==revision()){retained=undefined;schedule(.25);return;}
        const key=`${result.data.catalogue_revision}:${version}`;
        if(state.ready)catalogue=result.data.catalogue_revision||undefined;
        if(state.ready&&applied!==key){
          if(!apply(result)){retained={result,version};schedule(.25);return;}
          applied=key;
        }
        retained=undefined;
        failures=0;status(state.error?'Episode metadata is unavailable.':!state.ready&&!state.pending?'Episodes are unavailable.':undefined);
        done=state.ready&&!state.pending;
        if(!done&&state.pending){
          if(state.ready&&state.waitable&&!state.error)schedule(0,0);
          else schedule(Math.max(1,state.retry_after||2));
        }
      }catch{
        if(signal.aborted)return;
        status('Could not load episodes.');schedule(Math.min(60,2**Math.min(++failures,6)));
      }
    })();
    pending=request;
    const clear=()=>{if(pending===request)pending=undefined;};
    void request.then(clear,clear);
    return request;
  };
  signal.addEventListener('abort',()=>clearTimeout(timer),{once:true});
  return {load,retry:async()=>{await pending;done=false;applied=undefined;retained=undefined;return load();}};
}

export function mountSeriesSeasons(root:HTMLElement,id:number,signal:AbortSignal,
  apply:(data:SeasonResponse,source:Document)=>boolean,revision:()=>number) {
  const status=(error?:string)=>{
    const message=root.querySelector<HTMLElement>('[data-season-load-error]');
    if(message){message.hidden=!error;message.textContent=error||'';}
    root.querySelector<HTMLElement>('[data-season-retry]')?.toggleAttribute('hidden',!error);
  };
  const loader=createSeasonLoader(async(revision)=>{
    const response=await fetch(`/title/${id}/seasons${revision?`?${new URLSearchParams({revision})}`:''}`,{signal});
    if(!response.ok||response.redirected)throw new Error('Episodes unavailable');
    const source=new DOMParser().parseFromString(await response.text(),'text/html');
    const data=JSON.parse(source.querySelector('[data-season-payload]')?.textContent||'null') as SeasonResponse;
    if(data?.id!==id||data.type!=='series'||!data.season_status||!Array.isArray(data.seasons)||!source.querySelector('[data-season-details]'))throw new Error('Episodes unavailable');
    return {data,source};
  },({data,source})=>apply(data,source),signal,status,revision);
  root.addEventListener('click',event=>{
    if((event.target as Element).closest('[data-season-retry]'))void loader.retry();
  },{signal});
  requestAnimationFrame(()=>requestAnimationFrame(()=>{if(!signal.aborted)void loader.load();}));
  return loader;
}
