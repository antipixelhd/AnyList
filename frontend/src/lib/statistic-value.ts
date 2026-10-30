const active=new WeakMap<HTMLElement,()=>void>();
/** Update persistent headline elements; cancellation preserves the latest rendered value. */
export function statisticValue(node:HTMLElement,text:string,signal:AbortSignal,animate=true){
  active.get(node)?.();
  if(node.textContent===text||!animate||signal.aborted||matchMedia('(prefers-reduced-motion: reduce)').matches){node.textContent=text;return;}
  const parts=new Intl.NumberFormat('en').formatToParts(12345.6),group=parts.find(p=>p.type==='group')?.value||',',decimal=parts.find(p=>p.type==='decimal')?.value||'.';
  const number=(value:string)=>Number(value.replaceAll(group,'').replace(decimal,'.').replace(/[^0-9.\-]/g,''));
  const old=node.textContent||'',from=number(old),to=number(text);
  if(!/[0-9]/.test(old)||!/[0-9]/.test(text)||!Number.isFinite(from)||!Number.isFinite(to)){node.textContent=text;return;}
  const fraction=(text.split(decimal)[1]||'').replace(/[^0-9]/g,'').length,format=new Intl.NumberFormat('en',{minimumFractionDigits:fraction,maximumFractionDigits:fraction});
  const prefix=text.startsWith('≥')?'≥ ':'',suffix=text.endsWith('%')?'%':'';let frame=0;const start=performance.now();
  const cancel=()=>{cancelAnimationFrame(frame);signal.removeEventListener('abort',cancel);active.delete(node);};
  const step=(now:number)=>{if(signal.aborted){cancel();return;}const progress=Math.min(1,(now-start)/600),eased=1-(1-progress)**3;node.textContent=prefix+format.format(from+(to-from)*eased)+suffix;if(progress<1)frame=requestAnimationFrame(step);else{node.textContent=text;cancel();}};
  active.set(node,cancel);signal.addEventListener('abort',cancel,{once:true});frame=requestAnimationFrame(step);
}
