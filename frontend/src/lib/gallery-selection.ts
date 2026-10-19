import {createGallerySpring, gallerySpatial} from './gallery-spring';

/** Bounds in strip content coordinates: scrolling never changes the frame's target. */
export function gallerySelectionBounds(x:number,y:number,width:number,height:number,scale:number,padding:number) {
  return {
    x:x-(width*(scale-1)/2)-padding,
    y:y-(height*(scale-1)/2)-padding,
    width:width*scale+padding*2,
    height:height*scale+padding*2,
  };
}

export function galleryBarBounds(firstLeft:number,lastRight:number,top:number,height:number,padding:number,scroll:number,viewportWidth:number) {
  const left=Math.max(scroll,firstLeft-padding);
  const right=Math.min(scroll+viewportWidth,lastRight+padding);
  return {x:left,y:top-padding,width:Math.max(0,right-left),height:height+padding*2};
}

/** Same-winding shapes form one clipping region, so translucent fills never overlap. */
function roundedRect(x:number,y:number,width:number,height:number,radius:number) {
  const right=x+width,bottom=y+height,r=Math.min(radius,width/2,height/2);
  return `M ${x} ${y+r} Q ${x} ${y} ${x+r} ${y} H ${right-r} Q ${right} ${y} ${right} ${y+r} V ${bottom-r} Q ${right} ${bottom} ${right-r} ${bottom} H ${x+r} Q ${x} ${bottom} ${x} ${bottom-r} Z`;
}

export function createGallerySelection(strip:HTMLElement,signal:AbortSignal) {
  const element=document.createElement('span');
  element.className='image-viewer-selection';
  element.setAttribute('aria-hidden','true');
  element.hidden=true;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  let selected:HTMLElement|undefined,placed=false;
  let target:ReturnType<typeof gallerySelectionBounds>|undefined;
  let scroll=0,firstLeft=0,lastRight=0,top=0,height=0,padding=0,viewportWidth=0;
  const paint=(bounds:ReturnType<typeof gallerySelectionBounds>)=>{
    const bar=galleryBarBounds(firstLeft,lastRight,top,height,padding,scroll,viewportWidth);
    element.style.clipPath=`path("${roundedRect(bar.x,bar.y,bar.width,bar.height,8)} ${roundedRect(bounds.x,bounds.y,bounds.width,bounds.height,9)}")`;
  };
  const motion=createGallerySpring({x:0,y:0,width:0,height:0},paint,signal,{stiffness:gallerySpatial.fast});
  const update=(button:HTMLElement|undefined,immediate=false)=>{
    if(signal.aborted)return;
    selected=button;
    if(!selected||strip.hidden||!selected.offsetWidth){reset();return;}
    const style=getComputedStyle(strip);
    padding=parseFloat(style.getPropertyValue('--preview-selection-padding'))||0;
    const buttons=strip.querySelectorAll<HTMLElement>('button');
    const last=buttons[buttons.length-1];
    viewportWidth=strip.clientWidth;scroll=strip.scrollLeft;
    firstLeft=buttons[0].offsetLeft;lastRight=last.offsetLeft+last.offsetWidth;
    top=selected.offsetTop;height=selected.offsetHeight;
    element.style.width=`${Math.max(viewportWidth,last.offsetLeft+last.offsetWidth+(parseFloat(style.paddingRight)||0))}px`;
    element.style.height=`${strip.clientHeight}px`;
    const next=gallerySelectionBounds(selected.offsetLeft,selected.offsetTop,selected.offsetWidth,selected.offsetHeight,
      parseFloat(style.getPropertyValue('--preview-selected-scale'))||1,
      padding);
    element.hidden=false;
    if(!placed||immediate||reduced.matches){motion.snap(next);placed=true;}
    else if(!target||Object.keys(next).some(key=>next[key as keyof typeof next]!==target![key as keyof typeof next]))motion.to(next);
    target=next;
  };
  const reset=()=>{motion.stop();placed=false;target=undefined;element.hidden=true;};
  const observer=new ResizeObserver(()=>update(selected,true));
  observer.observe(strip);
  strip.addEventListener('scroll',()=>{scroll=strip.scrollLeft;if(placed)paint(motion.read().value);},{signal,passive:true});
  reduced.addEventListener('change',()=>{if(reduced.matches&&motion.active)motion.finish();},{signal});
  strip.closest('dialog')?.addEventListener('close',reset,{signal});
  signal.addEventListener('abort',()=>{observer.disconnect();element.remove();},{once:true});
  return {element,update,reset};
}
