/** Deferred offscreen thumbnails stay lazy; errors count as settled loads. */
export function galleryPreviewImagesSettled(images:Iterable<Pick<HTMLImageElement,'getAttribute'|'complete'>>) {
  return ![...images].some(image=>image.getAttribute('src')&&!image.complete);
}

/** Start the idle countdown only after visible gallery content has settled. */
export function createGalleryPreviewTimer(ready:()=>boolean,show:()=>void,hide:()=>void,signal:AbortSignal) {
  let timer:ReturnType<typeof setTimeout>|undefined,visible=false;
  const pause=()=>{clearTimeout(timer);timer=undefined;};
  const schedule=()=>{
    if(signal.aborted||!visible||timer!==undefined||!ready())return;
    timer=setTimeout(()=>{
      timer=undefined;
      if(signal.aborted||!visible)return;
      if(!ready()){show();return;}
      visible=false;hide();
    },1500);
  };
  const reveal=()=>{if(signal.aborted)return;pause();visible=true;show();schedule();};
  const refresh=()=>{if(!ready())reveal();else schedule();};
  const reset=()=>{pause();visible=false;};
  signal.addEventListener('abort',reset,{once:true});
  return {reveal,refresh,pause,reset,hide:()=>{if(!ready()){reveal();return;}reset();hide();}};
}
