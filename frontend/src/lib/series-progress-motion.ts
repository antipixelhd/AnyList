import {animate} from 'motion';

/** Keep visual progress continuous while episode values remain whole numbers. */
export function createProgressMotion(signal: AbortSignal) {
  let values = new WeakMap<HTMLElement, {value:number; target:number}>();
  const running = new Map<HTMLElement, ReturnType<typeof animate>>();
  let dragging = new WeakSet<HTMLElement>();
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const stop = (element:HTMLElement) => {
    running.get(element)?.stop();
    running.delete(element);
  };
  const fill = (element:HTMLElement, percent:number, immediate=false) => {
    if (signal.aborted || (dragging.has(element) && !immediate)) return;
    const target = Math.max(0,Math.min(100,percent));
    const previous = values.get(element);
    if (previous?.target===target && !immediate) return;
    stop(element);
    const state = {value:previous?.value??target,target};
    values.set(element,state);
    const paint = (value:number) => {
      state.value=value;
      element.style.setProperty('--progress',`${value}%`);
    };
    if (!previous || immediate || reduced.matches || !element.getClientRects().length) {
      paint(target);
      return;
    }
    const control = animate(state.value,target,{duration:.18,ease:[.2,.7,.2,1],onUpdate:paint});
    running.set(element,control);
    void control.finished.then(()=>{
      if (running.get(element)===control) running.delete(element);
    }).catch(()=>{});
  };
  const reset = () => {
    for (const element of running.keys()) stop(element);
    values=new WeakMap();
    dragging=new WeakSet();
  };
  reduced.addEventListener('change',()=>{
    if (!reduced.matches) return;
    for (const element of running.keys()) fill(element,values.get(element)!.target,true);
  },{signal});
  signal.addEventListener('abort',reset,{once:true});
  return {fill,reset,
    startDrag(element:HTMLElement) {stop(element);dragging.add(element);},
    endDrag(element:HTMLElement,percent:number) {dragging.delete(element);fill(element,percent);},
  };
}
export type ProgressMotion = ReturnType<typeof createProgressMotion>;
