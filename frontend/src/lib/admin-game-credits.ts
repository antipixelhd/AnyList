import {contributorSourceRefresh} from './contributor-source-refresh';
let installed=false,mounted:Element|null=null,listeners:AbortController|undefined;
function mount(){
  const root=document.querySelector('[data-game-credits-admin]');
  if(root===mounted&&listeners&&!listeners.signal.aborted)return;
  listeners?.abort();mounted=root;
  if(!root)return;
  listeners=new AbortController();const {signal}=listeners;
  const input=root.querySelector<HTMLInputElement>('input[type="search"]');
  const clear=root.querySelector<HTMLButtonElement>('[data-browse-search-clear]');
  input?.addEventListener('input',()=>{if(clear)clear.hidden=!input.value;},{signal});
  clear?.addEventListener('click',()=>{if(input){input.value='';input.focus();clear.hidden=true;}},{signal});
  root.querySelectorAll('form').forEach(form=>form.addEventListener('submit',()=>{
    const button=form.querySelector<HTMLButtonElement>('button[type="submit"]');
    if(button)button.disabled=true;
  },{signal}));
  const status=root.querySelector('[data-game-review-status]');
  const refresh=root.querySelector<HTMLButtonElement>('[data-game-review-refresh]');
  if(status&&refresh){
    const resource=contributorSourceRefresh(signal,ready=>{refresh.hidden=!ready;});
    resource.update(JSON.parse(status.textContent||'null'));
    refresh.addEventListener('click',()=>{refresh.disabled=true;location.reload();},{signal});
  }
}
export function initializeGameCreditsAdmin(){
  if(!installed){
    installed=true;document.addEventListener('astro:page-load',mount);
    document.addEventListener('astro:before-swap',()=>{listeners?.abort();mounted=null;});
  }
  mount();
}
