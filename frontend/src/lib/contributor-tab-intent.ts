import {predictionAllowed} from './predictive-loading.ts';

/** Warm only a domain the viewer is about to select. Touch demand fetches directly. */
export function bindContributorTabIntent(form:HTMLElement,signal:AbortSignal,warm:(domain:string)=>void,allowed=predictionAllowed) {
  let timer:ReturnType<typeof setTimeout>|undefined,target:HTMLInputElement|undefined;
  const clear=()=>{clearTimeout(timer);timer=undefined;target=undefined;};
  const schedule=(event:Event)=>{
    if(event instanceof PointerEvent&&event.pointerType!=='mouse')return;
    const node=event.target as Element;
    const input=node.closest<HTMLLabelElement>('label')?.querySelector<HTMLInputElement>('input[name="domain"]');
    if(!input||input.disabled||input.checked){clear();return;}
    if(target===input)return;
    clear();target=input;
    timer=setTimeout(()=>{if(!signal.aborted&&input.isConnected&&!input.disabled&&!input.checked&&allowed())warm(input.value);},180);
  };
  form.addEventListener('pointerover',schedule,{signal,passive:true});
  form.addEventListener('focusin',schedule,{signal});
  form.addEventListener('pointerout',event=>{if(target&&!target.closest('label')?.contains((event as PointerEvent).relatedTarget as Node|null))clear();},{signal});
  form.addEventListener('focusout',clear,{signal});
  signal.addEventListener('abort',clear,{once:true});
}
