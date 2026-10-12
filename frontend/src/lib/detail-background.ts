export type DetailLoadState = {state?:string; pending?:boolean; retry_after?:number; error?:string|null};

/** Optional reads start after paint, preserve cached content, and honor server cooldowns. */
export function detailBackground<T>(path:string, signal:AbortSignal, apply:(data:T)=>DetailLoadState|void,
  status:(state:'loading'|'ready'|'retry',error?:string)=>void) {
  let request:Promise<void>|undefined,timer:ReturnType<typeof setTimeout>|undefined,attempts=0,done=false;
  const schedule=(seconds:number)=>{
    clearTimeout(timer);
    timer=setTimeout(()=>{timer=undefined;if(!signal.aborted)void load();},Math.max(1000,seconds*1000));
  };
  const load=()=>{
    if(signal.aborted||done)return Promise.resolve();
    if(request)return request;
    clearTimeout(timer);timer=undefined;status('loading');
    request=(async()=>{
      try {
        const response=await fetch(path,{signal});
        if(!response.ok){
          if(response.status===401||response.status===403){done=true;throw new Error('Sign in again to load extra details.');}
          const header=response.headers.get('Retry-After')||'';
          const retry=Number(header)||Math.max(0,(Date.parse(header)-Date.now())/1000);
          if(response.status===429&&retry>0)schedule(retry);
          throw new Error(response.status===429?'Provider limit reached. Retrying automatically.':'Could not load extra details. Retrying automatically.');
        }
        const state=apply(await response.json())||{};
        attempts=0;
        if(state.state==='deferred'||state.state==='partial'||state.pending||state.state==='pending'){
          status(state.error?'retry':'loading',state.error?'Provider data is temporarily unavailable. Retrying automatically.':undefined);
          schedule(Math.max(3,state.retry_after||10));
        }else {done=true;status('ready');}
      }catch(error){
        if(signal.aborted)return;
        status('retry',(error as Error).message);
        if(!done&&!timer)schedule(Math.min(300,5*2**Math.min(attempts++,6)));
      }finally{request=undefined;}
    })();
    return request;
  };
  signal.addEventListener('abort',()=>clearTimeout(timer),{once:true});
  return {load, retry:()=>{done=false;return load();}, start:()=>requestAnimationFrame(()=>requestAnimationFrame(()=>{
    if(!signal.aborted)schedule(.5);
  }))};
}
