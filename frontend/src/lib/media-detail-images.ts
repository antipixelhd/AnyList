import {showDialog,reveal} from './ui-motion';
import {showToast} from './toasts';
import {bindImageViewerDismissal} from './image-viewer';
import {detailBackground,type DetailLoadState} from './detail-background';
import {responsiveArtwork} from './responsive-artwork';
import {reconcileGalleryChildren,createGalleryThumbnailWatcher} from './detail-gallery-dom';

type Galleries = {posters:string[]; artwork:string[]};
const imageKey=(url:string)=>url.replace(/(https:\/\/image\.tmdb\.org\/t\/p\/)[^/]+\//,'$1');
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
  let pending=!!info.imagesAvailable&&info.imageStatus?.state!=='ready';
  const skeleton=lightbox.querySelector<HTMLElement>('[data-image-viewer-loading]')!;
  const slots=lightbox.querySelector<HTMLElement>('[data-image-viewer-slots]')!;
  const viewerStatus=lightbox.querySelector<HTMLElement>('[data-image-viewer-status]')!;
  let active:keyof Galleries='artwork',current=0,revision=0;
  let loadingImage:string|undefined;
  const watchThumbnail=createGalleryThumbnailWatcher(signal,thumbnail=>{
    if(thumbnail.getBoundingClientRect().top<innerHeight)reveal(thumbnail);
  });
  const arrows=[...lightbox.querySelectorAll<HTMLButtonElement>('[data-image-viewer-step]')];
  const paintSlots=()=>{
    if(!lightbox.open)return;
    const rows=galleries[active],count=pending?10:rows.length;
    const existing=new Map([...slots.querySelectorAll<HTMLButtonElement>('button')].map(button=>[button.dataset.slotKey,button]));
    const buttons=Array.from({length:count},(_,index)=>{
      const key=`${active}:${rows[index]?imageKey(rows[index]):`pending:${index}`}`;
      let button=existing.get(key);
      if(!button){
        button=document.createElement('button');button.type='button';button.dataset.slotKey=key;
        if(rows[index]){const thumbnail=new Image();thumbnail.alt='';thumbnail.loading='lazy';thumbnail.src=responsiveArtwork(rows[index],{size:'w185',route:'direct'})?.src||rows[index];button.append(thumbnail);}
        else button.className='detail-image-skeleton';
        button.addEventListener('click',()=>showImage(Number(button!.dataset.imageIndex)),{signal});
      }
      button.dataset.imageIndex=String(index);
      button.setAttribute('aria-label',`${active==='posters'?'Poster':'Artwork'} ${index+1}${rows[index]?'':': loading'}`);
      button.setAttribute('aria-current',String(current===index));watchThumbnail(button);return button;
    });
    reconcileGalleryChildren(slots,buttons);
  };
  const showImage=(index:number)=>{
    const rows=galleries[active];
    const count=pending?10:rows.length;
    current=count?(index+count)%count:0;
    const url=rows[current],group=active;
    caption.textContent=`${name} · ${group==='posters'?'Poster':'Artwork'} ${current+1}${pending?'':` of ${rows.length}`}`;
    arrows.forEach(arrow=>arrow.hidden=count<2);
    paintSlots();
    if(url&&imageKey(image.src)===imageKey(url)&&image.complete&&image.naturalWidth>0){
      if(loadingImage)revision++;
      loadingImage=undefined;skeleton.hidden=true;image.style.visibility='';image.alt=caption.textContent!;return;
    }
    const requestKey=url?`${group}:${imageKey(url)}`:undefined;
    if(requestKey&&loadingImage===requestKey)return;
    const version=++revision;loadingImage=requestKey;
    image.style.visibility='hidden';
    skeleton.hidden=false;skeleton.classList.toggle('detail-image-skeleton',pending||!!url);
    skeleton.textContent=!url&&!pending?'No images available.':'';
    if(!url)return;
    const probe=new Image();probe.src=url;
    void probe.decode().then(()=>{
      if(version!==revision||signal.aborted)return;
      loadingImage=undefined;
      if(group==='artwork'&&probe.naturalWidth<=probe.naturalHeight){
        galleries.artwork=galleries.artwork.filter(value=>value!==url);
        showImage(Math.min(current,galleries.artwork.length-1));
        return;
      }
      image.src=url;image.alt=`${name} · ${group==='posters'?'Poster':'Artwork'} ${current+1}${pending?'':` of ${rows.length}`}`;
      image.style.visibility='';
      skeleton.hidden=true;
      caption.textContent=image.alt;
      arrows.forEach(arrow=>arrow.hidden=!pending&&rows.length<2);
      reveal(image);
    }).catch(()=>{
      if(version!==revision||signal.aborted)return;
      loadingImage=undefined;
      galleries[group]=galleries[group].filter(value=>value!==url);
      if(galleries[group].length)showImage(current);
      else {showImage(0);showToast('Unable to load this image. Retry the gallery.','error');}
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
  root.querySelectorAll<HTMLButtonElement>('.lab-gallery [data-detail-image],.lab-art-image').forEach(button=>{watchLandscape(button);watchThumbnail(button);});
  const galleryStatus=root.querySelector<HTMLElement>('[data-detail-artwork-status]');
  const paintHero=(url:string|undefined)=>{
    if(!url)return;
    const hero=root.querySelector<HTMLImageElement>('.lab-hero-image');
    const button=root.querySelector<HTMLButtonElement>('.lab-hero-art-button');
    if(button){button.dataset.detailImage=url;button.hidden=false;}
    if(!hero)return;
    const sameImage=imageKey(hero.src)===imageKey(url);
    if(sameImage&&hero.complete&&hero.naturalWidth){hero.hidden=false;return;}
    if(!sameImage)hero.hidden=true;
    hero.onload=()=>{if(signal.aborted)return;hero.hidden=false;reveal(hero);};
    hero.onerror=()=>{hero.hidden=true;};
    if(sameImage)return;
    const artwork=responsiveArtwork(url,{size:'w1280',sizes:'100vw',route:'direct'});
    if(artwork?.srcset)hero.srcset=artwork.srcset;else hero.removeAttribute('srcset');
    hero.sizes=artwork?.sizes||'100vw';hero.src=artwork?.src||url;
  };
  const applyImages=(data:Galleries&{poster?:string;backdrop?:string;image_status?:DetailLoadState;image_replacements?:Record<string,string>})=>{
      const selected=galleries[active][current];
      galleries.posters=uniqueDetailImages(data.posters||[]);
      galleries.artwork=uniqueDetailImages(data.artwork||[]);
      pending=!!data.image_status&&['pending','deferred'].includes(data.image_status.state||'');
      if(galleryStatus){galleryStatus.hidden=!!galleries.artwork.length;galleryStatus.textContent='No artwork available.';}
      const cover=root.querySelector<HTMLButtonElement>('.media-cover');
      const primary=data.poster||galleries.posters[0];
      if(cover&&primary){
        cover.dataset.detailImage=primary;
        const coverImage=cover.querySelector('img');
        if(coverImage&&imageKey(coverImage.src)!==imageKey(primary)){coverImage.removeAttribute('srcset');coverImage.src=primary.replace('/original/','/w500/');}
      }
      const heroButton=root.querySelector<HTMLButtonElement>('.lab-hero-art-button');
      if(galleries.artwork.length){
        const currentKey=(data.backdrop||heroButton?.dataset.detailImage||'').replace(/(https:\/\/image\.tmdb\.org\/t\/p\/)[^/]+\//,'$1');
        const preferred=galleries.artwork.find(url=>url.replace(/(https:\/\/image\.tmdb\.org\/t\/p\/)[^/]+\//,'$1')===currentKey)||galleries.artwork[0];
        paintHero(preferred);
      }
      const preview=root.querySelector<HTMLButtonElement>('.lab-art-image');
      if(preview){
        if(galleries.artwork.length){
          const preferred=galleries.artwork.includes(preview.dataset.detailImage||'')?preview.dataset.detailImage!:galleries.artwork[0];
          preview.dataset.detailImage=preferred;
          const thumbnail=preview.querySelector('img');if(thumbnail){
            const src=responsiveArtwork(preferred,{size:'w780',route:'direct'})?.src||preferred;
            if(imageKey(thumbnail.src)!==imageKey(src))thumbnail.src=src;
            watchThumbnail(preview);
          }
        }else {preview.classList.toggle('detail-image-skeleton',pending);const thumbnail=preview.querySelector('img');if(thumbnail)thumbnail.hidden=true;}
      }
      const grid=root.querySelector<HTMLElement>('[data-detail-artwork-grid]');
      if(grid){
        const existing=new Map([...grid.querySelectorAll<HTMLButtonElement>('[data-detail-image]')].map((button,index)=>[
          button.dataset.detailImage?imageKey(button.dataset.detailImage):`pending:${index}`,button]));
        const buttons=Array.from({length:pending?10:galleries.artwork.length},(_,index)=>{
          const url=galleries.artwork[index];
          let button=existing.get(url?imageKey(url):`pending:${index}`);
          if(!button){
            button=document.createElement('button');button.type='button';button.dataset.detailImage=url||'';
            if(url){const thumbnail=document.createElement('img');thumbnail.src=responsiveArtwork(url,{size:'w500',route:'direct'})?.src||url;thumbnail.alt=`${name} artwork`;thumbnail.loading='lazy';button.append(thumbnail);watchLandscape(button);watchThumbnail(button);}
            else button.className='detail-image-skeleton';
          }
          button.setAttribute('aria-label',`Expand ${name} artwork ${index+1}`);watchThumbnail(button);return button;
        });
        reconcileGalleryChildren(grid,buttons);
      }
      const preferred=data.image_replacements?.[selected]||selected;
      if(lightbox.open){
        const index=preferred?galleries[active].findIndex(url=>imageKey(url)===imageKey(preferred)):-1;
        showImage(index>=0?index:Math.min(current,Math.max(0,(pending?10:galleries[active].length)-1)));
      }
      return data.image_status;
  };
  const resource=detailBackground(`/api/proxy/tracking/title/${info.id}/images`,signal,applyImages,(state,error)=>{
    viewerStatus.hidden=state!=='retry';
    if(state==='retry'){
      viewerStatus.replaceChildren(document.createTextNode(error||'Images are temporarily unavailable. Retrying automatically.'));
      const button=document.createElement('button');button.type='button';button.className='lab-text-button';button.textContent='Retry';button.addEventListener('click',()=>void resource.retry(),{signal});viewerStatus.append(' ',button);
    }
    if(galleryStatus){galleryStatus.hidden=state!=='retry'&&(!!galleries.artwork.length||pending);galleryStatus.textContent=state==='retry'?error||'Artwork is temporarily unavailable. Retrying automatically.':'No artwork available.';}
  });
  const loadImages=()=>info.imagesAvailable?resource.load():Promise.resolve();
  if(pending)applyImages({...galleries,image_status:{state:'pending'}});
  root.addEventListener('click',event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('[data-detail-image]');
    if(!button)return;
    active=button.dataset.detailGallery==='posters'?'posters':'artwork';
    lightbox.dataset.gallery=active;
    const url=button.dataset.detailImage!;
    const gridIndex=[...root.querySelectorAll('[data-detail-artwork-grid] button')].indexOf(button);
    showDialog(lightbox);showImage(gridIndex>=0?gridIndex:Math.max(0,galleries[active].indexOf(url)));void loadImages();
  },{signal});
  // The hero is critical: start its small metadata read immediately. Full
  // gallery curation and extra image downloads wait for a gallery interaction.
  if(info.heroAvailable){
    const background=detailBackground<{backdrop?:string;background_status?:DetailLoadState}>(
      `/api/proxy/tracking/title/${info.id}/background`,signal,data=>{
        if(data.backdrop)applyImages({...galleries,artwork:uniqueDetailImages([...galleries.artwork,data.backdrop]),
          backdrop:data.backdrop,image_status:{state:pending?'pending':'ready'}});
        return data.background_status;
      },()=>{});
    void background.load();
    const artworkPanel=root.querySelector<HTMLElement>('[data-detail-panel="artwork"]');
    if(info.imagesAvailable&&artworkPanel&&!artworkPanel.hidden)void loadImages();
  }
  bindImageViewerDismissal(lightbox,signal);
  lightbox.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();showImage(current+(event.key==='ArrowLeft'?-1:1));}},{signal});
  arrows.forEach(button=>button.addEventListener('click',()=>showImage(current+Number(button.dataset.imageViewerStep)),{signal}));
  return loadImages;
}
