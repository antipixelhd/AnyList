export interface WorkProgress {works_current?:number;works_total?:number|null;works_pending?:boolean;pending?:boolean;}
export function workProgress(state:WorkProgress|null|undefined,known:number){
  const current=Number.isInteger(state?.works_current)&&state!.works_current!>=0?state!.works_current!:known;
  const total=Number.isInteger(state?.works_total)&&state!.works_total!>=current?state!.works_total!:null;
  return {current,total,pending:!!(state?.works_pending??state?.pending)};
}
export function mountWorkProgress(root:HTMLElement,signal:AbortSignal){
  const text=root.querySelector<HTMLElement>('[data-contributor-progress]');
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const animations=new Set<Animation>();
  const clear=()=>{for(const animation of animations)animation.cancel();animations.clear();};
  signal.addEventListener('abort',clear,{once:true});reduced.addEventListener('change',clear,{signal});
  const number=(selector:string,value:number)=>{
    const node=text?.querySelector<HTMLElement>(selector);if(!node||node.textContent===String(value))return;
    const previous=node.textContent;node.getAnimations().forEach(animation=>animation.cancel());node.textContent=String(value);
    if(previous&&!reduced.matches&&!signal.aborted){
      const animation=node.animate([{transform:'translateY(5px)',opacity:0},{transform:'translateY(0)',opacity:1}],{duration:180,easing:'ease-out'});
      animations.add(animation);animation.finished.then(()=>animations.delete(animation),()=>animations.delete(animation));
    }
  };
  return (state:WorkProgress|null|undefined,known:number)=>{
    const progress=workProgress(state,known);if(!text)return;
    text.hidden=!progress.pending;number('[data-works-current]',progress.current);
    const denominator=text.querySelector<HTMLElement>('[data-works-denominator]');
    if(denominator)denominator.hidden=progress.total===null;
    if(progress.total!==null)number('[data-works-total]',progress.total);
  };
}
