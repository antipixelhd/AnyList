export type AchievementCatalogue={work_id:number;total:number|null;availability:string;definitions_available:boolean};

/** Upgrade optional capabilities without replacing personal state or drafts. */
export function watchAchievementCatalogue(id:number,signal:AbortSignal,apply:(data:AchievementCatalogue)=>void){
  let timer:ReturnType<typeof setTimeout>|undefined,attempts=0;
  const read=async()=>{
    if(signal.aborted)return;
    try{
      const response=await fetch(`/api/proxy/tracking/games/works/${id}/achievement-catalogue`,{signal,cache:'no-store'});
      if(!response.ok||signal.aborted)return;
      const data=await response.json();
      if(signal.aborted||data.catalogue?.work_id!==id)return;
      apply(data.catalogue);
      if(data.status?.pending&&!data.status.error&&++attempts<12)
        timer=setTimeout(()=>void read(),Math.max(3,Math.min(60,data.status.retry_after||3))*1000);
    }catch{/* Optional metadata failure never resets an editor. */}
  };
  signal.addEventListener('abort',()=>{if(timer!==undefined)clearTimeout(timer);},{once:true});
  void read();
}
