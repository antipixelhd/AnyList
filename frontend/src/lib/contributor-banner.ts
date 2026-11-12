import {responsiveArtwork} from './responsive-artwork.ts';

type Work = {backdrop?:string|null;list_status?:string|null};

export function contributorBanner(works:Work[], studioBanner?:string|null, inList=false) {
  const backdrop=works.find(work=>(!inList||work.list_status)&&work.backdrop)?.backdrop;
  return {src:responsiveArtwork(backdrop||studioBanner||null,{size:'w1280',route:'direct'})?.src||'',isLogo:!backdrop&&!!studioBanner};
}

/** Fade the outgoing artwork before swapping sources; reveal only a loaded image. */
export function mountContributorBanner(image:HTMLImageElement, signal:AbortSignal) {
  const container=image.closest('.contributor-banner');
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  let animation:Animation|undefined,version=0,waiting=false;
  let target=image.getAttribute('src')||'';
  const cancel=()=>{animation?.cancel();animation=undefined;};
  const fade=(from:number,to:number)=>{
    cancel();
    if(reduced.matches)return Promise.resolve();
    const current=image.animate([{opacity:from},{opacity:to}],{duration:220,easing:'ease-in-out',fill:'both'});
    animation=current;
    return current.finished.catch(()=>{});
  };
  const loaded=()=>{
    if(!waiting||signal.aborted)return;
    waiting=false;
    if(!image.naturalWidth||!image.getAttribute('src')){image.hidden=true;return;}
    cancel();image.hidden=false;
    const current=version;
    void fade(0,Number(getComputedStyle(image).opacity)).then(()=>{if(current===version)cancel();});
  };
  image.addEventListener('load',loaded,{signal});
  image.addEventListener('error',()=>{image.hidden=true;if(waiting){waiting=false;cancel();}},{signal});
  if(image.complete&&!image.naturalWidth)image.hidden=true;
  reduced.addEventListener('change',cancel,{signal});
  signal.addEventListener('abort',()=>{version++;cancel();},{once:true});
  return (works:Work[], studioBanner?:string|null, inList=false)=>{
    if(signal.aborted)return;
    const {src,isLogo}=contributorBanner(works,studioBanner,inList);
    if(src===target)return;
    target=src;waiting=false;
    const current=++version;
    const swap=()=>{
      if(signal.aborted||current!==version)return;
      cancel();image.hidden=true;
      container?.classList.toggle('is-logo',isLogo);
      if(src){waiting=true;image.src=src;if(image.complete)loaded();}
      else image.removeAttribute('src');
    };
    if(!image.hidden&&image.getAttribute('src')&&!reduced.matches)
      void fade(Number(getComputedStyle(image).opacity),0).then(swap);
    else swap();
  };
}
