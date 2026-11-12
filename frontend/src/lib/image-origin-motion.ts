import {boundedAnimation} from './bounded-animation.ts';
import {registerDialogMotion} from './ui-motion';

type Box={left:number;top:number;width:number;height:number};
export function containedImageBox(bounds:Box,width:number,height:number):Box {
  if(!width||!height)return bounds;
  const scale=Math.min(bounds.width/width,bounds.height/height),w=width*scale,h=height*scale;
  return {left:bounds.left+(bounds.width-w)/2,top:bounds.top+(bounds.height-h)/2,width:w,height:h};
}
export function imageOriginTransform(from:Box,to:Box){
  return `translate(${from.left-to.left}px, ${from.top-to.top}px) scale(${from.width/to.width}, ${from.height/to.height})`;
}
export function createImageOriginMotion(dialog:HTMLDialogElement,track:HTMLElement,signal:AbortSignal,selected:()=>HTMLImageElement|undefined,key:()=>string) {
  const doc=dialog.ownerDocument,view=doc.defaultView!,reduced=view.matchMedia('(prefers-reduced-motion: reduce)');
  let origin:{button:HTMLElement;image:HTMLImageElement;box:Box;key:string;url:string}|undefined;
  let bridge:HTMLElement|undefined,version=0,overflow='',trackOpacity='';
  let controls:Array<ReturnType<typeof boundedAnimation>>=[];
  const faded=new Map<HTMLElement,string>();
  const valid=(box:Box)=>box.width>0&&box.height>0&&box.left<view.innerWidth&&box.top<view.innerHeight&&box.left+box.width>0&&box.top+box.height>0;
  const clear=()=>{
    version++;for(const control of controls)control.cancel();controls=[];
    if(bridge){bridge.remove();bridge=undefined;dialog.style.overflow=overflow;track.style.opacity=trackOpacity;}
    for(const [element,opacity] of faded)element.style.opacity=opacity;faded.clear();
    dialog.style.removeProperty('opacity');delete dialog.dataset.viewerOrigin;
  };
  const closed=()=>{clear();const button=origin?.button;if(button?.isConnected)button.focus({preventScroll:true});};
  const fade=(entering:boolean)=>{
    if(reduced.matches)return Promise.resolve();
    if(!entering)dialog.style.opacity='0';
    const control=boundedAnimation(dialog,{opacity:entering?[.85,1]:[1,0]},entering?180:120);controls.push(control);return control.finished;
  };
  const artBox=(image:HTMLImageElement)=>containedImageBox(image.getBoundingClientRect(),image.naturalWidth||origin?.image.naturalWidth||0,image.naturalHeight||origin?.image.naturalHeight||0);
  function fly(entering:boolean){
    if(!origin||!valid(origin.box)||!origin.url)return fade(entering);
    const image=selected();if(!image)return fade(entering);
    const destination=artBox(image),source=entering?origin.box:origin.image.getBoundingClientRect();
    if(!valid(destination)||!valid(source))return fade(entering);
    overflow=dialog.style.overflow;trackOpacity=track.style.opacity;
    dialog.style.overflow='visible';track.style.opacity='0';dialog.dataset.viewerOrigin='';
    bridge=doc.createElement('div');bridge.className='image-viewer-origin';bridge.setAttribute('aria-hidden','true');
    Object.assign(bridge.style,{position:'fixed',pointerEvents:'none',left:`${destination.left}px`,top:`${destination.top}px`,width:`${destination.width}px`,height:`${destination.height}px`,backgroundImage:`url(${JSON.stringify(entering?origin.url:(image.complete&&image.naturalWidth?image.currentSrc||image.src:origin.url))})`,backgroundSize:'cover',backgroundPosition:'center',borderRadius:'5px',transformOrigin:'0 0',zIndex:'4'});
    const small=imageOriginTransform(source,destination),full='translate(0px, 0px) scale(1, 1)';
    bridge.style.transform=entering?full:small;dialog.append(bridge);
    const motion=boundedAnimation(bridge,{transform:entering?[small,full]:[full,small]},entering?360:270);controls.push(motion);
    dialog.querySelectorAll<HTMLElement>('.image-viewer-button,.image-viewer-slots').forEach(control=>{
      if(!entering){faded.set(control,control.style.opacity);control.style.opacity='0';}
      controls.push(boundedAnimation(control,{opacity:entering?[0,1]:[1,0]},entering?240:150));
    });
    return motion.finished;
  }
  registerDialogMotion(dialog,{
    enter(){
      clear();const current=version;
      queueMicrotask(()=>{
        if(signal.aborted||!dialog.open||current!==version)return;
        if(reduced.matches)return;
        void fly(true).then(()=>{if(current===version)clear();});
      });
    },
    async exit(){
      const opening=bridge?.getBoundingClientRect();clear();
      if(reduced.matches)return;
      if(!origin||origin.key!==key()||!origin.image.isConnected||dialog.hasAttribute('data-viewer-zoomed')){await fade(false);return;}
      origin.box=origin.image.getBoundingClientRect();
      if(!valid(origin.box)){await fade(false);return;}
      const current=version,pending=fly(false);
      if(opening&&bridge){const final=bridge.style.transform;controls[0]?.cancel();const motion=boundedAnimation(bridge,{transform:[imageOriginTransform(opening,{left:parseFloat(bridge.style.left),top:parseFloat(bridge.style.top),width:parseFloat(bridge.style.width),height:parseFloat(bridge.style.height)}),final]},270);controls.push(motion);await motion.finished;}
      else await pending;
      if(version!==current)return;
    },cancel:clear,closed,
  },signal);
  const interrupt=()=>{if(!dialog.hasAttribute('data-motion-closing'))clear();};
  track.addEventListener('pointerdown',interrupt,{signal,passive:true});track.addEventListener('wheel',interrupt,{signal,passive:true});
  dialog.querySelector<HTMLElement>('[data-image-viewer-slots]')?.addEventListener('click',interrupt,{signal,capture:true});
  dialog.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'||event.key==='ArrowRight')interrupt();},{signal});
  dialog.querySelectorAll('[data-image-viewer-step]').forEach(button=>button.addEventListener('click',interrupt,{signal}));
  dialog.addEventListener('beforetoggle',event=>{if((event as ToggleEvent).newState==='closed')clear();},{signal});
  dialog.addEventListener('close',()=>{if(!dialog.open)closed();},{signal});
  view.addEventListener('resize',clear,{signal,passive:true});view.addEventListener('pagehide',clear,{signal});
  reduced.addEventListener('change',()=>{if(reduced.matches)clear();},{signal});
  signal.addEventListener('abort',clear,{once:true});
  return (button:HTMLElement,image:HTMLImageElement|undefined,identity:string)=>{
    clear();origin=image?.complete&&image.naturalWidth?{button,image,box:image.getBoundingClientRect(),key:identity,url:image.currentSrc||image.src}:undefined;
  };
}
