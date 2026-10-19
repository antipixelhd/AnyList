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

/** People and logos open as a compact preview; zoom can still use the viewport. */
export function detailEntityViewerFrame(width:number,height:number,viewportWidth:number,viewportHeight:number) {
  const scale=Math.min(1,600/width,600/height,viewportWidth*.8/width,viewportHeight*.7/height);
  return {width:width*scale,height:height*scale};
}

export function bindDetailViewerFrame(dialog:HTMLDialogElement,signal:AbortSignal,compact=false) {
  if(compact)dialog.dataset.viewerCompact='';
  let selected:HTMLImageElement|undefined,group:'posters'|'artwork'='artwork';
  const fit=()=>{
    const ready=selected?.naturalWidth&&selected?.naturalHeight;
    const landscapeShort=window.innerWidth>650&&window.innerHeight<=650;
    const mobile=window.innerWidth<=650||landscapeShort;
    const width=group==='posters'?780:ready?selected!.naturalWidth:1280;
    const height=group==='posters'?1170:ready?selected!.naturalHeight:720;
    const scale=Math.min(1,window.innerWidth/width,Math.max(1,window.innerHeight-(landscapeShort?0:48))/height);
    const dimensions=compact?detailEntityViewerFrame(ready?selected!.naturalWidth:width,ready?selected!.naturalHeight:height,window.innerWidth,window.innerHeight)
      :mobile?{width:width*scale,height:height*scale}
      :group==='posters'?detailPosterViewerFrame(window.innerWidth,window.innerHeight)
      :detailViewerFrame(ready?selected!.naturalWidth:1280,ready?selected!.naturalHeight:720,window.innerWidth,window.innerHeight);
    dialog.dataset.viewerPreviewsBelow=String((mobile||compact)&&window.innerHeight-dimensions.height>=124);
    dialog.style.setProperty('--viewer-image-width',`${dimensions.width}px`);
    dialog.style.setProperty('--viewer-image-height',`${dimensions.height}px`);
  };
  const fitVisible=()=>{if(dialog.open)fit();};
  window.addEventListener('resize',fitVisible,{signal,passive:true});
  dialog.addEventListener('close',()=>{selected=undefined;},{signal});
  // Prepare the correct aspect ratio before showModal measures the first frame.
  return (image:HTMLImageElement|undefined,kind:'posters'|'artwork')=>{selected=image;group=kind;fit();};
}
