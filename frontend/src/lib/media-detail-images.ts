import {showDialog,reveal,cancelUiMotion} from './ui-motion';
import {showToast} from './toasts';
import {bindImageViewerDismissal} from './image-viewer-dismissal';
import {detailBackground,type DetailLoadState} from './detail-background';
import {responsiveArtwork,applyResponsiveArtwork,artworkIdentity} from './responsive-artwork';
import {reconcileGalleryChildren,createGalleryThumbnailWatcher,createGalleryImageLoader} from './detail-gallery-dom';
import {createDetailImageCarousel} from './detail-image-carousel';
import {bindDetailImageWheel} from './detail-image-wheel';
import {bindDetailImageDragDismiss} from './detail-image-dismiss';
import {bindDetailImageZoom} from './detail-image-zoom';
import {editorStore} from './editor-store';
import {createDetailViewerImageLoader} from './detail-viewer-image';
import {bindDetailViewerFrame} from './detail-viewer-frame';
import {detailHeroArtwork,detailSceneArtwork} from './detail-artwork';
import {commitDetailHero,nextDetailHero,publishDetailArtwork} from './detail-hero';
import {createGalleryPreviewTimer,galleryPreviewImagesSettled} from './gallery-preview-timer';
import {createGallerySelection} from './gallery-selection';

type Galleries = {posters:string[]; artwork:string[]};
const imageKey=artworkIdentity;
export function uniqueDetailImages(values:unknown[],limit=10):string[] {
  const seen=new Set<string>();
  return values.filter((url):url is string=>{
    if(typeof url!=='string'||!(/^https?:\/\//.test(url)||url.startsWith('/api/proxy/media/image/')))return false;
    const key=imageKey(url);
    if(seen.has(key))return false;
    seen.add(key);return true;
  }).slice(0,limit);
}

export function mountDetailImages(root:HTMLElement,info:any,signal:AbortSignal,standalone?:{lightbox:HTMLDialogElement;logo:boolean;imagesPath?:string}) {
  const lightbox=standalone?.lightbox||root.querySelector<HTMLDialogElement>('#detail-lightbox')!;
  const track=lightbox.querySelector<HTMLElement>('[data-image-viewer-track]')!;
  const prepareViewer=bindDetailImageDragDismiss(lightbox,track,signal);
  const name=info.name||root.querySelector('.lab-heading h1')?.textContent||'Image';
  const initialHero=root.querySelector<HTMLButtonElement>('.lab-hero-art-button')?.dataset.detailImage;
  const artworkLimit=info.type==='game'?Infinity:10;
  const galleries:Galleries&{hero:string[]}={posters:uniqueDetailImages(info.posters||[]),artwork:uniqueDetailImages(info.artwork||[],artworkLimit),hero:initialHero?[initialHero]:[]};
  const galleryLimit=standalone&&!standalone.logo?5:10;
  let pending=!!info.imagesAvailable&&(!info.imageStatus?.state||['pending','deferred'].includes(info.imageStatus.state));
  const slots=lightbox.querySelector<HTMLElement>('[data-image-viewer-slots]')!;
  const selection=createGallerySelection(slots,signal);
  const viewerStatus=lightbox.querySelector<HTMLElement>('[data-image-viewer-status]')!;
  const announcement=lightbox.querySelector<HTMLElement>('[data-image-viewer-announcement]');
  let active:keyof typeof galleries='artwork',current=0;
  const viewerGroup=()=>active==='posters'?'posters':'artwork';
  const viewerPending=()=>pending&&active!=='hero';
  const loadViewerImage=createDetailViewerImageLoader(signal);
  const preparedImages=new WeakMap<HTMLImageElement,string>();
  const watchThumbnail=createGalleryThumbnailWatcher(signal,thumbnail=>{
    if(thumbnail.getBoundingClientRect().top<innerHeight)reveal(thumbnail);
  });
  const loadThumbnail=createGalleryImageLoader(signal,button=>{
    if(button.matches('.lab-gallery button,.lab-art-image'))watchLandscape(button as HTMLButtonElement);
    watchThumbnail(button);
    if(slots.contains(button))refreshPreviewLoading();
  });
  const arrows=[...lightbox.querySelectorAll<HTMLButtonElement>('[data-image-viewer-step]')];
  const mobileViewer=matchMedia('(max-width:650px), (max-height:650px)');
  let previewInteracting=false;
  let previewGesture:{x:number;y:number;moved:boolean}|undefined;
  let pointerPosition:{x:number;y:number}|undefined;
  const previewControls=[slots,...lightbox.querySelectorAll<HTMLElement>('.image-viewer-button,[data-image-viewer-status]')];
  track.tabIndex=-1;
  const previews=createGalleryPreviewTimer(()=>{
      if(!lightbox.open||previewInteracting||'viewerZoomed' in lightbox.dataset||viewerPending())return false;
      if(!mobileViewer.matches&&previewControls.some(control=>control.matches(':hover')))return false;
      // Source-free offscreen thumbnails stay lazy and must not block forever.
      if(!galleryPreviewImagesSettled(slots.querySelectorAll<HTMLImageElement>('img')))return false;
      const selected=carousel.slide(current);
      if(selected&&!selected.skeleton.hidden)return false;
      return true;
  },()=>{delete lightbox.dataset.previewsHidden;},()=>{
    // Keep keyboard navigation inside the modal when its focused controls hide.
    if(lightbox.contains(document.activeElement))track.focus({preventScroll:true});
    lightbox.dataset.previewsHidden='true';
  },signal);
  const clearPreviewTimer=previews.pause;
  const revealPreviews=()=>{
    if(mobileViewer.matches||!pointerPosition||insideViewer(pointerPosition.x,pointerPosition.y))previews.reveal();
  };
  function refreshPreviewLoading(){if(lightbox.open)previews.refresh();}
  const insideViewer=(x:number,y:number)=>{
    const bounds=lightbox.getBoundingClientRect();
    return x>=bounds.left&&x<bounds.right&&y>=bounds.top&&y<bounds.bottom;
  };
  previewControls.forEach(control=>{
    control.addEventListener('pointerenter',refreshPreviewLoading,{signal,passive:true});
    control.addEventListener('pointerleave',refreshPreviewLoading,{signal,passive:true});
  });
  slots.addEventListener('load',refreshPreviewLoading,{signal,capture:true});
  slots.addEventListener('error',refreshPreviewLoading,{signal,capture:true});
  lightbox.addEventListener('pointerdown',event=>{
    if(event.pointerType==='mouse')pointerPosition={x:event.clientX,y:event.clientY};
    // Modal backdrop events also target the dialog, despite being outside it.
    if(!insideViewer(event.clientX,event.clientY))return;
    previewInteracting=true;
    previewGesture={x:event.clientX,y:event.clientY,moved:false};
    clearPreviewTimer();
  },{signal,passive:true});
  window.addEventListener('pointermove',event=>{
    // Layout/visibility changes can repeat the last movement delta. Compare
    // absolute coordinates so a stationary pointer never extends the deadline.
    const moved=!pointerPosition||event.clientX!==pointerPosition.x||event.clientY!==pointerPosition.y;
    pointerPosition={x:event.clientX,y:event.clientY};
    if(moved&&!mobileViewer.matches&&lightbox.open&&insideViewer(event.clientX,event.clientY))revealPreviews();
    if(previewInteracting&&previewGesture&&Math.hypot(event.clientX-previewGesture.x,event.clientY-previewGesture.y)>8)previewGesture.moved=true;
  },{signal,passive:true});
  const releasePreviews=()=>{
    if(!previewInteracting)return;
    previewInteracting=false;
    if(previewGesture?.moved&&(mobileViewer.matches||pointerPosition&&insideViewer(pointerPosition.x,pointerPosition.y)))revealPreviews();else refreshPreviewLoading();
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
      else previews.hide();
    }else revealPreviews();
  },{signal});
  lightbox.addEventListener('focusin',event=>{if(event.target!==track)revealPreviews();},{signal});
  lightbox.addEventListener('focusout',refreshPreviewLoading,{signal});
  // Recentring and inertial scrolling are animation output, not fresh input.
  // Check loading without extending the user's existing idle deadline.
  slots.addEventListener('scroll',refreshPreviewLoading,{signal,passive:true});
  slots.addEventListener('wheel',revealPreviews,{signal,passive:true});
  mobileViewer.addEventListener('change',revealPreviews,{signal});
  lightbox.addEventListener('close',()=>{previews.reset();previewInteracting=false;delete lightbox.dataset.previewsHidden;},{signal});
  window.addEventListener('pagehide',()=>{previews.reset();previewInteracting=false;previewGesture=undefined;},{signal});
  window.addEventListener('pageshow',()=>{previewInteracting=false;previewGesture=undefined;if(lightbox.open)revealPreviews();},{signal});
  signal.addEventListener('abort',clearPreviewTimer,{once:true});
  const carousel=createDetailImageCarousel(track,signal,index=>showImage(index,false,false),index=>prepareImage(index));
  const resetZoom=bindDetailImageZoom(lightbox,track,signal,()=>carousel.slide(current)?.image,
    (index,preserveScroll=false,preserveZoom=false)=>showImage(index,false,!preserveScroll,preserveZoom),()=>{previewInteracting=false;revealPreviews();},()=>cancelUiMotion(lightbox));
  const fitViewer=bindDetailViewerFrame(lightbox,signal,!!standalone);
  let followedSlot:string|undefined;
  const paintSlots=()=>{
    if(!lightbox.open)return;
    const rows=galleries[active],count=viewerPending()?galleryLimit:rows.length;
    slots.hidden=!viewerPending()&&count<=1;
    if(slots.hidden){
      selection.update(undefined);slots.replaceChildren();followedSlot=undefined;refreshPreviewLoading();return;
    }
    const existing=new Map([...slots.querySelectorAll<HTMLButtonElement>('button')].map(button=>[button.dataset.slotKey,button]));
    const buttons=Array.from({length:count},(_,index)=>{
      const key=`${active}:${rows[index]?imageKey(rows[index]):`pending:${index}`}`;
      let button=existing.get(key);
      if(!button){
        button=document.createElement('button');button.type='button';button.dataset.slotKey=key;
        if(rows[index]){const thumbnail=new Image();thumbnail.alt='';thumbnail.dataset.gallerySrc=responsiveArtwork(rows[index],{size:'w185',route:'auto'})?.src||rows[index];button.append(thumbnail);}
        else button.className='detail-image-skeleton';
        button.addEventListener('click',()=>{
          const index=Number(button!.dataset.imageIndex);
          prepareImage(index);
          if(!resetZoom.preview(index,current))showImage(index,true,true,false,true);
        },{signal});
      }
      button.dataset.imageIndex=String(index);
      button.setAttribute('aria-label',`${active==='posters'?'Poster':'Artwork'} ${index+1}${rows[index]?'':': loading'}`);
      button.setAttribute('aria-current',String(current===index));loadThumbnail(button);return button;
    });
    reconcileGalleryChildren(slots,[selection.element,...buttons]);
    refreshPreviewLoading();
    const selected=buttons[current];
    selection.update(selected);
    if(selected&&selected.dataset.slotKey!==followedSlot){
      followedSlot=selected.dataset.slotKey;
      const bounds=slots.getBoundingClientRect(),buttonBounds=selected.getBoundingClientRect();
      slots.scrollTo({left:slots.scrollLeft+buttonBounds.left-bounds.left-(slots.clientWidth-buttonBounds.width)/2,
        behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth'});
    }
  };
  const showImage=(index:number,animate=false,scroll=true,preserveZoom=false,fast=false)=>{
    if(!preserveZoom){if(scroll)resetZoom();else resetZoom.preserveInteraction();}
    const rows=galleries[active];
    const count=viewerPending()?galleryLimit:rows.length;
    const previous=current;
    current=count?(index+count)%count:0;
    if(current!==previous)revealPreviews();
    const group=active;
    const alt=`${name} · ${group==='posters'?'Poster':'Artwork'} ${current+1}`;
    if(announcement)announcement.textContent=count?`${group==='posters'?'Poster':'Artwork'} ${current+1}${viewerPending()?'':` of ${count}`}`:'No images available';
    const {image,skeleton}=carousel(rows,url=>`${group}:${imageKey(url)}`,count,current,alt,scroll,animate,fast);
    if(!preserveZoom)fitViewer(image,viewerGroup());
    arrows.forEach(arrow=>{arrow.hidden=count<2;arrow.disabled=count<2;});
    paintSlots();
    if(!rows[current]){
      image.style.visibility='hidden';skeleton.hidden=false;
      skeleton.classList.toggle('detail-image-skeleton',viewerPending());
      skeleton.textContent=viewerPending()?'':'No images available.';
      return;
    }
    prepareImage(current);
  };
  const prepareImage=(index:number)=>{
    if(!lightbox.open||signal.aborted)return;
    const group=active,original=galleries[group][index];
    if(!original)return;
    const url=responsiveArtwork(original,{size:group==='posters'?'w780':'original',route:'auto'})?.src;
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
      if(!standalone&&group!=='posters'&&image.naturalWidth<=image.naturalHeight){discard();return;}
      if(lightbox.open&&active===group&&galleries[group][current]===original)fitViewer(image,viewerGroup());
      if(!cached&&lightbox.open&&active===group&&galleries[group][current]===original)reveal(image);
      refreshPreviewLoading();
    }).catch(()=>{
      if(signal.aborted)return;
      if(discard())showToast('Unable to load this image. Retry the gallery.','error');
      refreshPreviewLoading();
    });
  };
  const watchLandscape=(button:HTMLButtonElement)=>{
    // Games retain the provider's complete gallery. Scene selection is based
    // on typed source evidence; a square extra must not remove a usable panel.
    if(info.type==='game')return;
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
  let previewInfo=info.artworkPreview as {image?:string;thumbnail?:string}|undefined;
  let thumbnails:Record<string,string>=info.artworkThumbnails||{};
  const paintPreview=()=>{
    const button=root.querySelector<HTMLButtonElement>('.lab-art-image');
    const image=button?.querySelector('img');
    if(!button||!image)return;
    const url=previewInfo?.image||galleries.artwork[0];
    if(!url)return;
    button.closest<HTMLElement>('.lab-art-preview')?.removeAttribute('hidden');
    // Enrichment must not replace an already displayed full image with its thumbnail.
    if(image.complete&&image.naturalWidth&&imageKey(button.dataset.detailImage||'')===imageKey(url))return;
    button.dataset.detailImage=url;
    const art=detailSceneArtwork(url,null,'auto',previewInfo?.image===url?previewInfo.thumbnail:thumbnails[url]);
    const hero=root.querySelector<HTMLImageElement>('.lab-hero-image');
    if(hero&&imageKey(hero.currentSrc||hero.src)===imageKey(url)){
      image.removeAttribute('srcset');image.removeAttribute('sizes');image.src=hero.currentSrc||hero.src;
    }else if(art&&image.getAttribute('src')!==art.src)applyResponsiveArtwork(image,art.src,{size:'w780',route:'auto'});
    watchThumbnail(button);
  };
  if(info.type==='game'){
    const image=root.querySelector<HTMLImageElement>('.lab-art-image img');
    const failed=new Set<string>();
    const recover=()=>{
      if(signal.aborted||!image)return;
      failed.add(imageKey(image.currentSrc||image.src));
      const next=(info.backdropFallbacks||[]).find((url:string)=>!failed.has(imageKey(url)));
      if(next){previewInfo={image:next};paintPreview();}
      else root.querySelector<HTMLElement>('.lab-art-preview')?.setAttribute('hidden','');
    };
    image?.addEventListener('error',recover,{signal});
    if(image?.complete&&image.currentSrc&&!image.naturalWidth)recover();
  }
  let loadPreview=()=>Promise.resolve();
  const failedHeroes=new Set<string>();
  let fallback=info.backdropFallback as string|undefined;
  let heroRevision=0;
  const paintHero=(url:string|undefined)=>{
    if(!url)return;
    if(failedHeroes.has(imageKey(url)))return;
    const hero=root.querySelector<HTMLImageElement>('.lab-hero-image');
    const button=root.querySelector<HTMLButtonElement>('.lab-hero-art-button');
    if(!hero)return;
    const sameImage=imageKey(hero.src)===imageKey(url);
    const revision=++heroRevision;
    const candidate=sameImage?hero:new Image();
    const commit=()=>{
      if(signal.aborted||revision!==heroRevision)return;
      commitDetailHero(hero,candidate,url,button,backdrop=>{
        galleries.hero=[backdrop];
        publishDetailArtwork(editorStore,info,{backdrop});
      });
    };
    const failed=()=>{
      if(signal.aborted||revision!==heroRevision)return;
      failedHeroes.add(imageKey(url));
      if(!hero.naturalWidth)hero.hidden=true;
      const recover=()=>{
        if(signal.aborted)return;
        const next=nextDetailHero([previewInfo?.image,fallback,...(info.backdropFallbacks||[])],failedHeroes);
        if(next){paintHero(next);return;}
        if(!hero.naturalWidth){hero.hidden=true;if(button)button.hidden=true;}
      };
      if(info.type==='game'||previewInfo?.image||fallback)recover();else void loadPreview().then(recover);
    };
    candidate.onload=()=>{void candidate.decode().then(commit,failed);};
    candidate.onerror=failed;
    if(sameImage){if(hero.complete){if(hero.naturalWidth)commit();else failed();}return;}
    const artwork=detailHeroArtwork(url);
    candidate.fetchPriority='high';candidate.decoding='async';
    if(artwork?.srcset)candidate.srcset=artwork.srcset;
    candidate.sizes=artwork?.sizes||'100vw';candidate.src=artwork?.src||url;
  };
  const applyImages=(data:Galleries&{poster?:string;backdrop?:string;backdrop_fallback?:string;artwork_thumbnails?:Record<string,string>;image_status?:DetailLoadState;image_replacements?:Record<string,string>})=>{
      const selected=galleries[active][current];
      galleries.posters=uniqueDetailImages(standalone?[info.posters?.[0],...(data.image_status?.state==='deferred'?galleries.posters:data.posters||[])]:data.posters||[]).slice(0,galleryLimit);
      galleries.artwork=uniqueDetailImages(data.artwork||[],artworkLimit);
      if(info.type!=='game'&&lightbox.open&&active==='artwork'&&selected&&selected===previewInfo?.image&&!galleries.artwork.includes(selected))galleries.artwork.unshift(selected);
      thumbnails={...thumbnails,...data.artwork_thumbnails};
      pending=!!data.image_status&&['pending','deferred'].includes(data.image_status.state||'');
      if(galleryStatus){galleryStatus.hidden=!!galleries.artwork.length;galleryStatus.textContent='No artwork available.';}
      const cover=root.querySelector<HTMLButtonElement>('.media-cover');
      const primary=data.poster||galleries.posters[0];
      if(cover&&primary){
        cover.dataset.detailImage=primary;
        const coverImage=cover.querySelector('img');
        if(coverImage&&imageKey(coverImage.src)!==imageKey(primary))applyResponsiveArtwork(coverImage,primary,{size:'w500',sizes:coverImage.sizes||'(max-width:650px) 108px, 210px',route:'auto'});
      }
      fallback=data.backdrop_fallback||fallback;
      paintHero(data.backdrop);
      if(!standalone)publishDetailArtwork(editorStore,info,{poster:primary});
      paintPreview();
      const grid=root.querySelector<HTMLElement>('[data-detail-artwork-grid]');
      if(grid){
        const existing=new Map([...grid.querySelectorAll<HTMLButtonElement>('[data-detail-image]')].map((button,index)=>[
          button.dataset.detailImage?imageKey(button.dataset.detailImage):`pending:${index}`,button]));
        const buttons=Array.from({length:pending?galleryLimit:galleries.artwork.length},(_,index)=>{
          const url=galleries.artwork[index];
          let button=existing.get(url?imageKey(url):`pending:${index}`);
          if(!button){
            button=document.createElement('button');button.type='button';button.dataset.detailImage=url||'';
            if(url){const thumbnail=document.createElement('img');thumbnail.dataset.gallerySrc=detailSceneArtwork(url,null,'auto',thumbnails[url])?.src||url;thumbnail.alt=`${name} artwork`;button.append(thumbnail);}
            else button.className='detail-image-skeleton';
          }
          button.setAttribute('aria-label',`Expand ${name} artwork ${index+1}`);loadThumbnail(button);return button;
        });
        reconcileGalleryChildren(grid,buttons);
      }
      const preferred=data.image_replacements?.[selected]||selected;
      if(lightbox.open){
        const index=preferred?galleries[active].findIndex(url=>imageKey(url)===imageKey(preferred)):-1;
        showImage(index>=0?index:Math.min(current,Math.max(0,(viewerPending()?galleryLimit:galleries[active].length)-1)));
      }
      return data.image_status;
  };
  root.addEventListener('anylist:game-primary',event=>{
    const data=(event as CustomEvent).detail;
    if(signal.aborted||info.type!=='game'||data?.type!=='game'||data.id!==info.id)return;
    previewInfo=data.artwork_preview;info.backdropFallbacks=data.backdrop_fallbacks||info.backdropFallbacks;
    applyImages({...data,
      posters:uniqueDetailImages([...(data.posters||[]),...galleries.posters]),
      artwork:uniqueDetailImages(data.artwork||galleries.artwork,artworkLimit),image_status:{state:'ready'}});
    const tab=root.querySelector<HTMLButtonElement>('[data-detail-tab="artwork"]');
    if(tab){
      const empty=!galleries.artwork.length,focused=document.activeElement===tab;
      if(empty&&tab.getAttribute('aria-selected')==='true')root.querySelector<HTMLButtonElement>('[data-detail-tab="overview"]')?.click();
      tab.hidden=empty;tab.disabled=empty;if(empty)tab.tabIndex=-1;
      if(empty&&focused)root.querySelector<HTMLButtonElement>('[data-detail-tab="overview"]')?.focus({preventScroll:true});
    }
  },{signal});
  const resource=detailBackground(standalone?.imagesPath||`/api/proxy/tracking/title/${info.id}/images`,signal,applyImages,(state,error)=>{
    viewerStatus.hidden=state!=='retry';
    if(state==='retry'){
      viewerStatus.replaceChildren(document.createTextNode(error||'Images are temporarily unavailable. Retrying automatically.'));
      const button=document.createElement('button');button.type='button';button.className='lab-text-button';button.textContent='Retry';button.addEventListener('click',()=>void resource.retry(),{signal});viewerStatus.append(' ',button);
    }
    if(galleryStatus){galleryStatus.hidden=state!=='retry'&&(!!galleries.artwork.length||pending);galleryStatus.textContent=state==='retry'?error||'Artwork is temporarily unavailable. Retrying automatically.':'No artwork available.';}
  });
  const loadImages=()=>info.imagesAvailable?resource.load():Promise.resolve();
  const previewResource=detailBackground<{artwork_preview?:{image?:string;thumbnail?:string};preview_status?:DetailLoadState}>(
    `/api/proxy/tracking/title/${info.id}/artwork-preview`,signal,data=>{
      if(data.artwork_preview?.image){
        previewInfo=data.artwork_preview;
        if(!galleries.artwork.length)galleries.artwork=[data.artwork_preview.image];
        paintPreview();
      }else if(!galleries.artwork.length){
        const button=root.querySelector<HTMLButtonElement>('.lab-art-image');
        button?.classList.remove('detail-image-skeleton');
        if(button)button.closest<HTMLElement>('.lab-art-preview')!.hidden=true;
      }
      return data.preview_status;
    },()=>{});
  loadPreview=()=>previewResource.load();
  if(pending)applyImages({...galleries,image_status:{state:'pending'}});
  if(initialHero)paintHero(initialHero);
  if(info.heroAvailable){
    // One preview is visible content; full gallery analysis still waits for intent.
    if(info.previewStatus?.state!=='ready')void loadPreview();
    else if(!previewInfo?.image&&!galleries.artwork.length)root.querySelector<HTMLElement>('.lab-art-preview')?.setAttribute('hidden','');
  }
  root.addEventListener('click',event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>(standalone?'[data-portrait-image]':'[data-detail-image]');
    if(!button||button.disabled)return;
    active=button.matches('.lab-hero-art-button')?'hero':button.dataset.detailGallery==='posters'||(standalone&&!standalone.logo)?'posters':'artwork';
    if(active==='hero')galleries.hero=[button.dataset.detailImage!];
    lightbox.dataset.gallery=viewerGroup();
    const url=(standalone?button.dataset.portraitImage:button.dataset.detailImage)!;
    if(active==='artwork'&&url&&!galleries.artwork.includes(url))galleries.artwork.unshift(url);
    // A portrait may arrive after initialization. Keep that primary image in
    // the standalone gallery before opening or requesting optional extras.
    if(standalone&&url){
      if(!galleries[active].some(value=>imageKey(value)===imageKey(url)))galleries[active].unshift(url);
      info[active]=[url];
    }
    const gridIndex=[...root.querySelectorAll('[data-detail-artwork-grid] button')].indexOf(button);
    followedSlot=undefined;
    prepareViewer();
    fitViewer(undefined,viewerGroup());
    showDialog(lightbox);previews.reveal();showImage(gridIndex>=0?gridIndex:Math.max(0,galleries[active].findIndex(value=>imageKey(value)===imageKey(url))));if(active!=='hero')void loadImages();
  },{signal});
  // The hero is critical: start its small metadata read immediately. Full
  // gallery curation and extra image downloads wait for a gallery interaction.
  if(info.heroAvailable){
    const background=detailBackground<{backdrop?:string;backdrop_fallback?:string;background_status?:DetailLoadState}>(
      `/api/proxy/tracking/title/${info.id}/background`,signal,data=>{
        fallback=data.backdrop_fallback||fallback;
        if(data.backdrop)paintHero(data.backdrop);
        return data.background_status;
      },()=>{});
    if(!initialHero||info.backgroundStatus?.state!=='ready')void background.load();
    const artworkPanel=root.querySelector<HTMLElement>('[data-detail-panel="artwork"]');
    if(info.imagesAvailable&&artworkPanel&&!artworkPanel.hidden)void loadImages();
  }
  bindImageViewerDismissal(lightbox,signal);
  bindDetailImageWheel(lightbox,track,signal,direction=>showImage(Math.max(0,Math.min(galleries[active].length-1,current+direction)),true,true,false,true));
  lightbox.addEventListener('keydown',event=>{
    if(event.key==='Tab')previews.reveal();
    if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();showImage(current+(event.key==='ArrowLeft'?-1:1),true);}
  },{signal});
  arrows.forEach(button=>button.addEventListener('click',()=>showImage(current+Number(button.dataset.imageViewerStep),true),{signal}));
  return loadImages;
}
