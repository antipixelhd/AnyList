import {showDialog,reveal} from './ui-motion';
import {showToast} from './toasts';
import {bindImageViewerDismissal} from './image-viewer';
import {detailBackground,type DetailLoadState} from './detail-background';
import {responsiveArtwork,applyResponsiveArtwork,artworkIdentity} from './responsive-artwork';
import {reconcileGalleryChildren,createGalleryThumbnailWatcher,createGalleryImageLoader} from './detail-gallery-dom';
import {createDetailImageCarousel} from './detail-image-carousel';
import {bindDetailImageDragDismiss} from './detail-image-dismiss';
import {editorStore} from './editor-store';
import {createDetailViewerImageLoader} from './detail-viewer-image';

type Galleries = {posters:string[]; artwork:string[]};
const imageKey=artworkIdentity;
export function uniqueDetailImages(values:unknown[]):string[] {
  const seen=new Set<string>();
  return values.filter((url):url is string=>{
    if(typeof url!=='string'||!(/^https?:\/\//.test(url)||url.startsWith('/api/proxy/media/image/')))return false;
    const key=imageKey(url);
    if(seen.has(key))return false;
    seen.add(key);return true;
  }).slice(0,10);
}

export function mountDetailImages(root:HTMLElement,info:any,signal:AbortSignal) {
  const lightbox=root.querySelector<HTMLDialogElement>('#detail-lightbox')!;
  const track=lightbox.querySelector<HTMLElement>('[data-image-viewer-track]')!;
  const prepareViewer=bindDetailImageDragDismiss(lightbox,track,signal);
  const name=root.querySelector('.lab-heading h1')!.textContent!;
  const galleries:Galleries={posters:uniqueDetailImages(info.posters||[]),artwork:uniqueDetailImages(info.artwork||[])};
  let pending=!!info.imagesAvailable&&info.imageStatus?.state!=='ready';
  const slots=lightbox.querySelector<HTMLElement>('[data-image-viewer-slots]')!;
  const viewerStatus=lightbox.querySelector<HTMLElement>('[data-image-viewer-status]')!;
  let active:keyof Galleries='artwork',current=0;
  const loadViewerImage=createDetailViewerImageLoader(signal);
  const preparedImages=new WeakMap<HTMLImageElement,string>();
  const watchThumbnail=createGalleryThumbnailWatcher(signal,thumbnail=>{
    if(thumbnail.getBoundingClientRect().top<innerHeight)reveal(thumbnail);
  });
  const loadThumbnail=createGalleryImageLoader(signal,button=>{
    if(button.matches('.lab-gallery button,.lab-art-image'))watchLandscape(button as HTMLButtonElement);
    watchThumbnail(button);
  });
  const arrows=[...lightbox.querySelectorAll<HTMLButtonElement>('[data-image-viewer-step]')];
  const mobileViewer=matchMedia('(max-width:650px)');
  let previewTimer:ReturnType<typeof setTimeout>|undefined;
  let previewInteracting=false;
  let previewGesture:{x:number;y:number;moved:boolean}|undefined;
  const clearPreviewTimer=()=>{clearTimeout(previewTimer);previewTimer=undefined;};
  const revealPreviews=()=>{
    clearPreviewTimer();
    delete lightbox.dataset.previewsHidden;
    if(!mobileViewer.matches||!lightbox.open||previewInteracting)return;
    previewTimer=setTimeout(()=>{
      const focused=document.activeElement;
      if(focused?.matches(':focus-visible')&&(slots.contains(focused)||arrows.includes(focused as HTMLButtonElement)))return;
      lightbox.dataset.previewsHidden='true';
    },2000);
  };
  lightbox.addEventListener('pointerdown',event=>{
    previewInteracting=true;
    previewGesture={x:event.clientX,y:event.clientY,moved:false};
    clearPreviewTimer();
  },{signal,passive:true});
  lightbox.addEventListener('pointermove',event=>{
    if(previewInteracting&&previewGesture&&Math.hypot(event.clientX-previewGesture.x,event.clientY-previewGesture.y)>8)previewGesture.moved=true;
  },{signal,passive:true});
  const releasePreviews=()=>{
    if(!previewInteracting)return;
    previewInteracting=false;
    if(previewGesture?.moved)revealPreviews();
  };
  window.addEventListener('pointerup',releasePreviews,{signal,passive:true});
  window.addEventListener('pointercancel',()=>{
    if(!previewInteracting)return;
    if(previewGesture)previewGesture.moved=true;
    releasePreviews();
  },{signal,passive:true});
  lightbox.addEventListener('click',event=>{
    if(mobileViewer.matches&&(event.target as Element).closest('[data-image-viewer-track]')){
      if(previewGesture?.moved)return;
      if(lightbox.dataset.previewsHidden)revealPreviews();
      else {clearPreviewTimer();lightbox.dataset.previewsHidden='true';}
    }else revealPreviews();
  },{signal});
  lightbox.addEventListener('focusin',revealPreviews,{signal});
  lightbox.addEventListener('focusout',revealPreviews,{signal});
  slots.addEventListener('scroll',revealPreviews,{signal,passive:true});
  mobileViewer.addEventListener('change',revealPreviews,{signal});
  lightbox.addEventListener('close',()=>{clearPreviewTimer();previewInteracting=false;delete lightbox.dataset.previewsHidden;},{signal});
  signal.addEventListener('abort',clearPreviewTimer,{once:true});
  const carousel=createDetailImageCarousel(track,signal,index=>showImage(index,false,false),index=>prepareImage(index));
  let followedSlot:string|undefined;
  const paintSlots=()=>{
    if(!lightbox.open)return;
    const rows=galleries[active],count=pending?10:rows.length;
    const existing=new Map([...slots.querySelectorAll<HTMLButtonElement>('button')].map(button=>[button.dataset.slotKey,button]));
    const buttons=Array.from({length:count},(_,index)=>{
      const key=`${active}:${rows[index]?imageKey(rows[index]):`pending:${index}`}`;
      let button=existing.get(key);
      if(!button){
        button=document.createElement('button');button.type='button';button.dataset.slotKey=key;
        if(rows[index]){const thumbnail=new Image();thumbnail.alt='';thumbnail.dataset.gallerySrc=responsiveArtwork(rows[index],{size:'w185',route:'auto'})?.src||rows[index];button.append(thumbnail);}
        else button.className='detail-image-skeleton';
        button.addEventListener('click',()=>showImage(Number(button!.dataset.imageIndex),true),{signal});
      }
      button.dataset.imageIndex=String(index);
      button.setAttribute('aria-label',`${active==='posters'?'Poster':'Artwork'} ${index+1}${rows[index]?'':': loading'}`);
      button.setAttribute('aria-current',String(current===index));loadThumbnail(button);return button;
    });
    reconcileGalleryChildren(slots,buttons);
    const selected=buttons[current];
    if(selected&&selected.dataset.slotKey!==followedSlot){
      followedSlot=selected.dataset.slotKey;
      const bounds=slots.getBoundingClientRect(),buttonBounds=selected.getBoundingClientRect();
      slots.scrollTo({left:slots.scrollLeft+buttonBounds.left-bounds.left-(slots.clientWidth-buttonBounds.width)/2,
        behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth'});
    }
  };
  const showImage=(index:number,animate=false,scroll=true)=>{
    const rows=galleries[active];
    const count=pending?10:rows.length;
    const previous=current;
    current=count?(index+count)%count:0;
    if(current!==previous)revealPreviews();
    const group=active;
    const alt=`${name} · ${group==='posters'?'Poster':'Artwork'} ${current+1}`;
    const {image,skeleton}=carousel(rows,url=>`${group}:${imageKey(url)}`,count,current,alt,scroll,animate);
    arrows.forEach(arrow=>{arrow.hidden=count<2;arrow.disabled=Number(arrow.dataset.imageViewerStep)<0?current===0:current>=count-1;});
    paintSlots();
    if(!rows[current]){
      image.style.visibility='hidden';skeleton.hidden=false;
      skeleton.classList.toggle('detail-image-skeleton',pending);
      skeleton.textContent=pending?'':'No images available.';
      return;
    }
    prepareImage(current);
  };
  const prepareImage=(index:number)=>{
    if(!lightbox.open||signal.aborted)return;
    const group=active,original=galleries[group][index];
    if(!original)return;
    const url=responsiveArtwork(original,{size:group==='posters'?'w780':'w1280',route:'auto'})?.src;
    const slide=carousel.slide(index);
    if(!url||!slide||preparedImages.get(slide.image)===url)return;
    const {image,skeleton}=slide;
    preparedImages.set(image,url);
    skeleton.classList.add('detail-image-skeleton');skeleton.textContent='';
    const discard=()=>{
      const selected=galleries[active][current];
      const wasSelected=active===group&&selected&&imageKey(selected)===imageKey(original);
      galleries[group]=galleries[group].filter(value=>imageKey(value)!==imageKey(original));
      if(lightbox.open&&active===group){
        const retained=galleries[group].findIndex(value=>selected&&imageKey(value)===imageKey(selected));
        showImage(retained>=0?retained:Math.min(current,galleries[group].length-1));
      }
      return wasSelected;
    };
    const ready=loadViewerImage(image,skeleton,url,`${name} · ${group==='posters'?'Poster':'Artwork'} ${index+1}`);
    const cached=image.complete&&image.naturalWidth>0;
    void ready.then(loaded=>{
      if(!loaded||signal.aborted)return;
      if(group==='artwork'&&image.naturalWidth<=image.naturalHeight){discard();return;}
      if(!cached&&lightbox.open&&active===group&&galleries[group][current]===original)reveal(image);
    }).catch(()=>{
      if(signal.aborted)return;
      if(discard())showToast('Unable to load this image. Retry the gallery.','error');
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
  root.querySelectorAll<HTMLButtonElement>('.lab-gallery [data-detail-image],.lab-art-image').forEach(loadThumbnail);
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
    const artwork=responsiveArtwork(url,{size:'w1280',sizes:'100vw',route:'auto'});
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
        if(coverImage&&imageKey(coverImage.src)!==imageKey(primary))applyResponsiveArtwork(coverImage,primary,{size:'w500',sizes:coverImage.sizes||'(max-width:650px) 108px, 210px',route:'auto'});
      }
      const heroButton=root.querySelector<HTMLButtonElement>('.lab-hero-art-button');
      if(galleries.artwork.length){
        const currentKey=imageKey(data.backdrop||heroButton?.dataset.detailImage||'');
        const preferred=galleries.artwork.find(url=>imageKey(url)===currentKey)||galleries.artwork[0];
        paintHero(preferred);
      }
      editorStore.updateArtwork(info.id,{poster:primary,backdrop:heroButton?.dataset.detailImage});
      const preview=root.querySelector<HTMLButtonElement>('.lab-art-image');
      if(preview){
        if(galleries.artwork.length){
          const preferred=galleries.artwork.includes(preview.dataset.detailImage||'')?preview.dataset.detailImage!:galleries.artwork[0];
          preview.dataset.detailImage=preferred;
          const thumbnail=preview.querySelector('img');if(thumbnail){
            const src=responsiveArtwork(preferred,{size:'w780',route:'auto'})?.src||preferred;
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
            if(url){const thumbnail=document.createElement('img');thumbnail.dataset.gallerySrc=responsiveArtwork(url,{size:'w500',route:'auto'})?.src||url;thumbnail.alt=`${name} artwork`;button.append(thumbnail);}
            else button.className='detail-image-skeleton';
          }
          button.setAttribute('aria-label',`Expand ${name} artwork ${index+1}`);loadThumbnail(button);return button;
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
    followedSlot=undefined;
    prepareViewer();
    showDialog(lightbox);revealPreviews();showImage(gridIndex>=0?gridIndex:Math.max(0,galleries[active].findIndex(value=>imageKey(value)===imageKey(url))));void loadImages();
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
  lightbox.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();showImage(Math.max(0,Math.min((pending?10:galleries[active].length)-1,current+(event.key==='ArrowLeft'?-1:1))),true);}},{signal});
  arrows.forEach(button=>button.addEventListener('click',()=>showImage(Math.max(0,Math.min((pending?10:galleries[active].length)-1,current+Number(button.dataset.imageViewerStep))),true),{signal}));
  return loadImages;
}
