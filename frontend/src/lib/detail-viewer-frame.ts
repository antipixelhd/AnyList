/** Leave room for previews and viewport margins without stretching or upscaling. */
export function detailViewerFrame(width:number,height:number,viewportWidth:number,viewportHeight:number) {
  const scale=Math.min(1,Math.max(1,viewportWidth-48)/width,Math.max(1,viewportHeight-124)/height);
  return {width:width*scale,height:height*scale};
}

/** Poster variants share one viewport-sized stage, regardless of source size. */
export function detailPosterViewerFrame(viewportWidth:number,viewportHeight:number) {
  const height=Math.max(1,Math.min(1170,viewportHeight-48,(viewportWidth-48)*1.5));
  return {width:height*2/3,height};
}

export function bindDetailViewerFrame(dialog:HTMLDialogElement,signal:AbortSignal) {
  let selected:HTMLImageElement|undefined,group:'posters'|'artwork'='artwork';
  const fit=()=>{
    const ready=selected?.naturalWidth&&selected?.naturalHeight;
    const dimensions=group==='posters'?detailPosterViewerFrame(window.innerWidth,window.innerHeight)
      :detailViewerFrame(ready?selected!.naturalWidth:1280,ready?selected!.naturalHeight:720,window.innerWidth,window.innerHeight);
    dialog.style.setProperty('--viewer-image-width',`${dimensions.width}px`);
    dialog.style.setProperty('--viewer-image-height',`${dimensions.height}px`);
  };
  const fitVisible=()=>{if(dialog.open)fit();};
  window.addEventListener('resize',fitVisible,{signal,passive:true});
  dialog.addEventListener('close',()=>{selected=undefined;},{signal});
  // Prepare the correct aspect ratio before showModal measures the first frame.
  return (image:HTMLImageElement|undefined,kind:'posters'|'artwork')=>{selected=image;group=kind;fit();};
}
