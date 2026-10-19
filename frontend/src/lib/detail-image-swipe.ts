/** Finger-following paging. Keep one selection until the short snap completes. */
export function createDetailImageSwipe(track:HTMLElement,signal:AbortSignal,select:(index:number)=>void) {
  const reduced=matchMedia('(prefers-reduced-motion:reduce)');
  let frame=0,start=0,index=0;
  const nearest=()=>Math.max(0,Math.min(track.children.length-1,Math.round(track.scrollLeft/track.clientWidth)));
  const cancel=(commit=false)=>{
    cancelAnimationFrame(frame);frame=0;
    const active='viewerSwiping' in track.dataset;
    delete track.dataset.viewerSwiping;
    if(active&&!commit)track.scrollLeft=index*track.clientWidth;
    if(active&&commit){const selected=nearest();track.scrollLeft=selected*track.clientWidth;select(selected);}
  };
  const begin=()=>{
    cancel(true);index=nearest();start=track.scrollLeft;
    track.dataset.viewerSwiping='';
  };
  const move=(dx:number)=>{track.scrollLeft=Math.max(0,Math.min((track.children.length-1)*track.clientWidth,start-dx));};
  const finish=(dx:number,velocity:number,cancelled=false)=>{
    const deliberate=Math.abs(dx)>=Math.max(40,track.clientWidth*.15)||(Math.abs(dx)>=16&&Math.abs(velocity)>=.5);
    const destination=Math.max(0,Math.min(track.children.length-1,index+(!cancelled&&deliberate?(dx<0?1:-1):0)));
    const from=track.scrollLeft,to=destination*track.clientWidth,began=performance.now();
    const done=()=>{frame=0;track.scrollLeft=to;delete track.dataset.viewerSwiping;select(destination);};
    if(reduced.matches||Math.abs(to-from)<1){done();return;}
    const tick=(now:number)=>{
      const progress=Math.min(1,(now-began)/160);
      track.scrollLeft=from+(to-from)*(1-(1-progress)**3);
      if(progress<1)frame=requestAnimationFrame(tick);else done();
    };
    frame=requestAnimationFrame(tick);
  };
  signal.addEventListener('abort',()=>cancel(),{once:true});
  return {begin,move,finish,cancel};
}
