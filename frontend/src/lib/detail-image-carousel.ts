import {reconcileGalleryChildren} from './detail-gallery-dom';

/** Scroll-backed slides share decoded images; phone touch arbitration lives in detail-image-zoom. */
export function visibleDetailImageIndices(left:number,width:number,count:number) {
  if(width<=0||count<=0)return [];
  const start=Math.max(0,Math.min((count-1)*width,left));
  const first=Math.floor(start/width),last=Math.min(count-1,Math.ceil((start+width)/width)-1);
  return Array.from({length:last-first+1},(_,i)=>first+i)
    .filter(index=>Math.min(start+width,(index+1)*width)-Math.max(start,index*width)>=8);
}

export function createDetailImageCarousel(track:HTMLElement,signal:AbortSignal,select:(index:number)=>void,prepare:(index:number)=>void=()=>{}) {
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  let current=0,count=0,frame=0,navigating=false;
  const align=(animate=false)=>{
    navigating=animate&&!reduced.matches;
    track.scrollTo({left:current*track.clientWidth,behavior:navigating?'smooth':'instant'});
  };
  track.addEventListener('scroll',()=>{
    if(frame)return;
    frame=requestAnimationFrame(()=>{
      frame=0;
      if(navigating||!track.clientWidth||!count)return;
      // Prepare the incoming image as soon as the gesture exposes it, before
      // it crosses the selection boundary. Programmatic jumps skip this.
      visibleDetailImageIndices(track.scrollLeft,track.clientWidth,count).forEach(prepare);
      const index=Math.max(0,Math.min(count-1,Math.round(track.scrollLeft/track.clientWidth)));
      if(index!==current){current=index;select(index);}
    });
  },{signal,passive:true});
  track.addEventListener('scrollend',()=>{navigating=false;},{signal});
  const interrupt=()=>{navigating=false;};
  track.addEventListener('pointerdown',interrupt,{signal,passive:true});
  track.addEventListener('wheel',interrupt,{signal,passive:true});
  const resize=new ResizeObserver(()=>{if(track.clientWidth)align();});
  resize.observe(track);
  signal.addEventListener('abort',()=>{resize.disconnect();cancelAnimationFrame(frame);},{once:true});

  const render=(rows:string[],key:(url:string)=>string,total:number,index:number,alt:string,scroll=true,animate=false)=>{
    current=index;count=total;
    const existing=new Map([...track.children].map(slide=>[(slide as HTMLElement).dataset.imageKey,slide as HTMLElement]));
    const slides=Array.from({length:Math.max(1,total)},(_,position)=>{
      const identity=rows[position]?key(rows[position]):`pending:${position}`;
      let slide=existing.get(identity);
      if(!slide){
        slide=document.createElement('div');slide.className='image-viewer-slide';slide.dataset.imageKey=identity;
        const image=document.createElement('img');image.draggable=false;image.alt=alt;image.style.visibility='hidden';
        const loading=document.createElement('div');loading.className='image-viewer-slide-loading detail-image-skeleton';
        loading.setAttribute('aria-label','Loading image');loading.setAttribute('role','status');
        slide.append(image,loading);
      }
      slide.setAttribute('aria-hidden',String(position!==index));
      // Only selected or gesture-exposed images receive a source. Keep visited
      // elements attached so returning to them reuses the browser's cache.
      return slide;
    });
    reconcileGalleryChildren(track,slides);
    if(scroll)align(animate);
    const selected=slides[index]||slides[0];
    return {image:selected.querySelector<HTMLImageElement>('img')!,skeleton:selected.querySelector<HTMLElement>('.image-viewer-slide-loading')!};
  };
  return Object.assign(render,{slide:(index:number)=>{
    const slide=track.children[index];
    return slide?{image:slide.querySelector<HTMLImageElement>('img')!,skeleton:slide.querySelector<HTMLElement>('.image-viewer-slide-loading')!}:undefined;
  }});
}
