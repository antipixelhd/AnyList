import {createGallerySpring,gallerySpatial} from './gallery-spring';

/** Direct dragging and spring settling share the same position and momentum. */
export function createDetailImageSwipe(track:HTMLElement,signal:AbortSignal,select:(index:number,preserveScroll?:boolean)=>void) {
  const reduced=matchMedia('(prefers-reduced-motion:reduce)');
  let start=0,index=0;
  const motion=createGallerySpring({left:0},value=>{track.scrollLeft=value.left;},signal,{stiffness:gallerySpatial.fast,
    bounds:()=>({left:{min:0,max:Math.max(0,(track.children.length-1)*track.clientWidth)}})});
  reduced.addEventListener('change',()=>{if(reduced.matches)motion.finish();},{signal});
  const nearest=()=>Math.max(0,Math.min(track.children.length-1,Math.round(track.scrollLeft/Math.max(1,track.clientWidth))));
  const cancel=(commit=false)=>{
    motion.stop();const active='viewerSwiping' in track.dataset;delete track.dataset.viewerSwiping;
    if(active&&!commit){track.scrollLeft=index*track.clientWidth;motion.snap({left:track.scrollLeft});}
    if(active&&commit){index=nearest();select(index,true);}
    return active;
  };
  const begin=()=>{
    cancel(true);index=nearest();start=track.scrollLeft;motion.snap({left:start});track.dataset.viewerSwiping='';
  };
  const move=(dx:number)=>motion.drag({left:Math.max(0,Math.min((track.children.length-1)*track.clientWidth,start-dx))});
  const finish=(dx:number,velocity:number,cancelled=false)=>{
    track.dataset.viewerSwiping='';
    const deliberate=Math.abs(dx)>=Math.max(40,track.clientWidth*.15)||(Math.abs(dx)>=16&&Math.abs(velocity)>=.5);
    const destination=Math.max(0,Math.min(track.children.length-1,index+(!cancelled&&deliberate?(dx<0?1:-1):0)));
    motion.to({left:destination*track.clientWidth},{velocity:{left:cancelled?0:-velocity*1000},immediate:reduced.matches,done:()=>{
      delete track.dataset.viewerSwiping;select(destination);
    }});
  };
  signal.addEventListener('abort',()=>cancel(),{once:true});return {begin,move,finish,cancel};
}
