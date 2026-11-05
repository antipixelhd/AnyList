import {responsiveArtwork} from './responsive-artwork.ts';

type Work = {backdrop?:string|null;list_status?:string|null};

export function contributorBanner(works:Work[], studioBanner?:string|null, inList=false) {
  const backdrop=works.find(work=>(!inList||work.list_status)&&work.backdrop)?.backdrop;
  return {src:responsiveArtwork(backdrop||studioBanner||null,{size:'w1280',route:'direct'})?.src||'',isLogo:!backdrop&&!!studioBanner};
}

/** Keep the existing image mounted, but never reveal artwork from the old list. */
export function mountContributorBanner(image:HTMLImageElement, signal:AbortSignal) {
  const container=image.closest('.contributor-banner');
  const loaded=()=>{image.hidden=!image.naturalWidth||!image.getAttribute('src');};
  image.addEventListener('load',loaded,{signal});
  image.addEventListener('error',()=>{image.hidden=true;},{signal});
  if(image.complete)loaded();
  return (works:Work[], studioBanner?:string|null, inList=false)=>{
    if(signal.aborted)return;
    const {src,isLogo}=contributorBanner(works,studioBanner,inList);
    container?.classList.toggle('is-logo',isLogo);
    if(src===image.getAttribute('src'))return;
    image.hidden=true;
    if(src){image.src=src;if(image.complete)loaded();}
    else image.removeAttribute('src');
  };
}
