// Leave enough room for labels and touch targets instead of squeezing categories.
export function chartMinimumWidth(categories: number) {
  return Math.max(0, categories) * 44 + 32;
}

/** Start each dataset at its newest/rightmost categories; preserve user panning. */
export function mountChartViewport(scroller:HTMLElement, signal:AbortSignal) {
  let dataset:string|undefined;
  let atEnd=true;
  const align=()=>{
    if(!signal.aborted&&atEnd&&scroller.clientWidth>0)
      scroller.scrollLeft=Math.max(0,scroller.scrollWidth-scroller.clientWidth);
  };
  const observer=new ResizeObserver(align);
  observer.observe(scroller);
  scroller.addEventListener('scroll',()=>{
    atEnd=scroller.scrollWidth-scroller.clientWidth-scroller.scrollLeft<2;
  },{signal});
  signal.addEventListener('abort',()=>observer.disconnect(),{once:true});
  return {update(key:string){
    if(key!==dataset){dataset=key;atEnd=true;}
    align();
  }};
}
