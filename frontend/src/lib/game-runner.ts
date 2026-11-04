import {formatDateTime} from './date-time';
type Runner={enabled:boolean;available:boolean;state:string;heartbeat:string|null;current:string|null;current_name?:string|null;categories:Record<string,number>;[key:string]:unknown};
let dispose:(()=>void)|undefined;

export function installGameRunner(){
  dispose?.();dispose=undefined;
  const candidate=document.querySelector<HTMLElement>('#game-runner');
  if(!candidate)return;
  const root=candidate;
  const toggle=root.querySelector<HTMLButtonElement>('#runner-toggle')!;
  const error=root.querySelector<HTMLElement>('#runner-error')!;
  let timer:ReturnType<typeof setTimeout>|undefined;
  let controller:AbortController|undefined;
  let data:Runner|undefined;
  let stopped=false;
  let changing=false;
  const write=(selector:string,value:string)=>{const node=root.querySelector(selector);if(node)node.textContent=value;};
  function paint(value:Runner){
    data=value;
    write('#runner-state',({running:'Running',idle:'Waiting for idle capacity',stopped:'Stopped',disabled:'Disabled by server configuration'} as Record<string,string>)[value.state]||value.state);
    root.querySelectorAll<HTMLElement>('[data-value]').forEach(node=>{node.textContent=Number(value[node.dataset.value!]??0).toLocaleString();});
    root.querySelectorAll<HTMLElement>('[data-category]').forEach(node=>{node.textContent=Number(value.categories[node.dataset.category!]??0).toLocaleString();});
    write('#runner-current',value.current_name||value.current||'—');
    write('#runner-heartbeat',value.heartbeat?formatDateTime(value.heartbeat,'time'): '—');
    toggle.textContent=value.enabled?'Stop':'Start';
    toggle.disabled=!value.available||changing;
  }
  function schedule(){clearTimeout(timer);if(!stopped&&!document.hidden)timer=setTimeout(()=>void refresh(),5000);}
  async function refresh(enabled?:boolean){
    if(stopped||document.hidden&&enabled===undefined)return;
    controller?.abort();const request=new AbortController();controller=request;
    try{
      const response=await fetch('/api/proxy/admin/game-runner',{method:enabled===undefined?'GET':'PATCH',cache:'no-store',signal:request.signal,
        ...(enabled===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})})});
      if(!response.ok)throw new Error('Could not update the background mapper.');
      const value=await response.json() as Runner;
      if(stopped||request.signal.aborted)return;
      paint(value);error.hidden=true;
    }catch(cause){if(!request.signal.aborted&&!stopped){error.textContent=cause instanceof Error?cause.message:'Mapper unavailable.';error.hidden=false;}}
    finally{if(controller===request){controller=undefined;schedule();}}
  }
  const click=async()=>{if(!data||changing)return;changing=true;toggle.disabled=true;await refresh(!data.enabled);changing=false;if(data)paint(data);};
  const visibility=()=>{clearTimeout(timer);if(document.hidden)controller?.abort();else if(!changing)void refresh();};
  toggle.addEventListener('click',click);document.addEventListener('visibilitychange',visibility);
  dispose=()=>{stopped=true;clearTimeout(timer);controller?.abort();toggle.removeEventListener('click',click);document.removeEventListener('visibilitychange',visibility);};
  document.addEventListener('astro:before-swap',()=>dispose?.(),{once:true});
  void refresh();
}
