/** Wheel notches become bounded slide steps; a large delta never skips a gallery. */
export function bindDetailImageWheel(dialog:HTMLDialogElement,track:HTMLElement,signal:AbortSignal,step:(direction:number)=>void) {
  let delta=0,queue=0,last=0;
  let timer:ReturnType<typeof setTimeout>|undefined;
  const clear=()=>{clearTimeout(timer);timer=undefined;delta=queue=last=0;};
  const drain=()=>{
    if(!dialog.open||signal.aborted){clear();return;}
    if(!queue){timer=undefined;return;}
    const direction=Math.sign(queue);queue-=direction;step(direction);
    timer=setTimeout(drain,180);
  };
  track.addEventListener('wheel',event=>{
    // Preserve browser zoom and native horizontal touchpad gestures.
    if(!dialog.open||event.ctrlKey||event.metaKey||Math.abs(event.deltaX)>Math.abs(event.deltaY)||!event.deltaY)return;
    event.preventDefault();
    const now=performance.now(),direction=-Math.sign(event.deltaY);
    if(now-last>200||Math.sign(delta)!==direction)delta=0;
    if(queue&&Math.sign(queue)!==direction)queue=0;
    last=now;
    const pixels=Math.min(80,Math.abs(event.deltaY)*(event.deltaMode===1?20:event.deltaMode===2?track.clientHeight:1));
    delta+=direction*pixels;
    if(Math.abs(delta)<40)return;
    delta=0;queue=Math.max(-3,Math.min(3,queue+direction));
    if(timer===undefined)drain();
  },{signal,passive:false});
  dialog.addEventListener('close',clear,{signal});
  signal.addEventListener('abort',clear,{once:true});
}
