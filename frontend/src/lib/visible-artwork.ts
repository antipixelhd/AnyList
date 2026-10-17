/** Unlike native lazy loading, hidden panels never receive image sources. */
export function visibleArtwork(signal:AbortSignal) {
  const work=new WeakMap<Element,()=>void>();
  const observer=new IntersectionObserver(entries=>{
    for(const entry of entries)if(entry.isIntersecting){
      observer.unobserve(entry.target);const start=work.get(entry.target);work.delete(entry.target);
      if(!signal.aborted)start?.();
    }
  },{rootMargin:'80px'});
  signal.addEventListener('abort',()=>observer.disconnect(),{once:true});
  return (element:Element,start:()=>void)=>{if(!signal.aborted){work.set(element,start);observer.observe(element);}};
}

export function observeDeferredArtwork(root:HTMLElement,signal:AbortSignal) {
  const visible=visibleArtwork(signal);
  root.querySelectorAll<HTMLImageElement>('img[data-deferred-src]').forEach(image=>visible(image,()=>{
    if(image.dataset.deferredSrcset)image.srcset=image.dataset.deferredSrcset;
    if(image.dataset.deferredSizes)image.sizes=image.dataset.deferredSizes;
    image.src=image.dataset.deferredSrc!;delete image.dataset.deferredSrc;
  }));
}
