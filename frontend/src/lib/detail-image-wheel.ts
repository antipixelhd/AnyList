/** Each deliberate notch retargets immediately; never queue obsolete animation work. */
export function bindDetailImageWheel(dialog:HTMLDialogElement,track:HTMLElement,signal:AbortSignal,step:(direction:number)=>void) {
  let delta=0,last=0;
  const clear=()=>{delta=last=0;};
  dialog.addEventListener('wheel',event=>{
    // Preserve browser zoom and native horizontal touchpad gestures.
    if(!dialog.open||signal.aborted||event.ctrlKey||event.metaKey||Math.abs(event.deltaX)>Math.abs(event.deltaY)||!event.deltaY)return;
    // Preview-strip scrolling stays native; the rest of the modal navigates
    // consistently even when a fading control changes the pointer hit target.
    if((event.target as Element)?.closest('[data-image-viewer-slots]'))return;
    event.preventDefault();
    const now=performance.now(),direction=Math.sign(event.deltaY);
    if(now-last>200||Math.sign(delta)!==direction)delta=0;
    last=now;
    const pixels=Math.min(80,Math.abs(event.deltaY)*(event.deltaMode===1?20:event.deltaMode===2?track.clientHeight:1));
    delta+=direction*pixels;
    if(Math.abs(delta)<40)return;
    delta=0;step(direction);
  },{signal,passive:false});
  dialog.addEventListener('close',clear,{signal});
  signal.addEventListener('abort',clear,{once:true});
}
