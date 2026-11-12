import {boundedAnimation} from './bounded-animation.ts';
import {cancelUiMotion} from './ui-motion';

export function createAnchoredFieldReveal(dialog:HTMLDialogElement,wrapper:HTMLElement,content:HTMLElement,signal:AbortSignal) {
  const view=dialog.ownerDocument.defaultView!;
  const reduced=view.matchMedia('(prefers-reduced-motion: reduce)');
  let motion:ReturnType<typeof boundedAnimation>|undefined,fade:ReturnType<typeof boundedAnimation>|undefined;
  let anchor:{top:string;bottom:string;max:string}|undefined,target=0,version=0;
  const stop=()=>{version++;motion?.cancel();fade?.cancel();motion=fade=undefined;wrapper.style.removeProperty('height');wrapper.style.removeProperty('overflow');wrapper.hidden=content.hidden;};
  const reset=()=>{
    stop();
    if(anchor){dialog.style.marginTop=anchor.top;dialog.style.marginBottom=anchor.bottom;dialog.style.maxHeight=anchor.max;anchor=undefined;}
    target=content.hidden?0:content.getBoundingClientRect().height;
  };
  const pin=()=>{
    if(anchor)return;
    const top=Math.max(0,dialog.getBoundingClientRect().top);
    cancelUiMotion(dialog,true);
    const max=view.getComputedStyle(dialog).maxHeight;
    anchor={top:dialog.style.marginTop,bottom:dialog.style.marginBottom,max:dialog.style.maxHeight};
    dialog.style.marginTop=`${top}px`;dialog.style.marginBottom='0px';
    dialog.style.maxHeight=`min(${max==='none'?'100dvh':max}, calc(100dvh - ${top+20}px))`;
  };
  function settle(from:number,enter=false){
    stop();const current=version;
    wrapper.hidden=false;
    target=content.hidden?0:content.getBoundingClientRect().height;
    if(reduced.matches||Math.abs(target-from)<.5){wrapper.hidden=content.hidden;return;}
    wrapper.style.height=`${target}px`;wrapper.style.overflow='clip';
    motion=boundedAnimation(wrapper,{height:[`${from}px`,`${target}px`]},320);
    if(enter&&!content.hidden)fade=boundedAnimation(content,{opacity:[.65,1],translate:['0 4px','0 0']},320);
    void motion.finished.then(()=>{if(version===current){motion=undefined;wrapper.style.removeProperty('height');wrapper.style.removeProperty('overflow');wrapper.hidden=content.hidden;}});
  }
  const resize=new ResizeObserver(()=>{
    if(!anchor||!dialog.open||signal.aborted)return;
    const next=content.hidden?0:content.getBoundingClientRect().height;
    if(Math.abs(next-target)>.5)settle(motion?wrapper.getBoundingClientRect().height:target);
  });
  resize.observe(content);
  dialog.addEventListener('close',()=>{if(!dialog.open)reset();},{signal});
  dialog.addEventListener('beforetoggle',event=>{if((event as ToggleEvent).newState==='closed')reset();},{signal});
  view.addEventListener('resize',reset,{signal,passive:true});
  reduced.addEventListener('change',()=>{if(reduced.matches)stop();},{signal});
  signal.addEventListener('abort',()=>{resize.disconnect();reset();},{once:true});
  return {
    change(update:()=>void,animate=false){
      if(!animate||!dialog.open||signal.aborted){stop();update();wrapper.hidden=content.hidden;target=content.hidden?0:content.getBoundingClientRect().height;return;}
      const from=wrapper.hidden?0:wrapper.getBoundingClientRect().height;
      pin();update();settle(from,true);
    },reset,
  };
}
