export type DetailLoadState = {state?:string; pending?:boolean; retry_after?:number; error?:string|null};

/** Optional reads start after paint, preserve cached content, and honor server cooldowns. */
export function detailBackground<T>(path:string, signal:AbortSignal, apply:(data:T)=>DetailLoadState|void|Promise<DetailLoadState|void>,
  status:(state:'loading'|'ready'|'retry',error?:string)=>void) {
  let request:Promise<void>|undefined,timer:ReturnType<typeof setTimeout>|undefined,attempts=0,done=false,recovering=false;
  const schedule=(seconds:number)=>{
    clearTimeout(timer);
    timer=setTimeout(()=>{timer=undefined;if(!signal.aborted)void load();},Math.max(1000,seconds*1000));
  };
  const load=()=>{
    if(signal.aborted||done)return Promise.resolve();
    if(request)return request;
    clearTimeout(timer);timer=undefined;if(!recovering)status('loading');
    request=(async()=>{
      try {
        const response=await fetch(path,{signal});
        if(!response.ok){
          if(response.status===401||response.status===403){done=true;throw new Error(response.status===403?'Access to these details is unavailable.':'Sign in again to load extra details.');}
          const header=response.headers.get('Retry-After')||'';
          const retry=Number(header)||Math.max(0,(Date.parse(header)-Date.now())/1000);
          if(response.status===429&&retry>0)schedule(retry);
          throw new Error(response.status===429?'Provider limit reached. Retrying automatically.':'Could not load extra details. Retrying automatically.');
        }
        const data=await response.json();
        if(signal.aborted)return;
        const state=await apply(data)||{};
        if(signal.aborted)return;
        attempts=0;
        recovering=!!state.error;
        if(state.state==='deferred'||state.state==='partial'||state.pending||state.state==='pending'){
          status(state.error?'retry':'loading',state.error?'Some details couldn’t be refreshed. We’ll retry shortly.':undefined);
          schedule(Math.max(1,state.retry_after||10));
        }else {done=true;status('ready');}
      }catch(error){
        if(signal.aborted)return;
        recovering=true;
        status('retry',(error as Error).message);
        if(!done&&!timer)schedule(Math.min(300,5*2**Math.min(attempts++,6)));
      }finally{request=undefined;}
    })();
    return request;
  };
  signal.addEventListener('abort',()=>clearTimeout(timer),{once:true});
  return {load, retry:async(afterCurrent=false)=>{if(afterCurrent&&request)await request;done=false;return load();}, start:()=>requestAnimationFrame(()=>requestAnimationFrame(()=>{
    if(!signal.aborted)void load();
  }))};
}
