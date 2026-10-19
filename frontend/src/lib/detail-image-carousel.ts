import {reconcileGalleryChildren} from './detail-gallery-dom';
import {createGallerySpring,gallerySpatial} from './gallery-spring';

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
  const motion=createGallerySpring({left:track.scrollLeft},value=>track.scrollTo({left:value.left,behavior:'instant'}),signal,{
    stiffness:gallerySpatial.fast,precision:{left:.5},
    bounds:()=>({left:{min:0,max:Math.max(0,(count-1)*track.clientWidth)}}),
  });
  const clearNavigation=()=>{navigating=false;delete track.dataset.viewerNavigating;};
  const align=(animate=false)=>{
    if(!motion.active)motion.snap({left:track.scrollLeft});
    navigating=animate&&!reduced.matches;
    if(navigating)track.dataset.viewerNavigating='';
    motion.to({left:current*track.clientWidth},{immediate:!navigating,done:clearNavigation});
  };
  track.addEventListener('scroll',()=>{
    if(frame)return;
    frame=requestAnimationFrame(()=>{
      frame=0;
      if(navigating||!track.clientWidth||!count)return;
      if(track.closest('[data-viewer-zoom-frame]'))return;
      // Prepare the incoming image as soon as the gesture exposes it, before
      // it crosses the selection boundary. Programmatic jumps skip this.
      visibleDetailImageIndices(track.scrollLeft,track.clientWidth,count).forEach(prepare);
      if('viewerSwiping' in track.dataset)return;
      const index=Math.max(0,Math.min(count-1,Math.round(track.scrollLeft/track.clientWidth)));
      if(index!==current){current=index;select(index);}
    });
  },{signal,passive:true});
  const interrupt=()=>{motion.stop();clearNavigation();};
  track.addEventListener('pointerdown',interrupt,{signal,passive:true});
  track.addEventListener('wheel',event=>{if(Math.abs(event.deltaX)>Math.abs(event.deltaY))interrupt();},{signal,passive:true});
  track.closest('dialog')?.addEventListener('close',interrupt,{signal});
  reduced.addEventListener('change',()=>{if(reduced.matches)motion.finish();},{signal});
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
        const image=document.createElement('img');image.draggable=false;image.alt=alt;image.dataset.motionFade='';image.style.visibility='hidden';
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
