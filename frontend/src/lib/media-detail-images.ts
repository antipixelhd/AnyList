import {showDialog,closeDialog,reveal} from './ui-motion';
import {showToast} from './toasts';
import {bindImageViewerDismissal} from './image-viewer';

type Galleries = {posters:string[]; artwork:string[]};
export function uniqueDetailImages(values:unknown[]):string[] {
  const seen=new Set<string>();
  return values.filter((url):url is string=>{
    if(typeof url!=='string'||!/^https?:\/\//.test(url))return false;
    const key=url.replace(/(https:\/\/image\.tmdb\.org\/t\/p\/)[^/]+\//,'$1');
    if(seen.has(key))return false;
    seen.add(key);return true;
  }).slice(0,10);
}

export function mountDetailImages(root:HTMLElement,info:any,signal:AbortSignal) {
  const lightbox=root.querySelector<HTMLDialogElement>('#detail-lightbox')!;
  const image=lightbox.querySelector<HTMLImageElement>('[data-image-viewer-image]')!;
  const caption=lightbox.querySelector<HTMLElement>('p')!;
  const name=root.querySelector('.lab-heading h1')!.textContent!;
  const galleries:Galleries={posters:uniqueDetailImages(info.posters||[]),artwork:uniqueDetailImages(info.artwork||[])};
  let active:keyof Galleries='artwork',current=0,revision=0;
  const arrows=[...lightbox.querySelectorAll<HTMLButtonElement>('[data-image-viewer-step]')];
  const showImage=(index:number)=>{
    const rows=galleries[active];
    if(!rows.length)return;
    current=(index+rows.length)%rows.length;
    const version=++revision;
    const url=rows[current],group=active;
    image.style.visibility='hidden';
    caption.textContent=`${name} · ${group==='posters'?'Poster':'Artwork'} ${current+1} of ${rows.length}`;
    arrows.forEach(arrow=>arrow.hidden=rows.length<2);
    const probe=new Image();probe.src=url;
    void probe.decode().then(()=>{
      if(version!==revision||signal.aborted)return;
      if(group==='artwork'&&probe.naturalWidth<=probe.naturalHeight){
        galleries.artwork=galleries.artwork.filter(value=>value!==url);
        if(galleries.artwork.length)showImage(current);else void closeDialog(lightbox);
        return;
      }
      image.src=url;image.alt=`${name} · ${group==='posters'?'Poster':'Artwork'} ${current+1} of ${rows.length}`;
      image.style.visibility='';
      caption.textContent=image.alt;
      arrows.forEach(arrow=>arrow.hidden=rows.length<2);
      reveal(image);
    }).catch(()=>{
      if(version!==revision||signal.aborted)return;
      galleries[group]=galleries[group].filter(value=>value!==url);
      if(galleries[group].length)showImage(current);
      else {void closeDialog(lightbox);showToast('Unable to load this image.','error');}
    });
  };
  const watchLandscape=(button:HTMLButtonElement)=>{
    const thumbnail=button.querySelector<HTMLImageElement>('img');
    if(!thumbnail)return;
    const inspect=()=>{
      if(thumbnail.naturalWidth&&thumbnail.naturalWidth<=thumbnail.naturalHeight){
        const url=button.dataset.detailImage!;
        galleries.artwork=galleries.artwork.filter(value=>value!==url);
        button.hidden=true;
      }
    };
    if(thumbnail.complete)inspect();else thumbnail.addEventListener('load',inspect,{signal,once:true});
  };
  root.querySelectorAll<HTMLButtonElement>('.lab-gallery [data-detail-image],.lab-art-image').forEach(watchLandscape);
  let loading:Promise<void>|undefined;
  const galleryStatus=root.querySelector<HTMLElement>('[data-detail-artwork-status]');
  const loadImages=()=>{
    if(!info.imagesAvailable)return Promise.resolve();
    if(loading)return loading;
    if(galleryStatus&&!galleries.artwork.length){galleryStatus.textContent='Loading artwork…';galleryStatus.hidden=false;}
    loading=fetch(`/api/proxy/tracking/title/${info.id}/images`,{signal}).then(async response=>{
      if(!response.ok)throw new Error('Image variants unavailable');
      const data=await response.json();
      if(signal.aborted)return;
      const selected=galleries[active][current];
      galleries.posters=uniqueDetailImages(data.posters||[]);
      galleries.artwork=uniqueDetailImages(data.artwork||[]);
      if(galleryStatus){galleryStatus.hidden=!!galleries.artwork.length;galleryStatus.textContent='No artwork available.';}
      const cover=root.querySelector<HTMLButtonElement>('.media-cover');
      const primary=data.poster||galleries.posters[0];
      if(cover&&primary){
        cover.dataset.detailImage=primary;
        const coverImage=cover.querySelector('img');
        if(coverImage){coverImage.removeAttribute('srcset');coverImage.src=primary.replace('/original/','/w500/');}
      }
      const heroButton=root.querySelector<HTMLButtonElement>('.lab-hero-art-button');
      const hero=root.querySelector<HTMLImageElement>('.lab-hero-image');
      if(hero)hero.hidden=!galleries.artwork.length;
      if(heroButton)heroButton.hidden=!galleries.artwork.length;
      if(galleries.artwork.length){
        const currentKey=(data.backdrop||heroButton?.dataset.detailImage||'').replace(/(https:\/\/image\.tmdb\.org\/t\/p\/)[^/]+\//,'$1');
        const preferred=galleries.artwork.find(url=>url.replace(/(https:\/\/image\.tmdb\.org\/t\/p\/)[^/]+\//,'$1')===currentKey)||galleries.artwork[0];
        if(heroButton)heroButton.dataset.detailImage=preferred;
        if(hero){hero.removeAttribute('srcset');hero.src=preferred.replace('/original/','/w1280/');}
      }
      const preview=root.querySelector<HTMLButtonElement>('.lab-art-image');
      if(preview){
        if(galleries.artwork.length){
          const preferred=galleries.artwork.includes(preview.dataset.detailImage||'')?preview.dataset.detailImage!:galleries.artwork[0];
          preview.dataset.detailImage=preferred;
          const thumbnail=preview.querySelector('img');if(thumbnail)thumbnail.src=preferred;
        }else preview.closest<HTMLElement>('section')!.hidden=true;
      }
      const grid=root.querySelector<HTMLElement>('[data-detail-artwork-grid]');
      if(grid){
        const existing=new Map([...grid.querySelectorAll<HTMLButtonElement>('[data-detail-image]')].map(button=>[button.dataset.detailImage,button]));
        const buttons=galleries.artwork.map((url,index)=>{
          let button=existing.get(url);
          if(!button){
            button=document.createElement('button');button.type='button';button.dataset.detailImage=url;
            const thumbnail=document.createElement('img');thumbnail.src=url;thumbnail.alt=`${name} artwork`;thumbnail.loading='lazy';button.append(thumbnail);watchLandscape(button);
          }
          button.setAttribute('aria-label',`Expand ${name} artwork ${index+1}`);return button;
        });
        grid.replaceChildren(...buttons);
      }
      const preferred=data.image_replacements?.[selected]||selected;
      if(lightbox.open){if(galleries[active].length)showImage(Math.max(0,galleries[active].indexOf(preferred)));else void closeDialog(lightbox);}
    }).catch(()=>{loading=undefined;if(galleryStatus&&!signal.aborted&&!galleries.artwork.length){galleryStatus.textContent='Artwork could not be loaded.';galleryStatus.hidden=false;}});
    return loading;
  };
  root.addEventListener('click',event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('[data-detail-image]');
    if(!button)return;
    active=button.dataset.detailGallery==='posters'?'posters':'artwork';
    lightbox.dataset.gallery=active;
    const url=button.dataset.detailImage!;
    if(galleries[active].length){showImage(Math.max(0,galleries[active].indexOf(url)));showDialog(lightbox);void loadImages();}
    else {
      button.setAttribute('aria-busy','true');
      void loadImages().then(()=>{if(signal.aborted)return;if(galleries[active].length){showImage(0);showDialog(lightbox);}else showToast('No image variants available.','error');}).finally(()=>button.removeAttribute('aria-busy'));
    }
  },{signal});
  // Refresh stale policies without waiting for the visitor to open a gallery.
  if(info.imagesAvailable)void loadImages();
  bindImageViewerDismissal(lightbox,signal);
  lightbox.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();showImage(current+(event.key==='ArrowLeft'?-1:1));}},{signal});
  arrows.forEach(button=>button.addEventListener('click',()=>showImage(current+Number(button.dataset.imageViewerStep)),{signal}));
  return loadImages;
}
