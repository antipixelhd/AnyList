import {showDialog,closeDialog,reveal} from './ui-motion';
import {showToast} from './toasts';

type Galleries = {posters:string[]; artwork:string[]};
export function uniqueDetailImages(values:unknown[]):string[] {
  const seen=new Set<string>();
  return values.filter((url):url is string=>{
    if(typeof url!=='string'||!/^https?:\/\//.test(url))return false;
    const key=url.replace(/(https:\/\/image\.tmdb\.org\/t\/p\/)[^/]+\//,'$1');
    if(seen.has(key))return false;
    seen.add(key);return true;
  });
}

export function mountDetailImages(root:HTMLElement,info:any,signal:AbortSignal) {
  const lightbox=root.querySelector<HTMLDialogElement>('#detail-lightbox')!;
  const image=lightbox.querySelector<HTMLImageElement>('[data-detail-lightbox-image]')!;
  const caption=lightbox.querySelector<HTMLElement>('p')!;
  const name=root.querySelector('.lab-heading h1')!.textContent!;
  const galleries:Galleries={posters:uniqueDetailImages(info.posters||[]),artwork:uniqueDetailImages(info.artwork||[])};
  let active:keyof Galleries='artwork',current=0,revision=0;
  const arrows=[...lightbox.querySelectorAll<HTMLButtonElement>('[data-detail-image-step]')];
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
  const loadImages=()=>{
    if(!info.imagesAvailable)return Promise.resolve();
    if(loading)return loading;
    loading=fetch(`/api/proxy/tracking/title/${info.id}/images`,{signal}).then(async response=>{
      if(!response.ok)throw new Error('Image variants unavailable');
      const data=await response.json();
      if(signal.aborted)return;
      const selected=galleries[active][current];
      galleries.posters=uniqueDetailImages([...galleries.posters,...(data.posters||[])]);
      galleries.artwork=uniqueDetailImages([...galleries.artwork,...(data.artwork||[])]);
      const grid=root.querySelector<HTMLElement>('[data-detail-artwork-grid]');
      if(grid){
        const existing=new Set([...grid.querySelectorAll<HTMLElement>('[data-detail-image]')].map(button=>button.dataset.detailImage));
        galleries.artwork.filter(url=>!existing.has(url)).forEach(url=>{
          const button=document.createElement('button');button.type='button';button.dataset.detailImage=url;button.setAttribute('aria-label',`Expand ${name} artwork`);
          const thumbnail=document.createElement('img');thumbnail.src=url;thumbnail.alt=`${name} artwork`;thumbnail.loading='lazy';button.append(thumbnail);grid.append(button);watchLandscape(button);
        });
      }
      if(lightbox.open)showImage(Math.max(0,galleries[active].indexOf(selected)));
    }).catch(()=>{loading=undefined;});
    return loading;
  };
  root.addEventListener('click',event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('[data-detail-image]');
    if(!button)return;
    active=button.dataset.detailGallery==='posters'?'posters':'artwork';
    lightbox.dataset.gallery=active;
    const url=button.dataset.detailImage!;
    if(!galleries[active].includes(url))galleries[active].unshift(url);
    showImage(galleries[active].indexOf(url));showDialog(lightbox);void loadImages();
  },{signal});
  if(new URLSearchParams(location.search).get('tab')==='artwork')void loadImages();
  lightbox.querySelector('[data-detail-close-lightbox]')!.addEventListener('click',()=>{void closeDialog(lightbox);},{signal});
  lightbox.addEventListener('click',event=>{if(event.target===lightbox)void closeDialog(lightbox);},{signal});
  lightbox.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();showImage(current+(event.key==='ArrowLeft'?-1:1));}},{signal});
  arrows.forEach(button=>button.addEventListener('click',()=>showImage(current+Number(button.dataset.detailImageStep)),{signal}));
  return loadImages;
}
