/** Admit the selected image before bulk downloads compete for its bandwidth.
 * Once ready, preload the complete gallery, starting with both swipe neighbors. */
export function preloadDetailGallery(index:number,count:number,prepare:(index:number)=>Promise<unknown>|undefined,valid:()=>boolean) {
  const selected=prepare(index);
  const remaining=()=>{
    if(!valid())return;
    const started=new Set([index]);
    for(let distance=1;started.size<count;distance++){
      for(const next of [(index+distance)%count,(index-distance+count)%count]){
        if(started.has(next))continue;
        started.add(next);prepare(next);
      }
    }
  };
  return Promise.resolve(selected).then(remaining,remaining);
}
