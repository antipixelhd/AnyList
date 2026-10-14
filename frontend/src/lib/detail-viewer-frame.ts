/** Leave room for exterior controls and fit without stretching or upscaling. */
export function detailViewerFrame(width:number,height:number,viewportWidth:number,viewportHeight:number) {
  const scale=Math.min(1,Math.max(1,viewportWidth-160)/width,Math.max(1,viewportHeight-172)/height);
  return {width:width*scale,height:height*scale};
}

export function bindDetailViewerFrame(dialog:HTMLDialogElement,signal:AbortSignal) {
  const desktop=matchMedia('(min-width:651px)');
  let selected:HTMLImageElement|undefined,group:'posters'|'artwork'='artwork';
  const fit=()=>{
    if(!desktop.matches||!dialog.open)return;
    const ready=selected?.naturalWidth&&selected?.naturalHeight;
    const dimensions=detailViewerFrame(ready?selected!.naturalWidth:group==='posters'?780:1280,
      ready?selected!.naturalHeight:group==='posters'?1170:720,window.innerWidth,window.innerHeight);
    dialog.style.setProperty('--viewer-image-width',`${dimensions.width}px`);
    dialog.style.setProperty('--viewer-image-height',`${dimensions.height}px`);
  };
  window.addEventListener('resize',fit,{signal,passive:true});
  desktop.addEventListener('change',fit,{signal});
  dialog.addEventListener('close',()=>{selected=undefined;},{signal});
  return (image:HTMLImageElement,kind:'posters'|'artwork')=>{selected=image;group=kind;fit();};
}
