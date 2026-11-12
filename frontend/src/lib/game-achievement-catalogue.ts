export type AchievementCatalogue={work_id:number;total:number|null;availability:string;definitions_available:boolean};

/** Upgrade optional capabilities without replacing personal state or drafts. */
export function watchAchievementCatalogue(id:number,signal:AbortSignal,apply:(data:AchievementCatalogue,status?:{pending?:boolean;error?:string;state?:string})=>void,
  primaryEvents:EventTarget|undefined=typeof document==='undefined'?undefined:document){
  let timer:ReturnType<typeof setTimeout>|undefined,failures=0,running=false,refreshQueued=false,denied=false;
  const schedule=(seconds:number)=>{
    clearTimeout(timer);
    if(!signal.aborted)timer=setTimeout(()=>{timer=undefined;void read();},Math.max(3,seconds)*1000);
  };
  const read=async()=>{
    if(signal.aborted||denied)return;
    if(running){refreshQueued=true;return;}
    clearTimeout(timer);timer=undefined;running=true;
    try{
      const response=await fetch(`/api/proxy/tracking/games/works/${id}/achievement-catalogue`,{
        signal:AbortSignal.any([signal,AbortSignal.timeout(30_000)]),cache:'no-store'});
      if(signal.aborted)return;
      if(response.status===401||response.status===403){denied=true;return;}
      if(!response.ok){
        const header=response.headers.get('Retry-After')||'';
        const delay=Number(header)||Math.max(0,(Date.parse(header)-Date.now())/1000);
        schedule(Math.max(delay||0,Math.min(300,5*2**Math.min(failures++,6))));return;
      }
      const data=await response.json();
      if(signal.aborted||data.catalogue?.work_id!==id)return;
      failures=0;
      apply(data.catalogue,data.status);
      if(data.status?.pending)schedule(data.status.retry_after||3);
    }catch{
      // Retry transport failures without touching an existing personal draft.
      schedule(Math.min(300,5*2**Math.min(failures++,6)));
    }finally{
      running=false;
      if(refreshQueued){refreshQueued=false;if(timer===undefined)void read();}
    }
  };
  // Capture also observes the non-bubbling publication from a detail root while
  // an editor is open. Join an in-flight read rather than racing its old result.
  primaryEvents?.addEventListener('anylist:game-primary',event=>{
    const data=(event as CustomEvent).detail;
    if(data?.type==='game'&&data.id===id&&timer===undefined)void read();
  },{signal,capture:true});
  signal.addEventListener('abort',()=>{if(timer!==undefined)clearTimeout(timer);},{once:true});
  void read();
}
