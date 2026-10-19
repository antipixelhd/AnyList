/** Deferred offscreen thumbnails stay lazy; errors count as settled loads. */
export function galleryPreviewImagesSettled(images:Iterable<Pick<HTMLImageElement,'getAttribute'|'complete'>>) {
  return ![...images].some(image=>image.getAttribute('src')&&!image.complete);
}

/** Only user activity resets the deadline; loading and animation merely recheck it. */
export function createGalleryPreviewTimer(ready:()=>boolean,show:()=>void,hide:()=>void,signal:AbortSignal) {
  let timer:ReturnType<typeof setTimeout>|undefined,visible=false,deadline=0;
  const pause=()=>{clearTimeout(timer);timer=undefined;};
  const schedule=()=>{
    if(signal.aborted||!visible||timer!==undefined||!ready())return;
    timer=setTimeout(()=>{
      timer=undefined;
      if(signal.aborted||!visible)return;
      if(!ready())return;
      visible=false;hide();
    },Math.max(0,deadline-Date.now()));
  };
  const reveal=()=>{if(signal.aborted)return;pause();deadline=Date.now()+1500;visible=true;show();schedule();};
  const refresh=()=>{if(!ready())pause();else schedule();};
  const reset=()=>{pause();visible=false;};
  signal.addEventListener('abort',reset,{once:true});
  return {reveal,refresh,pause,reset,hide:()=>{if(!ready()){reveal();return;}reset();hide();}};
}
