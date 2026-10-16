/** Keep loaded images attached when metadata or selection changes. */
export function reconcileGalleryChildren(parent: HTMLElement, children: HTMLElement[]) {
  const wanted=new Set(children);
  [...parent.children].forEach(child=>{if(!wanted.has(child as HTMLElement))child.remove();});
  children.forEach((child,index)=>{
    if(parent.children[index]!==child)parent.insertBefore(child,parent.children[index]||null);
  });
}

/** Browser lazy loading may fetch far beyond the viewport. Gallery variants
 * get a source only when their preview actually intersects the visible area. */
export function createGalleryImageLoader(signal:AbortSignal,start:(button:HTMLElement)=>void) {
  const observer=new IntersectionObserver(entries=>{
    for(const entry of entries){
      if(!entry.isIntersecting)continue;
      const button=entry.target as HTMLElement,image=button.querySelector<HTMLImageElement>('img');
      observer.unobserve(button);
      if(signal.aborted||!image?.dataset.gallerySrc)continue;
      image.src=image.dataset.gallerySrc;delete image.dataset.gallerySrc;start(button);
    }
  },{rootMargin:'0px'});
  signal.addEventListener('abort',()=>observer.disconnect(),{once:true});
  return (button:HTMLElement)=>{
    if(signal.aborted)return;
    if(button.querySelector<HTMLImageElement>('img')?.dataset.gallerySrc)observer.observe(button);
    else start(button);
  };
}

/** A lazy image must retain a layout box to load; its skeleton covers the wait. */
export function createGalleryThumbnailWatcher(signal: AbortSignal, ready:(image:HTMLImageElement)=>void) {
  const watched=new WeakMap<HTMLImageElement,{src:string;cleanup:()=>void}>();
  return (button:HTMLElement)=>{
    const image=button.querySelector<HTMLImageElement>('img');
    if(!image||!image.getAttribute('src')||signal.aborted)return;
    image.hidden=false;
    const src=image.src;
    const finish=()=>{
      if(signal.aborted||image.src!==src||!image.naturalWidth)return;
      button.classList.remove('detail-image-skeleton');image.style.removeProperty('opacity');
      watched.get(image)?.cleanup();watched.delete(image);ready(image);
    };
    if(image.complete&&image.naturalWidth){
      button.classList.remove('detail-image-skeleton');image.style.removeProperty('opacity');
      watched.get(image)?.cleanup();watched.delete(image);return;
    }
    button.classList.add('detail-image-skeleton');image.style.opacity='0';
    if(watched.get(image)?.src===src)return;
    watched.get(image)?.cleanup();
    const fail=()=>{
      if(signal.aborted||image.src!==src)return;
      button.classList.remove('detail-image-skeleton');image.style.removeProperty('opacity');
      watched.get(image)?.cleanup();watched.delete(image);
    };
    const cleanup=()=>{image.removeEventListener('load',finish);image.removeEventListener('error',fail);};
    watched.set(image,{src,cleanup});
    image.addEventListener('load',finish,{signal,once:true});
    image.addEventListener('error',fail,{signal,once:true});
    if(image.complete&&image.naturalWidth)finish();
  };
}
